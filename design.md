# Revenue Reconciliation — Design

## 1. Goal

A store keeps orders in one system and payments in its payment processor. The two should agree, and often don't. This application lets a user upload both exports, reconciles them, and shows on one dashboard:

- **how bad it is:** how much value reconciles, how much is in dispute, and how much money is at risk;
- **what kind of problems exist:** discrepancies grouped by type;
- **what to look at first:** a prioritised, searchable list that drills down to the source rows.

Principles:
- Reconciliation is **deterministic**: the same files and rules version always give the same result, regardless of row order.
- Every result carries **evidence** (source rows, compared values, rule applied), so anyone can verify it without AI.
- An LLM **explains** results in plain language. It never decides whether records match.
- Money uses `Decimal` / `NUMERIC(14,2)`, never floats. Amounts in different currencies are never added together.
- Each user's data is private to that user.

## 2. Architecture

```
Browser ──► Next.js (Vercel)
              │  /api/*  rewrite (same origin)
              ▼
           FastAPI (Railway)
              ├─ auth        signup / login / logout / me
              ├─ ingestion   CSV validation, parsing, normalisation   (pure)
              ├─ reconcile   deterministic engine                     (pure)
              ├─ dashboard   summary + filterable result queries
              └─ explain     Gemini adapter, schema validation, fallback
              ▼
           PostgreSQL (Neon)
```

- **Modular monolith.** Parsing and reconciliation are pure functions with no database or HTTP dependency, so they are unit-tested directly:
  - `(file bytes) → rows + validation report`
  - `(orders, payments, rules) → results`
- **Same-origin proxy.** Next.js forwards `/api/*` to FastAPI, so the browser only ever talks to one origin. The session can then live in an httpOnly cookie, and no CORS setup is needed.
- **Request flow.** Upload both files → validate and reconcile in memory → write everything in one transaction → the dashboard reads the stored results.
- **Synchronous processing.** A few thousand rows reconcile in milliseconds, so there is no job queue. Uploads are capped at 2 MB per file.

## 3. Key decisions

| Area | Decision | Why |
|---|---|---|
| Frontend | Next.js, TypeScript, Tailwind, shadcn/ui, Recharts, TanStack Table | Accessible components; chart and table state handled by mature libraries |
| Backend | FastAPI, Pydantic, SQLAlchemy 2.x, Alembic | Typed contracts, Decimal support, reviewable migrations |
| Database | PostgreSQL (Neon) | Relational integrity, exact `NUMERIC` money, managed hosting |
| Hosting | Vercel (web), Railway (API), Neon (DB) | The API host does not sleep, so there are no cold starts |
| Auth | Own email + password: bcrypt + JWT in an httpOnly cookie | Small and auditable; no third-party signup step |
| Isolation | Each import belongs to one user; all data is reached through an owned import | One enforcement point that is easy to test |
| File storage | Original CSV text + sha256 stored in Postgres | Same transaction as the parsed rows: an import is fully saved or not at all. Object storage is the path for large files |
| Processing | Synchronous | Fast enough at this size; avoids queue infrastructure |
| Matching key | `order_id` = `order_reference` after trim + uppercase | The only shared identifier between the two files |
| Comparison | Order `net_amount` vs settled charge `amount` | What the customer owes vs what was charged; fees are processor cost |
| Tolerance | 0.05 in the transaction currency, absolute per order | Absorbs rounding noise without hiding real errors |
| LLM | Gemini, backend only, JSON schema, default temperature, deterministic fallback | Explains results; never decides them (section 10) |

## 4. Data model

| Table | Purpose | Main columns |
|---|---|---|
| `users` | Accounts | id (uuid), email (unique, lower-case), password_hash, created_at |
| `imports` | One upload of an orders file + payments file, and its result | id, user_id, orders/payments filename, orders/payments sha256, orders/payments raw text, rules_version, summary (jsonb: metrics + validation report), created_at |
| `orders` | Accepted, de-duplicated order rows | id, import_id, row_numbers (int[]), order_id_raw, order_key, order_date, customer_email, currency, gross_amount, discount, net_amount, status, raw (jsonb), dq_flags (text[]) |
| `payments` | Accepted, de-duplicated payment events | id, import_id, row_numbers, transaction_ref, order_reference_raw, order_key (null when blank), processed_at, currency, amount, fee, net_settled, type, status, raw (jsonb), dq_flags |
| `reconciliation_items` | One result per order, per orphan key + currency, or per blank-reference payment. **Matched results are stored too**, so every headline number drills down to its rows | id, import_id, item_key, order_key, category, severity, severity_rank, currency, expected_amount, actual_amount, difference, reconciled_value, disputed_value, amount_at_risk, risk_direction, reason, dq_flags, evidence (jsonb) |
| `explanations` | Cached LLM output | id, import_id, item_id (null = whole-import summary), prompt_version, model, output (jsonb), created_at |

**Constraints**

| Constraint | Purpose |
|---|---|
| `imports` unique `(user_id, orders_sha256, payments_sha256, rules_version)` | Re-uploading the same files returns the existing import |
| `orders` unique `(import_id, order_key)`; `payments` unique `(import_id, transaction_ref)`; `reconciliation_items` unique `(import_id, item_key)` | One row per business key in each import |
| `explanations` composite FK `(item_id, import_id)` → `reconciliation_items (id, import_id)` | An explanation can only reference an item from its own import. A NULL `item_id` (summary) skips the check |
| `explanations` `UNIQUE NULLS NOT DISTINCT (import_id, item_id, prompt_version, model)` | One cached explanation per item, prompt version and model. Plain `UNIQUE` would treat NULL summary rows as distinct |
| `CHECK`: money ≥ 0 (except the signed `difference`); 3-letter currency; status, type, category, severity and direction within allowed sets; `email = lower(email)` | Invalid states cannot be stored |
| `CHECK (reconciled_value = 0 OR disputed_value = 0)` | An item counts in exactly one bucket |
| `CHECK (currency IS NOT NULL OR category = 'NEEDS_REVIEW')` | Currency may be unknown only when the evidence is incomplete |
| Child FKs `ON DELETE CASCADE`; `imports.user_id` `ON DELETE RESTRICT` | Deleting an import removes everything that belongs to it |

**Indexes:**
- `imports (user_id, created_at DESC)` for import history;
- `orders (import_id, order_key)` and `payments (import_id, order_key)` for grouping;
- `reconciliation_items (import_id, severity_rank, amount_at_risk DESC)` for the default priority order;
- `reconciliation_items (import_id, category)` for filters and chart drill-down.

Child tables have no `user_id`. Ownership lives only on `imports`, so it cannot drift out of sync. Enum-like columns are text with `CHECK`, so adding a value is a simple migration. List queries never select the raw file columns.

## 5. Ingestion

**Files.** A file is rejected with `422`, and nothing is saved, if:
- it is not UTF-8 (a BOM is tolerated) or not comma-delimited;
- it is larger than 2 MB;
- it is empty;
- it is missing required headers. This also catches orders and payments swapped between the two upload slots.

Header matching ignores case and surrounding spaces. Extra columns are kept in `raw`. A file with headers but no rows is valid and shows a "no records" state.

**Fields.** A row missing a required field, or with an invalid value, is **rejected** and listed in the validation report with its row number and reason. It is never coerced, for example to 0. A missing optional field becomes a data-quality flag.

| | Required | Optional |
|---|---|---|
| Orders | order_id, currency, net_amount, status | order_date, customer_email, gross_amount, discount |
| Payments | transaction_ref, currency, amount, type, status | processed_at, order_reference, fee, net_settled |

| Field | Rule |
|---|---|
| Identifiers | Raw value kept; match key = `strip().upper()`. Flagged `reference_normalised` when the key differs from the raw value. A blank order reference makes the payment an orphan keyed by its `transaction_ref` |
| Dates | Orders `YYYY-MM-DD HH:MM:SS`. Payments `DD/MM/YYYY HH:MM`; this format is fixed for the column because some values have day > 12. Both files are timezone-naive and treated as the same zone. A blank `processed_at` is flagged and skips timing checks |
| Amounts | `Decimal`, at most 2 decimal places, ≥ 0 |
| Discount | Blank → 0 only when gross = net; otherwise flagged |
| Consistency | `gross − discount = net` and `amount − fee = net_settled`; a mismatch is flagged, not rejected |
| Currency | Allow-list of ISO codes with 2 minor units (USD, EUR, GBP, INR, AUD, CAD, SGD) |
| Status / type | Orders `completed / cancelled / refunded`; payments `charge / refund` × `settled / pending / failed` |

**Duplicates** are compared on parsed values, so `0` and `0.0` are equal.
- **Exact duplicate** of an order, or of a payment with the same `transaction_ref`: collapsed into one record whose `row_numbers` lists every source row, and flagged `duplicate_row`.
- **Same id, different content:** an `id_conflict`. None of the conflicting rows are inserted. They are listed in the validation report, and the key becomes `NEEDS_REVIEW`.

**Rejected rows and results.** A rejected row is linked to an order key only when all three hold:
1. the row has the expected number of columns;
2. its key field is non-blank after normalisation;
3. the rejection reason is in a different field.

Then that key becomes `NEEDS_REVIEW`. Otherwise the row stays only in the validation report, because guessing a key could attach evidence to the wrong order. This rule stops one malformed payment row from producing a false "missing payment".

## 6. Reconciliation

### 6.1 Matching

1. Index orders by `order_key`; group payment events by `order_key`.
2. Per order, using **settled** events in the **order's currency**:
   `collected = Σ settled charges − Σ settled refunds`.
3. Expected cash by order status: `completed` → `net_amount`; `cancelled` → 0; `refunded` → 0.
4. `difference = collected − expected`.
5. Payment keys with no order become orphan items, one per key and currency.

Pending and failed events move no money, so they are excluded from `collected` but shown as evidence. Some cases that are correct and need no action:
- a failed attempt followed by a successful charge (a normal retry);
- several charges summing to the expected amount (a split payment);
- a refund that brings a duplicate charge or an overcharge back to the expected amount.

**Refunds.** The payments export does not link a refund to the charge it reverses, so refunds are netted per order through `order_reference`. The orders export has no refund amount or "partially refunded" status. So `completed` with an unchanged `net_amount` asserts the full amount was kept.
- **Partial refund on a completed order** (`REFUND_NOT_IN_ORDERS`): the refund is settled in the processor, but the order record does not reflect it, so the order system overstates revenue by the shortfall. It may be legitimate (e.g. goodwill); the action is to confirm it and update the order.
- **Refunded order with a smaller refund** (`REFUND_INCOMPLETE`): the customer may still be owed the balance, or the status is wrong.
- **Refund without a matching charge** (refunds exceed charges for a key): `OVER_REFUNDED` on an order, or an under-collected orphan when there is no order.

For `OVER_REFUNDED`, two amounts are kept separate:

| Amount | Definition | Used for |
|---|---|---|
| `amount_at_risk` | `−difference`: total shortfall against the order record | The money-at-risk KPI. It uses the same definition as other categories and rises as the refund grows |
| `excess_refund` | Σ settled refunds − Σ settled charges | Stored in the evidence and shown in the drill-down only; never added to a KPI |

Example: a completed order of 100, charged 100 and refunded 120, has 120 at risk and an excess refund of 20.

### 6.2 Tolerances

Tolerances are code constants versioned by `RULES_VERSION` (not environment variables), so a given version always behaves the same.

| Rule | Value | Why |
|---|---|---|
| Amount | 0.05 in the transaction currency, absolute, same currency only | In the sample exports, rounding noise is ≤ 0.02 and the smallest real error is 18.50. A percentage tolerance would hide large errors on large orders. Within-tolerance items stay visible |
| Timing | A settled charge more than 7 days after the order, or more than 1 h before it | Normal charges land within 1–90 min. Card authorisations typically expire after about 7 days. Refunds are exempt |

### 6.3 Classification

First matching rule wins. `tol` is 0.05 in the item's currency.

| # | Category | Condition | Severity | Amount at risk | Direction |
|---|---|---|---|---|---|
| 0 | `NEEDS_REVIEW` | Id conflict, or a linked rejected row | High | 0 (unknown) | Unverified |
| 1 | `CURRENCY_MISMATCH` | A settled or pending event's currency ≠ order currency | High | Order net | Unverified |
| 2 | `MISSING_PAYMENT` | expected > 0, no payment events | High | expected | Under-collected |
| 3 | `PAYMENT_FAILED` | expected > 0, no settled charge, failed charge(s) only | High | expected | Under-collected |
| 4 | `PAYMENT_PENDING` | No settled charge, a pending charge exists | Low | Pending amount | Under-collected; over-collected if the order is cancelled/refunded |
| 5 | `CANCELLED_BUT_CHARGED` | Cancelled, collected > tol | High | collected | Over-collected |
| 6 | `REFUND_INCOMPLETE` | Refunded, collected > tol | High | collected | Over-collected |
| 7 | `OVER_REFUNDED` | collected < −tol | High | −difference | Under-collected |
| 8 | `REFUND_NOT_IN_ORDERS` | Completed, settled refund(s), difference < −tol | Medium | −difference | Under-collected |
| 9 | `DUPLICATE_CHARGE` | ≥ 2 settled charges each ≈ expected, difference > tol | High | difference | Over-collected |
| 10 | `OVERCHARGED` | difference > tol | High | difference | Over-collected |
| 11 | `UNDERCHARGED` | difference < −tol | Medium | −difference | Under-collected |
| 12 | `WITHIN_TOLERANCE` | 0 < abs(difference) ≤ tol | Info | 0 | — |
| 13 | `LATE_CHARGE` | Amount agrees, timing rule breached | Low | 0 | — |
| 14 | `MATCHED` | Everything agrees | — | 0 | — |
| — | `ORPHAN_PAYMENT` | Payment key with no order | Medium | abs(net settled) | Over-collected if net > 0; under-collected if net < 0 |

**Why this order:**
- Incomplete evidence comes first, so no other conclusion is drawn from it.
- A currency mismatch makes amount comparisons meaningless, so it comes before them.
- Missing/failed rules apply only when money is expected, so a cancelled order with a failed attempt is correctly `MATCHED`.
- Status rules come before the generic amount rules, so a cancelled-but-charged order is not called "overcharged".

Signals that don't change the category are attached as `dq_flags`: a timing breach when another category won, a pending charge next to settled ones, a fee mismatch.

**Severity** follows the order of action: customer harm first (wrong charges, refunds owed), then revenue leakage, then hygiene. The default sort is severity, then amount at risk descending, then order key.

**Determinism.** No randomness, clock reads, LLM calls, or dependence on row order happen inside the engine. Output is sorted by a stable key.

## 7. Metrics

All metrics are grouped by currency and never summed across currencies. They are plain sums over `reconciliation_items`, so clicking a metric filters the table to exactly the rows behind it.

| Metric | Definition |
|---|---|
| Total orders | Unique order keys (raw row count shown alongside) |
| Total payments | Payment events by type and status, and settled charge value |
| Value reconciled | Σ `reconciled_value` |
| Value in dispute | Σ `disputed_value`: how much business is affected |
| Money at risk | Σ `amount_at_risk` by direction: **under-collected** (store is short; the pending part is labelled), **over-collected** (owed to customers or unexplained cash), **unverified** (currency mismatch). `NEEDS_REVIEW` items are shown as a count, because their exposure is unknown |

| Category | `reconciled_value` | `disputed_value` |
|---|---|---|
| `MATCHED`, `WITHIN_TOLERANCE`, `LATE_CHARGE` | expected | 0 |
| `NEEDS_REVIEW` | 0 | Order net if an order exists. Otherwise the settled event amounts if they are all in one currency. Otherwise 0, with currency unknown |
| `CURRENCY_MISMATCH` | 0 | Order net in the order's currency; the foreign-currency payment appears in the evidence only |
| `ORPHAN_PAYMENT` | 0 | Σ settled charge + refund amounts for that key and currency |
| Other order categories | 0 | Order net |

**No double counting:**
- each accepted order and each accepted payment belongs to exactly one item;
- each item has one category and one direction;
- the database ensures an item counts as either reconciled or disputed, never both.

Example: an order of 92.81 charged 117.81 has 92.81 in dispute and 25.00 at risk.

## 8. Idempotency and failure handling

A repeated request never repeats its side effects, and a failure never leaves half-written data.

**Import write path:**
1. Read both files, hash them, then parse, validate and reconcile in memory.
2. `BEGIN`, then `INSERT INTO imports … ON CONFLICT DO NOTHING RETURNING id`.
3. If no row is returned, the same pair already exists: roll back and return it (`200`, `already_imported: true`).
4. Otherwise insert the orders, payments and items, then `COMMIT` (`201`).

If two identical uploads arrive together, the second insert waits on the unique index. It then either finds the committed import or proceeds if the first rolled back. There is no check-then-insert gap.

| Operation | Repeated or failing request |
|---|---|
| Sign up | Same email → `409`; the unique index and a caught `IntegrityError` close the race |
| Upload | Same file pair → the existing import is returned; no new rows |
| Reconcile | Same input → identical result (pure engine, versioned rules) |
| Import failure | Rolled back as one transaction; nothing persists |
| Explain | Repeated or concurrent clicks → one cached row (`ON CONFLICT DO NOTHING`, then read back) |
| LLM failure | One retry, then a deterministic fallback, which is not cached so a later click can retry; reconciliation data is never touched |
| Double submit in UI | Buttons disable while a request is in flight |

Errors use one shape, `{ "error": { "code", "message", "details"? } }`, with no stack traces or secrets.

## 9. Authentication and security

- **Accounts:**
  - The email is trimmed, lower-cased and unique. The password must be 8–72 bytes: bcrypt reads only 72 bytes, so longer passwords are rejected rather than silently truncated.
  - Signup starts a session.
  - Login returns the same `401` for an unknown email and a wrong password. It verifies against a dummy hash for unknown emails, so timing reveals nothing.
- **Session:**
  - A JWT (`sub` = user id, 12 h expiry, signed with `JWT_SECRET`) in an `httpOnly; Secure; SameSite=Lax; Path=/` cookie.
  - `Secure` can be disabled only for local HTTP.
  - Logout clears the cookie. Trade-off: there is no server-side revocation, so a stolen token works until it expires.
- **Isolation:**
  - Every import-scoped request loads the import with `WHERE id = :id AND user_id = :me`. Child data is only queried through that import.
  - Another user's resources return `404`, so their existence is not revealed.
- **CSRF:**
  - `SameSite=Lax` stops browsers sending the cookie on cross-site POSTs.
  - Every `POST`, including login and signup, also checks `Origin` against `APP_ORIGINS`. If `Origin` is missing, `Referer` is checked instead. If both are missing, the request is rejected with `403`. Browsers always send `Origin` on POST, and non-browser clients don't carry the user's cookie anyway.
- **Rate limiting** (in memory, `429` with `Retry-After`):
  - Login: 5 failed attempts per client IP + email per 15 min. A success resets the counter. An attacker can block only their own IP, never the account.
  - Login + signup: 20 requests per client IP per minute.
  - Per email across all IPs, there is no hard block. After 10 failures in 15 min, each attempt is delayed by 1 s, doubling up to 5 s. The delay happens before the password check and applies to correct attempts too, so a real user is slowed, never locked out.
  - Counters key on the email text whether or not an account exists, so throttling does not reveal accounts.
  - Explain endpoints: 10 per user per minute, to cap LLM cost.
- **Client IP** comes from `X-Forwarded-For`, counting `TRUSTED_PROXY_HOPS` entries from the right. Entries further left can be forged and are ignored. This value must be verified on the deployed proxy chain.
- **Frontend guard:** middleware redirects to `/login` without a session. This is for UX only; the API enforces access.

## 10. LLM explanations

- **Endpoints:** explain one item, or summarise a whole import. Both run on the backend; the API key is never sent to the browser.
- **Input:**
  - A compact evidence object: category, rule, amounts, currencies, statuses, events, timestamps.
  - No customer email or other personal data.
  - The summary sends the metrics plus the top 20 items.
- **Prompt injection:** CSV values are untrusted. They are passed as a JSON data block, with instructions to treat them as data and explain only what the evidence supports. Output is rendered as plain text.
- **Output schema:**
  - For one item: `{ summary, likely_cause, recommended_actions[1..4], confidence }`.
  - For the summary: `{ headline, top_issues[], recommended_priorities[] }`.
  - Lengths are capped. Every response is validated with Pydantic.
- **Parameters:**
  - Temperature stays at the default 1.0. Google recommends this for the Gemini 3 family, because lower values can cause looping or degraded output.
  - Consistency comes from the schema, a low thinking level, capped output, and caching: each explanation is generated once and then stays stable.
  - If a non-reasoning model is configured instead, a low temperature (about 0.2) is the right setting.
- **Failure:** on a timeout, provider error, invalid JSON or schema violation, retry once (total under 25 s). If that also fails, return a template explanation built from the evidence, marked `source: "fallback"`.
- **Cache key:** import + item + prompt version + model, so changing the model or the prompt never serves stale text.

## 11. API

| Method | Path | Purpose | Responses |
|---|---|---|---|
| POST | `/api/auth/signup` | Create account and session | 201, 409, 422, 429 |
| POST | `/api/auth/login` | Start session | 200, 401, 429 |
| POST | `/api/auth/logout` | End session | 204 |
| GET | `/api/auth/me` | Current user | 200, 401 |
| POST | `/api/imports` | Upload `orders` + `payments` (multipart), validate, reconcile | 201, 200 (`already_imported`), 413, 422 |
| GET | `/api/imports` | Import history, newest first | 200 |
| GET | `/api/imports/{id}` | Metrics and validation report | 200, 404 |
| GET | `/api/imports/{id}/items` | Results. Filters: `category`, `severity`, `direction`, `currency`, `has_dq`; search `q` (order id, transaction ref, email); sort; pagination. Defaults to non-matched items by priority | 200, 404 |
| GET | `/api/imports/{id}/items/{item_id}` | Evidence plus any cached explanation | 200, 404 |
| POST | `/api/imports/{id}/items/{item_id}/explain` | Explain one result | 200, 404, 429 |
| POST | `/api/imports/{id}/summary/explain` | Summarise the import | 200, 404, 429 |
| GET | `/api/health` | Liveness | 200 |

Every route except signup, login and health requires a session (`401` otherwise).

## 12. UI

1. **Login / Sign up.**
2. **Imports:** drag-and-drop for both files, with type and size checks; the validation report with rejected rows and reasons; import history.
3. **Dashboard:**
   - **KPI row:** orders, payments, reconciled, in dispute, money at risk (under / over / unverified).
   - **Chart:** money at risk by category. Clicking a bar filters the table.
   - **"Look at these first" table:** severity, category, order, expected vs actual, at risk; with filters, search and sorting. Clicking a KPI filters it too.
   - **Row drawer:** evidence with source row numbers, data-quality notes, and **Explain with AI**, with loading, error and fallback states.
   - **Tabs:** Discrepancies · Data quality · AI summary.
   - **States:** no imports, uploading, file rejected, imported with warnings, nothing to reconcile, session expired.

## 13. Testing

- **Engine (golden):**
  - The category of every order in the sample exports is pinned.
  - Pinned totals:
    - under-collected 886.85 USD; over-collected 936.58 USD;
    - unverified 210.00 USD + 145.00 EUR;
    - reconciled 39,963.28 USD across 168 orders;
    - in dispute 2,469.37 USD + 145.00 EUR.
  - The same result is expected after shuffling input rows.
- **Engine (rules):**
  - Tolerance edges: 0.05 → within tolerance; 0.06 → mismatch.
  - Correct cases: a split payment, a duplicate charge later refunded, and an overcharge corrected by a refund all reconcile as `MATCHED`.
  - Cancelled orders: with only a failed attempt → `MATCHED`; with a pending charge → pending, over-collected.
  - Refunded order with no events → `MATCHED`.
  - A rejected row with a readable key → `NEEDS_REVIEW`; with a blank key → validation report only.
  - Refund-only orphan → under-collected.
  - Every order and payment belongs to exactly one item.
  - Refund boundaries (order net 100):

  | Status | Charge | Refund | Category | At risk | Excess refund |
  |---|---|---|---|---|---|
  | refunded | 100 | 80 | `REFUND_INCOMPLETE` | 20 | 0 |
  | refunded | 100 | 120 | `OVER_REFUNDED` | 20 | 20 |
  | completed | 100 | 20 | `REFUND_NOT_IN_ORDERS` | 20 | 0 |
  | completed | 100 | 100 | `REFUND_NOT_IN_ORDERS` | 100 | 0 |
  | completed | 100 | 120 | `OVER_REFUNDED` | 120 | 20 |
  | completed | — | 30 | `OVER_REFUNDED` | 130 | 30 |

- **Ingestion:**
  - date formats and blank timestamps;
  - blank discount;
  - normalisation;
  - duplicates vs conflicts;
  - missing headers, swapped files, empty and header-only files;
  - invalid amounts, currencies and statuses;
  - oversize files.
- **API and security:**
  - **Auth:** signup, login and logout; duplicate signup; passwords over 72 bytes; cookie flags; unauthenticated → `401`; another user's data → `404`.
  - **Origin:** a foreign `Origin` → `403`; `Referer` fallback; both missing → `403`.
  - **Rate limits:**
    - an IP + email limit hit → `429`, but a different IP is still allowed;
    - the per-email delay never refuses a correct password;
    - unknown and known emails behave the same;
    - a forged forwarded IP is ignored.
  - **Idempotency and failures:** repeat and concurrent uploads → one import; a mid-import failure → no rows; repeat explain → one cached row; a model change → a new explanation; a cross-import explanation is blocked by the FK; LLM timeout or malformed output → fallback.
- The sample CSVs are added under `backend/tests/fixtures/` with the engine tests.

## 14. Configuration

| Variable | Used by | Purpose |
|---|---|---|
| `DATABASE_URL` | API, migrations | Postgres connection string. Use Neon's direct (non-pooled) endpoint, because transaction-mode poolers break prepared statements |
| `JWT_SECRET` | API | ≥ 32 random bytes |
| `COOKIE_SECURE` | API | `true` in production, `false` only for local HTTP |
| `APP_ORIGINS` | API | Comma-separated origins allowed for `POST` |
| `TRUSTED_PROXY_HOPS` | API | Number of proxies in front of the API, used to read the client IP; `0` locally |
| `GEMINI_API_KEY` | API | LLM key |
| `GEMINI_MODEL` | API | Model id |
| `API_BASE_URL` | Web (server) | Target of the `/api/*` rewrite |

## 15. Future work

| Item | When it becomes worth it |
|---|---|
| Organisations, shared workspaces, roles | Teams need shared access: add `tenants` + `memberships` and scope imports by tenant |
| Object storage for files | Files grow beyond a few MB: S3 with private keys and signed URLs |
| Background processing | Imports grow beyond synchronous limits: a worker and job table that re-checks ownership |
| Refresh tokens, revocation, password reset | Longer sessions or account recovery are needed |
| Shared rate limits, step-up checks | More than one API instance (Redis counters), or sustained attacks (CAPTCHA after repeated failures) |
| Postgres row-level security | Defence in depth for isolation |
| FX conversion | A trusted rate source is available: rates table with source and timestamp |
