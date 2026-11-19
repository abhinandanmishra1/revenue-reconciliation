# Design — Revenue Reconciliation Dashboard

> Status: **agreed design** (v1.1). v1 replaced the earlier multi-tenant proposal. v1.1 fixes classification precedence edge cases, defines the ingestion contract and indexes, and corrects the LLM temperature decision after checking current provider guidance. Every cut is recorded in section 10 with the path to add it back.

## 1. Goals

- Let a signed-in user upload an orders export and a payment-processor export, reconcile them, and see where money is leaking.
- Reconciliation is **deterministic and repeatable**: the same input files and rules version always produce the same classifications and amounts, regardless of row order. Generated ids and timestamps are not part of the result.
- Every result carries **evidence** (source rows, compared values, rule applied) so a reviewer can reach the same conclusion without AI.
- An LLM **explains** results in plain language. It never decides whether records match.
- Money is handled with `Decimal` / `NUMERIC(14,2)`, never floats. Amounts in different currencies are never added together.

## 2. Architecture

```
Browser ──► Next.js (Vercel)
              │  /api/*  rewrite (same origin → httpOnly cookie, no CORS)
              ▼
           FastAPI (Railway)
              ├─ auth        : signup / login / logout / me (bcrypt + JWT cookie)
              ├─ ingestion   : CSV validation, parsing, normalisation (pure)
              ├─ reconcile   : deterministic engine (pure, no I/O)
              ├─ dashboard   : summary + filterable result queries
              └─ explain     : LLM adapter (Gemini) with schema validation + fallback
              ▼
           PostgreSQL (Neon)
```

- **Modular monolith.** Ingestion parsing and the reconciliation engine are pure functions, `(file bytes) -> parsed rows + validation report` and `(orders, payments, rules) -> results`. They have no database or HTTP dependency, so they are unit-tested directly.
- **Same-origin proxy.** Next.js rewrites `/api/*` to the FastAPI service. The browser only ever talks to one origin. This lets the session live in an `httpOnly; Secure; SameSite=Lax` cookie (not readable by JavaScript, so not stealable by XSS), and no CORS configuration is needed.
- **Synchronous processing.** Ingest + reconcile of a few thousand rows takes milliseconds. Uploads are capped at **2 MB per file**, which keeps the request well under the request-size limits of the Vercel proxy hop. A queue is noted as future work.

## 3. Stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js (App Router), TypeScript, Tailwind, shadcn/ui | Accessible components, fast to build a clean UI |
| Charts / table | Recharts, TanStack Table | Recharts underpins shadcn charts; TanStack handles table state |
| API | FastAPI, Pydantic | Typed request/response contracts, Decimal support |
| ORM / migrations | SQLAlchemy 2.x, Alembic | Explicit schema, reviewable migrations |
| Database | PostgreSQL on Neon | Real relational DB, NUMERIC money, free managed tier |
| Auth | Own: bcrypt password hashing + JWT (HS256) in httpOnly cookie | Small, auditable, no third-party signup friction |
| LLM | Google Gemini API (Flash-class model), called from the backend only | Brief allows any provider; JSON-schema structured output |
| Hosting | Vercel (web), Railway (API, no idle sleep), Neon (DB) | No cold start for reviewers |
| Tests | pytest (engine, ingestion, API, isolation) | |

## 4. Authentication and data isolation

- **Signup:** email (trimmed, lower-cased, unique) + password of 8–72 bytes. The upper bound exists because bcrypt only uses the first 72 bytes, so longer passwords are rejected rather than silently truncated. Hashed with bcrypt (per-hash salt, adaptive cost). A successful signup also starts a session.
- **Login:** the same `401 invalid credentials` response for an unknown email and a wrong password. For an unknown email the server still verifies against a fixed dummy hash, so response time does not reveal which emails exist.
- **Session:** JWT with `sub` = user id, `exp` = 12 h, signed with `JWT_SECRET` (≥ 32 random bytes) from the environment. Sent as an `httpOnly`, `Secure`, `SameSite=Lax`, `Path=/` cookie. `Secure` is configurable only so local HTTP development works. Logout clears the cookie. Accepted trade-off: there is no server-side revocation or refresh token, so a stolen token is valid until it expires (12 h max).
- **Authorisation:** every protected route depends on `current_user`. Isolation is enforced at a single point. Every import-scoped request first loads the import with `WHERE id = :id AND user_id = :me`; child rows (orders, payments, results, explanations) are then only ever queried through that owned `import_id`. A resource owned by someone else returns **404, not 403**, so its existence isn't leaked.
- **CSRF:** the real protection is `SameSite=Lax`. Browsers do not attach the cookie to cross-site POSTs. As defence in depth, state-changing requests (`POST`) must carry an `Origin` header matching the configured app origin; otherwise they get `403`. Content-type checks are *not* relied on, because HTML forms can send multipart cross-site.
- **Frontend guard:** Next.js middleware redirects to `/login` when the cookie is absent. This is UX only; the API is the enforcement point.
- **Tests** prove that user B cannot list, read, or explain user A's imports or results.

## 5. Data model

| Table | Purpose | Key columns and constraints |
|---|---|---|
| `users` | Accounts | id (uuid PK), email (**unique**, stored lower-case), password_hash, created_at |
| `imports` | One upload of an orders file + payments file and its reconciliation | id (uuid PK), user_id (FK users, not null), orders_filename, payments_filename, orders_sha256, payments_sha256, orders_raw (text), payments_raw (text), rules_version, summary (jsonb: metrics + validation report), created_at. **Unique (user_id, orders_sha256, payments_sha256, rules_version)** |
| `orders` | Parsed, de-duplicated order rows | id, import_id (FK imports, cascade), row_numbers (int[]: all source rows collapsed into this one), order_id_raw, order_key, order_date, customer_email, currency, gross_amount, discount, net_amount, status, raw (jsonb), dq_flags (text[]) |
| `payments` | Parsed payment events | id, import_id (FK, cascade), row_numbers, transaction_ref, order_reference_raw, order_key, processed_at, currency, amount, fee, net_settled, type, status, raw (jsonb), dq_flags |
| `reconciliation_items` | One result per order key, plus one per orphan key. **Includes matched items**, so every headline number drills down to its rows | id, import_id (FK, cascade), order_key, category, severity, severity_rank (int), currency, expected_amount, actual_amount, difference, amount_at_risk, risk_direction, reason, dq_flags (text[]), evidence (jsonb: order rows, payment rows, rule ids, compared values) |
| `explanations` | LLM output cache | id, import_id (FK, cascade), item_id (FK reconciliation_items, **nullable**: null = whole-run summary), prompt_version, model, output (jsonb), created_at. **`UNIQUE NULLS NOT DISTINCT (import_id, item_id, prompt_version)`** so the run-summary row is also unique; without `NULLS NOT DISTINCT`, Postgres treats NULLs as distinct and would allow duplicate summaries |

**Indexes** (beyond PKs and the unique constraints above): `imports (user_id, created_at DESC)` for history; `orders (import_id, order_key)`; `payments (import_id, order_key)`; `reconciliation_items (import_id, severity_rank, amount_at_risk DESC)` for the default "look at these first" ordering; `reconciliation_items (import_id, category)` for filters and chart drill-down.

Notes:
- Child tables carry no `user_id`. Ownership lives in one place (`imports.user_id`), so there is no second copy that could drift out of sync. It is enforced by always going through an owned import.
- Money columns are `NUMERIC(14,2)`, parsed with `Decimal` from the original text. Only currencies with 2 minor units are accepted (see section 6).
- The original CSV text and its sha256 are stored on the import, so raw values stay available for audit. History/list queries never select the raw columns. Files go in Postgres rather than object storage on purpose. File + rows + results are written in **one transaction**, so an import is either fully saved or not saved at all. With a separate object store, a failure between the two writes leaves orphaned files or rows pointing at missing files. Object storage is the scale path (section 10).

## 5a. Idempotency and failure handling

A repeated request must never repeat its side effects. A failure must never leave half-written state.

**Import write path, in this order:**
1. Read both files (size-capped), compute sha256, and parse + validate + reconcile **in memory**. These are pure functions, and nothing is written yet. A file-level error returns `422` here.
2. `BEGIN`
3. `INSERT INTO imports … ON CONFLICT (user_id, orders_sha256, payments_sha256, rules_version) DO NOTHING RETURNING id`.
4. If nothing is returned, the pair was already imported. `ROLLBACK`, read the existing import, and return `200 { already_imported: true }`.
5. Otherwise bulk-insert orders, payments, and reconciliation items, set the summary, `COMMIT`, and return `201`.

Concurrency: if two identical uploads race, the second `INSERT` blocks on the unique index until the first transaction finishes. If the first commits, the second does nothing and returns the existing import. If the first rolls back, the second proceeds. Either way, exactly one import exists. There is no check-then-insert window.

| Operation | Duplicate / retry behaviour | How it is enforced |
|---|---|---|
| Sign up | Same email twice → second gets `409`, no second account | Unique email; the insert catches `IntegrityError` (no check-then-insert race) |
| Upload + reconcile | Same file pair (same bytes, any filenames) → existing import returned; no new rows, no new run | Unique key + `ON CONFLICT DO NOTHING` (above) |
| Reconciliation | Re-running on the same inputs gives identical classifications and amounts | Pure deterministic engine; tolerances are code constants tied to `RULES_VERSION`, **not** environment variables, so the same version can never behave differently between deployments |
| Partial failure during import | Parse error, DB error, or crash mid-import → nothing persists | Single transaction around import + rows + results + summary |
| LLM explain | Repeated or concurrent clicks for the same item → one stored explanation, returned from cache | `UNIQUE NULLS NOT DISTINCT (import_id, item_id, prompt_version)`; insert `ON CONFLICT DO NOTHING`, then read back |
| LLM failure | Timeout, provider error, or invalid output → one retry, then a deterministic fallback | The fallback is returned but **not cached**, so a later click can retry. Reconciliation data is never touched by this path |
| Frontend double submit | Upload / explain buttons disable while a request is in flight | Client guard; the server-side rules above are the real protection |

Failure-handling rules:
- Validation errors are reported with row numbers. Invalid financial values are never coerced to 0.
- Every external call (LLM) has a timeout, and the total time budget (attempt + retry) stays under 25 s so the proxy hop never times out first. Its failure degrades only the explanation, never the core result.
- Errors return a consistent JSON shape `{ "error": { "code", "message", "details"? } }` without stack traces or secrets.

## 6. Ingestion contract

**File level:** UTF-8 (BOM tolerated), ≤ 2 MB, comma-delimited, and the header row must contain the required columns (matched case- and whitespace-insensitively; extra columns are ignored and kept in `raw`). These are rejected with `422` and nothing is saved:
- an empty file
- missing required headers (this also catches orders and payments swapped between the two upload slots)
- unreadable encoding
- an oversize file

A file with headers but zero data rows is valid and produces an explicit "no eligible records" result.

**Required vs optional fields.** A row missing a *required* field is **rejected**. Rejected rows are listed in the validation report with row number and reason, and are never coerced. A missing *optional* field becomes a data-quality flag.

| File | Required | Optional |
|---|---|---|
| Orders | order_id, currency, net_amount, status | order_date, customer_email, gross_amount, discount |
| Payments | transaction_ref, currency, amount, type, status | processed_at, order_reference, fee, net_settled |

| Field | Rule |
|---|---|
| Order id / order reference | Raw value kept. Match key = `strip().upper()`. When the key differs from the raw value, the row gets flag `reference_normalised` (`ord-1802`, `" ord-1801 "`). A blank order reference becomes an orphan payment keyed by its transaction_ref |
| Order date | ISO `YYYY-MM-DD HH:MM:SS` |
| Payment `processed_at` | `DD/MM/YYYY HH:MM`. The column format is fixed because some rows have day > 12, and order-to-charge lags of 1–90 minutes confirm it. Blank → null + flag `missing_timestamp`; timing checks are skipped for that row |
| Amounts | `Decimal`, at most 2 dp, ≥ 0. Anything else is rejected |
| Discount | Blank → 0 **only if** gross = net, else flag `amount_inconsistent` |
| Consistency | `gross − discount = net` and `amount − fee = net_settled`. A mismatch is flagged, not rejected. Matching uses `net_amount` and `amount` |
| Currency | One of an allow-list of 2-minor-unit ISO codes (e.g. USD, EUR, GBP, INR, AUD, CAD, SGD); others are rejected |
| Status/type | Orders `completed / cancelled / refunded`; payments type `charge / refund`, status `settled / pending / failed`. Unknown values are rejected |
| Timezone | Both files are timezone-naive and treated as the same zone; the observed lags support this |

**Duplicates.** Compared on *parsed* values, so `0` and `0.0` are equal.
- An exact duplicate order row, or a payment row with the same `transaction_ref`, is collapsed to one record whose `row_numbers` lists every source row. It gets flag `duplicate_row` (ORD-1004).
- The same `order_id`, or the same `transaction_ref`, with **different** content is an `id_conflict`. The key is not matched; see `NEEDS_REVIEW` below.

## 7. Reconciliation engine

### 7.1 Matching

1. Build the order index by `order_key` (after de-duplication).
2. Group payment events by `order_key`.
3. Per order, from **settled** events in the **order's currency**: `collected = Σ settled charges − Σ settled refunds`.
4. Expected net cash by order status: `completed` → `net_amount`; `cancelled` → 0; `refunded` → 0 (a charge followed by a full refund).
5. `difference = collected − expected`.
6. Payment groups whose key has no order become **one orphan item per unknown order key**, with all of that key's events as evidence.

Pending and failed events move no money and do not count towards `collected`. A failed attempt followed by a successful charge is normal retry behaviour, not a discrepancy. **Split payments** (several settled charges of different amounts summing to the expected amount) are `MATCHED`. Fees are processor cost: they are validated (2.9% + $0.30 pattern, flag only) but are not part of what the customer owes, so the comparison is order `net_amount` against charge `amount`.

### 7.2 Tolerances (code constants, versioned by `RULES_VERSION`)

| Rule | Value | Reasoning |
|---|---|---|
| Amount tolerance | **$0.05 absolute per order**, same currency only, applied to every amount comparison below | Observed noise is ≤ $0.02 (rounding); the smallest real error is $18.50. A percentage tolerance would hide large errors on large orders. Items within tolerance are reported as `WITHIN_TOLERANCE`: counted as reconciled but still visible. |
| Late charge | A settled charge processed **> 7 days** after the order, or **> 1 h before** it | All normal charges land 1–90 min after the order. Card authorisations typically expire after about 7 days, so a capture weeks later suggests a re-charge or wrong linkage. Refunds are exempt. |

### 7.3 Classification (first matching rule wins; `tol` = 0.05)

| # | Category | Condition | Severity | Amount at risk | Direction |
|---|---|---|---|---|---|
| 0 | `NEEDS_REVIEW` | The key has an `id_conflict`, or a **rejected** row references it | High | 0 (unknown) | Unverified |
| 1 | `CURRENCY_MISMATCH` | Any settled or pending event currency ≠ order currency | High | Order net | Unverified |
| 2 | `MISSING_PAYMENT` | expected > 0 and no payment events at all | High | expected | Under-collected |
| 3 | `PAYMENT_FAILED` | expected > 0, no settled charge, failed charge(s) only | High | expected | Under-collected |
| 4 | `PAYMENT_PENDING` | No settled charge and a pending charge exists | Low | Pending amount | Under-collected if expected > 0; over-collected if the order is cancelled/refunded (the charge should be voided) |
| 5 | `CANCELLED_BUT_CHARGED` | Cancelled, `collected > tol` | High | collected | Over-collected |
| 6 | `REFUND_INCOMPLETE` | Refunded, `collected > tol` | High | collected | Over-collected |
| 7 | `OVER_REFUNDED` | `collected < −tol` (refunds exceed charges) | High | −collected | Under-collected |
| 8 | `REFUND_NOT_RECORDED` | Completed, settled refund(s) exist, **and** `difference < −tol` | Medium | −difference | Under-collected (revenue overstated) |
| 9 | `DUPLICATE_CHARGE` | ≥ 2 settled charges each within tol of expected, and `difference > tol` | High | difference | Over-collected |
| 10 | `OVERCHARGED` | `difference > tol` | High | difference | Over-collected |
| 11 | `UNDERCHARGED` | `difference < −tol` | Medium | −difference | Under-collected |
| 12 | `WITHIN_TOLERANCE` | `0 < |difference| ≤ tol` | Info | 0 | — |
| 13 | `LATE_CHARGE` | Amount agrees, timing rule breached | Low | 0 | — |
| 14 | `MATCHED` | Everything agrees | — | 0 | — |
| — | `ORPHAN_PAYMENT` | Payment key has no order | Medium | Σ settled charges − Σ settled refunds of the key | Over-collected (unexplained cash) |

Why this order:
- `NEEDS_REVIEW` is first. If a row about this key was rejected or conflicts, any conclusion would be built on incomplete evidence. Without this rule, a malformed payment row would surface as a false `MISSING_PAYMENT`.
- A currency mismatch makes every amount comparison meaningless, so it comes before them.
- Rules 2–3 apply only when money is expected, so a cancelled order with a failed attempt is correctly `MATCHED`.
- Status-driven rules (5–7) run before the generic amount comparison, so a cancelled-but-charged order is not mislabelled "overcharged".
- Rule 8 requires the order to actually be short. A refund that *remediates* a duplicate or an overcharge (net collected = expected) is therefore `MATCHED`, not a false "refund not recorded".
- A cancelled or refunded order with net collected within tolerance of 0 is correct and is `MATCHED`.

Secondary signals that don't change the category are attached as `dq_flags` and shown in the drawer: a timing breach when another category won, a pending charge alongside settled ones, a fee mismatch.

Severity reflects the order of action: **customer harm first** (they were charged wrongly, so there is chargeback and trust risk), then **revenue leakage**, then hygiene. Default ordering: `severity_rank`, then `amount_at_risk` descending, then `order_key`. Amounts in different currencies are compared only for ordering, never summed.

### 7.4 Data-quality notes (not discrepancies)

Normalised references, blank optional fields, collapsed duplicate rows, fee/consistency mismatches, and missing timestamps are attached as `dq_flags` on the source rows and the item. Rejected rows live in the import's validation report. A "Data quality" view lists both. Data-quality notes never add to the dispute totals.

### 7.5 Determinism

- No randomness, no wall-clock time inside the engine, no LLM, no dependence on input row order or dict iteration order.
- Output is sorted by the stable key above.
- Tests: shuffle the input rows and assert an identical result; a golden test pins the category of every order in the sample data plus the headline totals (section 13).

## 8. Headline metrics (per currency, never summed across currencies)

| Metric | Definition |
|---|---|
| Total orders | Unique order keys (raw row count shown alongside) |
| Total payments | Payment events (charges / refunds by status) and settled charge value |
| Value reconciled | Σ order net for `MATCHED`, `WITHIN_TOLERANCE`, `LATE_CHARGE` |
| Value in dispute | Face value of every other item (order net, or the orphan payment amount) |
| Money at risk | Σ `amount_at_risk`, split by `risk_direction` into **under-collected** (store is short; pending is shown as a labelled portion of this), **over-collected** (owed to customers or unexplained cash), and **unverified** (currency mismatch / needs review) |

"In dispute" is how much business is affected. "At risk" is how much money is actually wrong. For example, ORD-1401 is $92.81 in dispute but $25.00 at risk. Every metric is computed from `reconciliation_items`, so clicking it filters the table to exactly the rows behind it.

## 9. LLM explanations

- **Where:** backend endpoints `POST /api/imports/{id}/items/{item_id}/explain` and `POST /api/imports/{id}/summary/explain`. The API key lives only in the backend environment.
- **Input:** a compact evidence object (category, rule, expected/actual/difference, currencies, statuses, event list, timestamps). **No customer email or other PII is sent.** The run summary sends the metrics plus at most the top 20 items, so prompt size is bounded.
- **Prompt-injection guard:** CSV values (ids, statuses) are untrusted. They are passed as a JSON data block, and the system instruction says to treat them as data and to explain only what the evidence supports. The output is constrained by a schema and rendered as plain text in the UI, never as HTML.
- **Output (JSON schema):** `{ summary, likely_cause, recommended_actions[1..4], confidence: low|medium|high }`; for a run summary, `{ headline, top_issues[], recommended_priorities[] }`. String lengths and array sizes are capped.
- **Parameters:**
  - **Model:** a Gemini Flash-class model (configured by `GEMINI_MODEL`).
  - **Temperature:** left at the **provider default of 1.0**. This is deliberate. For the Gemini 3 family, Google recommends keeping temperature at 1.0 and warns that lowering it can cause looping or degraded output.
  - **Where consistency comes from instead:** the JSON schema; low `thinking_level` (the task is summarising given facts, not open reasoning, so this also cuts latency and cost); a capped output length; and the **cache**, which makes each item's explanation generated once and then stable for every viewer.
  - If an older non-reasoning model is configured instead, a low temperature (≈ 0.2) is the right choice for that model. The setting lives next to the model choice for that reason.
- **Bad responses:** a provider error, timeout, non-JSON output, or schema violation (validated with Pydantic) leads to one retry. If that also fails, the API returns a **deterministic template explanation** built from the evidence, marked `source: "fallback"`. The UI shows which source produced the text. The reconciliation result never depends on the LLM.
- **Cache key:** import + item + prompt version. Changing the model or prompt bumps `PROMPT_VERSION`.

## 10. Deliberate cuts (and how to add them back)

| Cut | Reason | Path forward |
|---|---|---|
| Organisations / memberships / roles | The requirement is per-user isolation | Add `tenants` + `memberships`; swap the `user_id` check on `imports` for `tenant_id` |
| Object storage for files | Files are KB-sized and stored in Postgres with a hash, in the same transaction | S3 with private keys + signed URLs |
| Background queue | Synchronous processing finishes in milliseconds | Worker + job table; the job carries ids and re-checks ownership |
| Refresh tokens, revocation, password reset | Out of scope for 24 h | Rotating refresh token + server-side session table, email reset flow |
| Login rate limiting | bcrypt cost slows brute force; proper limiting needs trusted client IPs behind the proxy | Per-account + per-IP limiter (Redis) |
| Postgres row-level security | One enforced query path + tests | RLS as defence in depth |
| FX conversion | No agreed rate source | Rates table with source and timestamp |

## 11. API

| Method | Path | Purpose | Responses |
|---|---|---|---|
| POST | `/api/auth/signup` | Create account + session | 201, 409 email taken, 422 invalid |
| POST | `/api/auth/login` | Session | 200, 401 |
| POST | `/api/auth/logout` | Clear session | 204 |
| GET | `/api/auth/me` | Current user | 200, 401 |
| POST | `/api/imports` | Multipart `orders` + `payments` → validate, store, reconcile | 201 new, 200 `already_imported`, 413 too large, 422 file errors |
| GET | `/api/imports` | The user's import history (newest first, no raw file content) | 200 |
| GET | `/api/imports/{id}` | Summary metrics + validation report (rejected rows) | 200, 404 |
| GET | `/api/imports/{id}/items` | Filters: `category`, `severity`, `direction`, `currency`, `has_dq`, `q` (order id / transaction ref / email); `sort`; `page`, `page_size`. Defaults to non-matched items, sorted by priority | 200, 404 |
| GET | `/api/imports/{id}/items/{item_id}` | Full evidence + cached explanation if one exists | 200, 404 |
| POST | `/api/imports/{id}/items/{item_id}/explain` | LLM explanation (or fallback) | 200, 404 |
| POST | `/api/imports/{id}/summary/explain` | LLM summary of the whole run | 200, 404 |
| GET | `/api/health` | Liveness (no config details) | 200 |

All non-2xx responses use the error shape from section 5a. Unauthenticated requests get `401`.

## 12. UI

1. **Login / Sign up.**
2. **Imports:** upload both files (drag and drop, with client-side checks for type and size), see the validation report (rejected rows with reasons), and the import history.
3. **Dashboard** (latest import by default, switchable from history):
   - KPI row: orders, payments, reconciled, in dispute, money at risk (under / over / unverified).
   - Chart: money at risk by category. Clicking a bar filters the table.
   - **"Look at these first"** table: severity, category, order, expected vs actual, at risk. It has filters, search, and sorting. Clicking a KPI filters the table; the "reconciled" KPI shows matched items.
   - Row → side drawer with evidence (source rows with row numbers), data-quality notes, and an **Explain with AI** button (loading, error, and fallback states).
   - Tabs: Discrepancies · Data quality · AI summary.
   - Explicit states: no imports yet, upload in progress, file rejected, imported with warnings, zero discrepancies, and session expired (redirect to login).

## 13. Testing

- **Engine:** a golden test pinning every order's category in the sample files and the headline totals:
  - under-collected $886.85
  - over-collected $936.58
  - unverified USD 210.00 + EUR 145.00
  - reconciled $39,963.28 across 168 orders

  Plus the row-shuffle determinism test, and unit tests for:
  - tolerance edges (0.05 → within, 0.06 → mismatch)
  - split payment → matched
  - duplicate charge later refunded → matched
  - overcharge corrected by a partial refund → matched
  - cancelled with failed charge → matched
  - cancelled with pending charge → pending, over-collected
  - refunded with no events → matched
  - refunds exceeding charges → over-refunded
  - completed with partial refund → refund not recorded
  - a rejected payment row referencing an order → needs review (not missing payment)
  - currency mismatch never summed across currencies
- **Ingestion:**
  - DD/MM date parsing and blank timestamps
  - blank discount (gross = net vs gross ≠ net)
  - reference normalisation
  - exact-duplicate collapse vs id conflict
  - missing headers / swapped files
  - header-only file and empty file
  - > 2 dp, negative and malformed amounts
  - unknown currency or status
  - oversize file
- **API / security:**
  - signup/login/logout and duplicate signup → 409
  - password > 72 bytes → 422
  - unauthenticated → 401 and cookie flags set
  - POST with a foreign `Origin` → 403
  - cross-user list/read/explain → 404
  - same file pair uploaded twice (sequentially and concurrently) → one import
  - import failure mid-write → no partial rows
  - explain twice → one cached row, including the run summary
  - LLM timeout / malformed JSON / schema violation → fallback, not cached
- The sample CSVs are committed under `backend/tests/fixtures/` as test fixtures.

## 14. Configuration (`.env.example`, no real values)

| Variable | Used by | Purpose |
|---|---|---|
| `DATABASE_URL` | API, migrations | Postgres connection string. Use Neon's **direct (non-pooled)** endpoint: transaction-mode poolers break prepared statements, and the single API instance holds a small pool itself |
| `JWT_SECRET` | API | ≥ 32 random bytes |
| `COOKIE_SECURE` | API | `true` in production, `false` only for local HTTP |
| `APP_ORIGIN` | API | Allowed `Origin` for state-changing requests |
| `GEMINI_API_KEY` | API | LLM key (backend only) |
| `GEMINI_MODEL` | API | Model id |
| `API_BASE_URL` | Web (server-side) | Target of the `/api/*` rewrite |

## 15. Delivery order

1. Engine + ingestion + tests
2. DB models, migrations, auth, API
3. Frontend: auth, upload, dashboard, drill-down
4. LLM explanations + fallback
5. Deploy (Neon → Railway → Vercel)
6. README: setup, architecture, reconciliation logic, findings, LLM approach, next steps, AI-tool usage; `.env.example`; demo credentials
