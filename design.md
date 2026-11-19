# Design — Revenue Reconciliation Dashboard

> Status: **agreed design** (v1). This replaces the earlier proposal. The earlier draft targeted a multi-tenant SaaS; this version is scoped to what the product needs now, with each cut recorded in section 10 together with the path to add it back.

## 1. Goals

- Let a signed-in user upload an orders export and a payment-processor export, reconcile them, and see where money is leaking.
- Reconciliation is **deterministic and repeatable**: the same input files and rules version always produce the same result, regardless of row order.
- Every discrepancy carries **evidence** (source rows, compared values, rule applied) so a reviewer can reach the same conclusion without AI.
- An LLM **explains** results in plain language. It never decides whether records match.
- Money is handled with `Decimal` / `NUMERIC(14,2)`, never floats. Amounts in different currencies are never added together.

## 2. Architecture

```
Browser ──► Next.js (Vercel)
              │  /api/*  rewrite (same origin → httpOnly cookie, no CORS)
              ▼
           FastAPI (Railway)
              ├─ auth        : signup / login / logout / me (bcrypt + JWT cookie)
              ├─ ingestion   : CSV validation, parsing, normalisation
              ├─ reconcile   : pure deterministic engine (no I/O)
              ├─ dashboard   : summary + filterable discrepancy queries
              └─ explain     : LLM adapter (Gemini) with schema validation + fallback
              ▼
           PostgreSQL (Neon)
```

- **Modular monolith.** The reconciliation engine is a pure function, `(orders, payments, rules) -> result`, with no database or HTTP dependency, so it is unit-tested directly.
- **Same-origin proxy.** Next.js rewrites `/api/*` to the FastAPI service. The browser only ever talks to one origin. This lets the session live in an `httpOnly; Secure; SameSite=Lax` cookie (not readable by JavaScript, so not stealable by XSS), and no CORS configuration is needed.
- **Synchronous processing.** Ingest + reconcile of a few thousand rows takes milliseconds. Upload size is capped (5 MB per file). A queue is noted as future work.

## 3. Stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js (App Router), TypeScript, Tailwind, shadcn/ui | Accessible components, fast to build a clean UI |
| Charts / table | Recharts, TanStack Table | Recharts underpins shadcn charts; TanStack handles filtering and sorting |
| API | FastAPI, Pydantic | Typed request/response contracts, Decimal support |
| ORM / migrations | SQLAlchemy 2.x, Alembic | Explicit schema, reviewable migrations |
| Database | PostgreSQL on Neon | Real relational DB, NUMERIC money, free managed tier |
| Auth | Own: bcrypt password hashing + JWT (HS256) in httpOnly cookie | Small, auditable, no third-party signup friction |
| LLM | Google Gemini API, called from the backend only | Brief allows any provider; JSON-schema structured output |
| Hosting | Vercel (web), Railway (API, no idle sleep), Neon (DB) | No cold start for reviewers |
| Tests | pytest (engine, ingestion, API, isolation) | |

## 4. Authentication and data isolation

- **Signup:** email (normalised to lower case, unique) + password (min 8 chars). Password hashed with bcrypt (per-hash salt, adaptive cost).
- **Login:** constant-time check; the same error for an unknown email and a wrong password, so the response doesn't reveal which emails exist.
- **Session:** JWT with `sub` = user id, `exp` = 12 h, signed with `JWT_SECRET` from the environment. Sent as an `httpOnly`, `Secure`, `SameSite=Lax` cookie. Logout clears the cookie. There is no refresh token; this trade-off is documented.
- **Authorisation:** every protected route depends on `current_user`. Every query on user-owned data filters by `user_id` (the user is the tenant). A resource owned by someone else returns **404, not 403**, so its existence isn't leaked.
- **CSRF:** a SameSite=Lax cookie is not sent on cross-site POSTs. State-changing endpoints also accept only JSON or multipart from the same origin.
- **Tests** prove that user B cannot list, read, or explain user A's imports or discrepancies.

## 5. Data model

| Table | Purpose | Key columns |
|---|---|---|
| `users` | Accounts | id (uuid), email (unique), password_hash, created_at |
| `imports` | One upload of an orders file + payments file and its reconciliation | id, user_id, orders_filename, payments_filename, orders_sha256, payments_sha256, orders_raw, payments_raw, rules_version, summary (jsonb), created_at. **Unique (user_id, orders_sha256, payments_sha256, rules_version)** |
| `orders` | Parsed order rows | id, import_id, user_id, row_number, order_id_raw, order_key, order_date, customer_email, currency, gross_amount, discount, net_amount, status, raw (jsonb), dq_flags (text[]) |
| `payments` | Parsed payment events | id, import_id, user_id, row_number, transaction_ref, order_reference_raw, order_key, processed_at, currency, amount, fee, net_settled, type, status, raw (jsonb), dq_flags |
| `discrepancies` | One reconciliation result per order key (or per orphan payment) | id, import_id, user_id, order_key, category, severity, currency, expected_amount, actual_amount, difference, amount_at_risk, risk_direction, reason, evidence (jsonb: order rows, payment rows, rule ids) |
| `explanations` | LLM output cache | id, discrepancy_id (or import_id for run summaries), prompt_version, model, output (jsonb), created_at |

- Money columns are `NUMERIC(14,2)`, parsed with `Decimal` from the original text.
- The original CSV text and its sha256 are stored on the import, so raw values stay available for audit. Files go in Postgres rather than object storage on purpose. File + rows + results are written in **one transaction**, so an import is either fully saved or not saved at all. With a separate object store, a failure between the two writes leaves orphaned files or rows pointing at missing files. Object storage is the scale path (section 10).

## 5a. Idempotency and failure handling

A repeated request must never repeat its side effects. A failure must never leave half-written state.

| Operation | Duplicate / retry behaviour | How it is enforced |
|---|---|---|
| Sign up | Same email twice → second gets `409`, no second account | Unique index on lower-cased email; the insert catches `IntegrityError` (no check-then-insert race) |
| Upload + reconcile | Same orders + payments file pair (same bytes) → returns the **existing** import with `already_imported: true`; no new rows, no new run | Unique `(user_id, orders_sha256, payments_sha256, rules_version)`. The insert uses `ON CONFLICT DO NOTHING`, then reads back, so two concurrent identical uploads still produce exactly one import |
| Reconciliation | Re-running on the same import gives byte-identical results | Pure deterministic engine; results are written once, inside the import transaction |
| Partial failure during import | Parse error, DB error, or crash mid-import → nothing persists | Single transaction around file + orders + payments + discrepancies + summary |
| LLM explain | Repeated or concurrent clicks for the same item → one stored explanation, returned from cache | Unique `(discrepancy_id, prompt_version)`; insert-or-read-existing |
| LLM failure | Timeout, provider error, or invalid JSON → one retry, then a deterministic fallback | The fallback is returned, not cached as a real LLM answer, so a later click can retry. Reconciliation data is never touched by this path |
| Frontend double submit | Upload / explain buttons disable while a request is in flight | Client guard; the server-side rules above are the real protection |

Failure-handling rules:
- Validation errors are reported with row numbers. Invalid financial values are never coerced to 0.
- Every external call (LLM) has a timeout. Its failure degrades only the explanation, never the core result.
- Errors return a consistent JSON shape `{ "error": { "code", "message" } }` without stack traces or secrets.

## 6. Ingestion rules

**File level:** UTF-8 (BOM tolerated), size ≤ 5 MB, required headers present (case- and whitespace-insensitive). Missing headers reject the file with a clear message; nothing is partially saved.

**Row level:** a row with an unparseable required financial field (id, amount, currency) is **excluded and reported** with its row number. It is never silently converted to 0. Valid rows still import, and the upload shows "imported with warnings".

| Field | Rule |
|---|---|
| Order id / order reference | Raw value kept. Match key = `strip().upper()`. Normalisation is recorded as a data-quality note (`ord-1802`, `" ord-1801 "`) |
| Order date | ISO `YYYY-MM-DD HH:MM:SS` |
| Payment `processed_at` | `DD/MM/YYYY HH:MM`. The column format is fixed because some rows have day > 12, and order-to-charge lags of 1–90 minutes confirm it. Blank → kept as null with a warning; timing checks are skipped for that row |
| Amounts | `Decimal`, 2 dp, ≥ 0 |
| Discount | Blank → 0 **only if** gross = net, else a data-quality error |
| Consistency | `gross − discount = net` (orders) and `amount − fee = net_settled` (payments) are checked; a mismatch is flagged |
| Currency | 3-letter uppercase code |
| Status/type | Known values only: orders `completed / cancelled / refunded`; payments type `charge / refund`, status `settled / pending / failed` |
| Exact duplicate order row | Collapsed to one, flagged `duplicate_row` (ORD-1004) |
| Same order id with different content | Flagged `id_conflict`; excluded from matching and shown for review |
| Duplicate transaction_ref | Exact duplicate collapsed and flagged; different content → conflict |
| Timezone | Both files are timezone-naive and treated as the same zone; the observed lags support this |

## 7. Reconciliation engine

### 7.1 Matching

1. Build the order index by `order_key` (after de-duplication).
2. Group payment events by `order_key`.
3. Per order, from **settled** events in the **order's currency**: `collected = Σ settled charges − Σ settled refunds`.
4. Expected net cash by order status: `completed` → `net_amount`; `cancelled` → 0; `refunded` → 0 (a charge followed by a full refund).
5. Payment groups whose key has no order become **orphan payments**.

A pending or failed event moves no money and does not count towards `collected`. Fees are processor cost: they are validated (2.9% + $0.30 pattern) but are not part of what the customer owes, so the comparison is order `net_amount` against charge `amount`.

### 7.2 Tolerances

| Rule | Value | Reasoning |
|---|---|---|
| Amount tolerance | **$0.05 absolute per order**, same currency only | Observed noise is ≤ $0.02 (rounding); the smallest real error is $18.50. A percentage tolerance would hide large errors on large orders. Items within tolerance are reported as `WITHIN_TOLERANCE`: counted as reconciled but still visible. |
| Late charge | Charge processed **> 7 days** after the order (or > 1 h before it) | All normal charges land 1–90 min after the order. Card authorisations typically expire after about 7 days, so a capture weeks later suggests a re-charge or wrong linkage. Refunds are exempt. |

Both values live in one versioned rules config (`RULES_VERSION`), which is stored on every import.

### 7.3 Classification (first matching rule wins)

| # | Category | Condition | Severity | Amount at risk | Direction |
|---|---|---|---|---|---|
| 1 | `CURRENCY_MISMATCH` | Any payment event currency ≠ order currency | High | Order net | Unverified |
| 2 | `MISSING_PAYMENT` | Completed order, no payment events | High | Order net | Under-collected |
| 3 | `PAYMENT_FAILED` | No settled charge; a failed charge exists | High | Order net | Under-collected |
| 4 | `PAYMENT_PENDING` | No settled charge; a pending charge exists | Low | Order net | Under-collected (pending) |
| 5 | `CANCELLED_BUT_CHARGED` | Cancelled order, collected > 0 | High | Collected | Over-collected |
| 6 | `REFUND_INCOMPLETE` | Refunded order, collected > 0 | High | Collected | Over-collected |
| 7 | `REFUND_NOT_RECORDED` | Completed order, settled refund(s) exist | Medium | Refunded amount | Under-collected (revenue overstated) |
| 8 | `DUPLICATE_CHARGE` | > 1 settled charge, each equal to the expected amount, sum > expected | High | Collected − expected | Over-collected |
| 9 | `OVERCHARGED` | collected − expected > tolerance | High | Difference | Over-collected |
| 10 | `UNDERCHARGED` | expected − collected > tolerance | Medium | Difference | Under-collected |
| 11 | `WITHIN_TOLERANCE` | 0 < \|difference\| ≤ tolerance | Info | 0 | — |
| 12 | `LATE_CHARGE` | Amount agrees, timing rule breached | Low | 0 | — |
| 13 | `MATCHED` | Everything agrees | — | 0 | — |
| — | `ORPHAN_PAYMENT` | Payment whose order key has no order | Medium | Payment amount | Over-collected (unexplained cash) |

A cancelled or refunded order with net collected = 0 is correct and is classified `MATCHED`.

Precedence rationale: a currency mismatch makes every amount comparison meaningless, so it goes first. Missing or unsettled money comes next. Status-driven expectations (cancelled/refunded) are checked before the generic amount comparison, so a cancelled-but-charged order is not mislabelled as "overcharged".

Severity reflects the order of action: **customer harm first** (they were charged wrongly, so there is chargeback and trust risk), then **revenue leakage**, then hygiene. Within a severity level, items sort by amount at risk, descending.

### 7.4 Data-quality notes (not discrepancies)

Normalised references, blank optional fields, collapsed duplicate rows, fee rounding, and missing timestamps are attached to the item as `dq_flags`. They are listed in a separate "Data quality" view. They never inflate the dispute totals.

### 7.5 Determinism

- No randomness, no wall-clock time inside the engine, no LLM.
- Output is sorted by a stable key (`severity rank, amount_at_risk desc, order_key`).
- A test shuffles input row order and asserts an identical result. A golden-file test pins the expected category of every order in the sample data.

## 8. Headline metrics (per currency, never summed across currencies)

| Metric | Definition |
|---|---|
| Total orders | Unique order keys (raw row count shown alongside) |
| Total payments | Payment events (charges / refunds) and settled charge value |
| Value reconciled | Σ order net for `MATCHED`, `WITHIN_TOLERANCE`, `LATE_CHARGE` |
| Value in dispute | Face value of every record that does not reconcile (order net, or payment amount for orphans) |
| Money at risk | Σ `amount_at_risk`, split into **under-collected** (store is short), **over-collected** (owed to customers or unexplained cash), and **unverified** (currency mismatch) |

"In dispute" is how much business is affected. "At risk" is how much money is actually wrong. For example, ORD-1401 is $92.81 in dispute but $25.00 at risk.

## 9. LLM explanations

- **Where:** backend endpoints `POST /api/imports/{id}/discrepancies/{did}/explain` and `POST /api/imports/{id}/summary/explain`. The API key lives only in the backend environment.
- **Input:** a compact evidence object (category, rule, amounts, currencies, statuses, event list, timestamps). **No customer email or other PII is sent.**
- **Output (JSON schema):** `{ summary, likely_cause, recommended_actions[], confidence: low|medium|high }`; for a run summary, `{ headline, top_issues[], recommended_priorities[] }`.
- **Parameters:** temperature **0.2**. Explanations should be stable and faithful to the evidence, not creative. It is slightly above 0 so the wording doesn't degenerate. Output tokens are capped and the request has a timeout (20 s).
- **Bad responses:** a provider error, timeout, non-JSON output, or schema violation leads to one retry. If that also fails, the API returns a **deterministic template explanation** built from the evidence, marked `source: "fallback"`. The UI shows which source produced the text. The reconciliation result never depends on the LLM.
- **Cache:** results are stored in `explanations`, keyed by item + prompt version, so repeated clicks are free and consistent.

## 10. Deliberate cuts (and how to add them back)

| Cut | Reason | Path forward |
|---|---|---|
| Organisations / memberships / roles | The requirement is per-user isolation | Add `tenants` + `memberships`; swap the `user_id` filter for `tenant_id` |
| Object storage for files | Files are KB-sized and stored in Postgres with a hash | S3 with private keys + signed URLs |
| Background queue | Synchronous processing finishes in milliseconds | Worker + job table; the job carries ids and re-checks ownership |
| Refresh tokens / password reset | Out of scope for 24 h | Rotating refresh token, email reset flow |
| Postgres row-level security | One enforced query layer + tests | RLS as defence in depth |
| FX conversion | No agreed rate source | Rates table with source and timestamp |

## 11. API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/signup` · `/api/auth/login` · `/api/auth/logout` | Session |
| GET | `/api/auth/me` | Current user |
| POST | `/api/imports` | Multipart: `orders` + `payments` files → validate, store, reconcile |
| GET | `/api/imports` | The user's import history |
| GET | `/api/imports/{id}` | Summary metrics + validation report |
| GET | `/api/imports/{id}/discrepancies` | Filter by category / severity / direction / search; paginated |
| GET | `/api/imports/{id}/discrepancies/{did}` | Full evidence |
| POST | `/api/imports/{id}/discrepancies/{did}/explain` | LLM explanation |
| POST | `/api/imports/{id}/summary/explain` | LLM summary of the whole run |
| GET | `/api/health` | Liveness |

## 12. UI

1. **Login / Sign up.**
2. **Imports:** upload both files (drag and drop) and see the validation report and import history.
3. **Dashboard** (latest import by default):
   - KPI row: orders, payments, reconciled, in dispute, money at risk (under / over / unverified).
   - Chart: money at risk by category. Clicking a bar filters the table.
   - **"Look at these first"** table: severity, category, order, expected vs actual, at risk. Filters, search, and sorting are included. Clicking a KPI filters the table.
   - Row → side drawer with evidence, data-quality notes, and an **Explain with AI** button (loading, error, and fallback states).
   - Tabs: Discrepancies · Data quality · AI summary.
   - Explicit states: no imports yet, processing, validation errors, zero discrepancies.

## 13. Testing

- **Engine:** a golden test that pins the expected category for every order in the sample files; the row-shuffle determinism test; unit tests per rule (tolerance edges at 0.05 / 0.06, cancelled with no charge, partial refund).
- **Ingestion:** date format, blank discount, normalisation, duplicates, missing headers, malformed amounts.
- **API:** signup/login/logout, duplicate signup → 409, unauthenticated → 401, cross-user access → 404, same file pair uploaded twice → one import, explain called twice → one cached explanation, LLM timeout / malformed JSON → fallback, import failure → no partial rows.
- The sample CSVs are committed under `backend/tests/fixtures/` as test fixtures.

## 14. Delivery order

1. Engine + ingestion + tests
2. DB models, migrations, auth, API
3. Frontend: auth, upload, dashboard, drill-down
4. LLM explanations + fallback
5. Deploy (Neon → Railway → Vercel)
6. README: setup, architecture, reconciliation logic, findings, LLM approach, next steps, AI-tool usage
