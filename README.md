# Revenue Reconciliation Dashboard

Upload an orders export and a payment-processor export, and see on one dashboard how much revenue reconciles, how much is in dispute, how much money is at risk, and which orders to look at first. Every result carries its evidence, down to the source row numbers. An LLM can explain a result in plain language, but it never decides one.

- **Live app:** https://revenue-reconciliation-p.vercel.app (API: https://revenue-reconciliation-p.up.railway.app/api/health)
- **Demo login:** _add the demo account after creating it on the live site_
- **Design:** [`design.md`](design.md) is the source of truth for the data model, rules, metrics, security and API.
- **Deployment:** [`docs/deployment.md`](docs/deployment.md)

## Local setup

### Everything at once (Docker)

```bash
cp .env.example .env        # set JWT_SECRET and POSTGRES_PASSWORD; GEMINI_* are optional
docker compose up --build
```

Open http://localhost:3000, sign up, and upload the two CSVs (sample files go in `backend/tests/fixtures/`). Without a Gemini key, "Explain with AI" returns the deterministic fallback.

### Without Docker

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 22, and Postgres 15+.

```bash
cd backend
cp .env.example .env        # point DATABASE_URL at your Postgres
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend
cp .env.example .env.local  # API_BASE_URL=http://localhost:8000
npm install
npm run dev
```

### Tests

```bash
cd backend
uv run pytest               # needs Postgres; set TEST_DATABASE_URL to override the default
```

The tests cover the engine (golden fixtures, row-shuffle determinism, every rule and refund boundary), ingestion, auth and CSRF, rate limits, idempotent and concurrent uploads, rollback on failure, user isolation, and the LLM adapter against a mocked provider.

## Architecture

```
Browser ──► Next.js (Vercel)  /api/* rewrite, same origin
               ▼
            FastAPI (Railway)
               ├─ auth        bcrypt, JWT in an httpOnly cookie, Origin check, rate limits
               ├─ ingestion   CSV → typed records + validation report        (pure)
               ├─ reconcile   records → classified items + metrics           (pure)
               ├─ imports     one-transaction write path, dashboard queries
               └─ explain     Gemini adapter, schema validation, cache, fallback
               ▼
            PostgreSQL (Neon)
```

| Path | What lives there |
|---|---|
| `backend/app/ingestion/` | File checks, field parsing, normalisation, duplicates vs id conflicts, rejected-row linking |
| `backend/app/reconcile/` | `rules.py` (versioned constants), `engine.py` (classification), `metrics.py` (per-currency sums) |
| `backend/app/services/imports.py` | Hash → parse → reconcile in memory → `INSERT … ON CONFLICT DO NOTHING` → children → `COMMIT` |
| `backend/app/security/` | Password hashing, JWT, client IP, rate limiter, Origin/Referer middleware |
| `backend/app/explain/` | Prompts, output schemas, REST adapter, retry and fallback, cache |
| `backend/alembic/` | Schema migration with every constraint and index from design.md section 4 |
| `frontend/src/app/` | Login, signup, imports, dashboard routes; `middleware.ts` redirects when there is no session |
| `frontend/src/components/dashboard/` | KPI row, risk chart, results table (TanStack Table), evidence drawer, explain panel |

Key choices:
- **Pure core.** Parsing and reconciliation are functions with no database, HTTP, clock or randomness, so they are tested directly.
- **Same-origin proxy.** The session cookie stays first-party, and no CORS configuration is needed.
- **Ownership lives only on `imports.user_id`.** Every query goes through `WHERE id = :id AND user_id = :me`, so another user's data returns 404.
- **Synchronous processing.** Files are capped at 2 MB, and a few thousand rows reconcile in milliseconds, so there is no job queue.

## Reconciliation logic

**Matching.** `order_id` and `order_reference` are trimmed and upper-cased, then matched. They are the only identifier the two files share. For each order, using settled events in the order's currency:

```
collected  = Σ settled charges − Σ settled refunds
expected   = net_amount if completed, else 0     (cancelled / refunded orders should keep nothing)
difference = collected − expected
```

Pending and failed events move no money, so they don't count towards `collected`, but they appear as evidence. The order's `net_amount` is compared with the charged `amount`, not `net_settled`, because processor fees are the store's cost, not the customer's error.

**Types.** These are checked in order and the first match wins:

| # | Category | When |
|---|---|---|
| 0 | Needs review | An id conflict, or a rejected row that names this order. No conclusion is drawn from incomplete evidence |
| 1 | Currency mismatch | A settled or pending event is in another currency. Amounts can't be compared |
| 2–4 | Missing / failed / pending payment | Nothing has settled yet |
| 5–6 | Cancelled but charged, refund incomplete | The order's status says the store should keep nothing |
| 7–8 | Over-refunded, refund not in orders | Refunds that the order record doesn't reflect |
| 9–11 | Duplicate charge, overcharged, undercharged | Generic amount rules, checked after the status rules |
| 12–14 | Within tolerance, late charge, matched | Reconciled |
| — | Orphan payment | A payment whose key has no order |

Each type has a fixed severity: customer harm first, then revenue leakage, then hygiene. The table is sorted by severity, then amount at risk, then order key.

**Tolerances, and why:**
- **0.05, absolute, in the transaction's currency.** Rounding noise in the sample files is ≤ 0.02 and the smallest real error is 18.50. A percentage tolerance would hide large errors on large orders. Items within tolerance stay visible.
- **Timing.** A settled charge more than 7 days after the order, or more than 1 hour before it, breaks the timing rule. Normal charges land 1–90 minutes after the order, and card authorisations typically expire after about 7 days. Refunds are exempt.
- **Versioned.** Both values are code constants under `RULES_VERSION`, not environment variables, so a stored import can always be reproduced.

**Amounts.** All money is `Decimal` / `NUMERIC(14,2)`. Amounts are never summed across currencies. Each item counts either as reconciled or as in dispute, never both (a database `CHECK` enforces this). Money at risk is split by direction: under-collected, over-collected, or unverified.

## What we found in the sample data

These totals come from the sample exports and are pinned in the golden test (`backend/tests/test_engine_golden.py`):

| | USD | EUR |
|---|---|---|
| Reconciled | 39,963.28 across 168 orders | — |
| In dispute | 2,469.37 | 145.00 |
| Under-collected (store is short) | 886.85 | — |
| Over-collected (owed to customers or unexplained cash) | 936.58 | — |
| Unverified (currency mismatch) | 210.00 | 145.00 |

What this means for the business:
- **Most revenue reconciles.** About 94% of USD order value (39,963.28 of 42,432.65) matches what was collected.
- **About 937 USD may be owed back to customers.** This comes from duplicate charges, overcharges, and cancelled or refunded orders that still hold money. It goes first: it is customer harm and a chargeback risk.
- **About 887 USD has not been collected,** from missing, failed or pending payments, refunds the order system doesn't show, and undercharges. Part of it is pending and may settle without action.
- **210.00 USD and 145.00 EUR can't be checked,** because the order and the payment are in different currencies. Someone needs to confirm which currency the customer actually paid in. The app does not do FX conversion, by design.
- **Data quality:** malformed rows are rejected rather than guessed. When a rejected row can be tied to an order, that order is marked Needs review instead of being reported as a false "missing payment".

_The per-category breakdown is printed by `cd backend && uv run python scripts/classify.py`; add its highlights here._

## LLM approach

- **What it does:** explains one result, or summarises a whole import. It never changes a category or an amount.
- **Prompting:**
  - A short system instruction: explain only what the evidence supports, use plain business language, quote currencies, and lower the confidence when unsure.
  - The evidence is a compact JSON object: category, rule, amounts, statuses, events and timestamps. It goes inside a `<data>` block that the model is told to treat as untrusted data, because CSV values could contain injected instructions.
  - No personal data is sent: no customer emails and no raw rows.
  - The summary sends the metrics plus the top 20 items.
- **Structured output:** the request asks for `application/json` with a JSON Schema. Every reply is validated with Pydantic: required fields, 1–4 actions, capped lengths, `extra="forbid"`. The UI renders it as plain text.
- **Temperature:**
  - Gemini 3 models keep the default 1.0, which Google recommends for them because lower values can cause looping, and use a low thinking level.
  - Any other model id gets 0.2.
  - Consistency comes from the schema, the capped output and the cache rather than from temperature.
- **Bad responses:** a timeout, provider error, invalid JSON or schema violation triggers **one retry**, with both attempts sharing a budget of under 25 s. If both fail, the user gets a **deterministic template** built from the evidence, labelled "rule-based". This fallback is **not cached**, so a later click can try the LLM again.
- **Cache:** one row per import + item + prompt version + model. `UNIQUE NULLS NOT DISTINCT` keeps a single whole-import summary row. Changing the model or the prompt never serves stale text, and repeated or concurrent clicks store one row.
- **Cost control:** 10 explain requests per user per minute.

## What I'd build next

- Shared rate-limit counters in Redis, so the API can run more than one instance.
- Background processing with a job table for files larger than a few MB, plus object storage for the originals.
- Teams: organisations and memberships, with imports scoped by tenant, and Postgres row-level security as defence in depth.
- Session revocation, refresh tokens and password reset.
- A way to resolve an item (mark it as investigated, with a note) and export the exception list.
- FX conversion once there is a trusted rate source, so currency mismatches can be checked.

## How AI tools were used

- **The design came first.** I wrote `design.md` before any code: the rules, metrics, constraints and failure handling.
- **Claude Code as a pair programmer.** It scaffolded the project and implemented each phase on its own branch in small commits. I reviewed every pull request before merging.
- **Ambiguities went back to me.** Where the design was ambiguous, the AI raised it instead of guessing, and the agreed clarifications were recorded in `design.md` in the same pull request.
- **Tests decide.** The fixture totals, the refund boundary table and the idempotency and isolation tests are the acceptance criteria. A change ships only when they pass.
- **No AI in the core.** AI never makes reconciliation decisions in the product. The engine is deterministic and the LLM only explains its output.
