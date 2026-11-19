# Design Notes — Revenue Reconciliation Dashboard

> Status: proposed design for review. This document captures the current architecture direction and the decisions that must be confirmed before implementation.

## 1. Goals and principles

- Import orders and payment events from customer-provided CSV files.
- Reconcile records using deterministic, explainable rules.
- Support multiple customers with strict tenant isolation.
- Preserve original files, source values, and processing history.
- Make reconciliation runs reproducible using fixed inputs and a rules version.
- Use AI only to explain already-computed evidence; AI must not make matching or financial decisions.
- Use exact decimal or integer-minor-unit arithmetic. Never use binary floating point for money.
- Keep currencies separate unless an explicit FX policy is agreed.
- Start with a modular monolith rather than premature microservices.

## 2. Where uploaded CSVs and their rows live

Use both private object storage and relational tables, for different purposes.

### Original files: private object storage

Store every uploaded CSV as an immutable object under a backend-generated key, for example:

    tenants/{tenant_id}/uploads/{upload_id}/source.csv

The database stores metadata and the storage key, not the file bytes. Metadata should include upload ID, tenant ID, uploader, dataset type (orders/payments), original filename, storage key, content hash, byte size, content type, upload time, processing status, and validation summary.

Keep the bucket private. A download must be authorized by the API before it issues a short-lived signed URL or streams the file. A storage key is not authorization. Apply file-size limits, safe filename handling, encryption, and a retention policy.

### Parsed rows: shared relational tables

Do not create a physical table for every customer. Use shared tables with explicit tenant and upload ownership columns. For example:

    tenants
      └── uploads
            ├── order_records
            └── payment_records
      └── reconciliation_runs
            └── reconciliation_items

Every imported row carries tenant_id and upload_id. All repository/service operations require a verified tenant context and filter by it. Use composite foreign keys where practical so a row cannot accidentally reference another tenant's upload.

A shared-schema model keeps migrations and reporting manageable. Per-customer tables make migrations, indexing, ORM mapping, and analytics harder and do not automatically guarantee isolation.

### Why not reconcile directly from CSV every time?

Repeated parsing wastes work. Search, filtering, pagination, dashboard aggregation, run history, audit evidence, and reprocessing become awkward. Persist normalized rows once, then reconcile database records associated with the selected uploads. Keep the original file as immutable source evidence.

## 3. Proposed architecture

    Browser / Next.js
       | authenticated access token
       v
    FastAPI API
       | verify signature, issuer, audience, expiry and claims
       v
    Verified user + tenant context
       |
       +--> Upload / ingestion service --> private object storage
       |              |
       |              +--> parse, validate, normalize --> PostgreSQL
       |
       +--> Reconciliation service --> deterministic rules --> PostgreSQL
       |
       +--> Dashboard query service -----------------------> PostgreSQL
       |
       +--> Explanation service --> minimal evidence --> Gemini API

### Request path

1. The user signs in through the identity provider.
2. The frontend sends an access token to FastAPI.
3. FastAPI independently validates the token using the provider's supported mechanism.
4. FastAPI resolves the internal user and tenant membership from trusted identity claims and database records.
5. Authorization dependencies create a verified tenant context.
6. Every domain service receives that context. A client-supplied tenant ID is never proof of access.
7. The service performs a tenant-scoped operation and returns only authorized data.

### Backend modules

- Identity and access: token verification, user mapping, tenant membership, authorization.
- Ingestion: upload metadata, object storage adapter, CSV parsing, validation, normalization, import lifecycle.
- Reconciliation: run orchestration, matching strategies, classification, financial calculations, evidence generation.
- Dashboard/read models: tenant-scoped aggregates, filters, pagination, detail views.
- Explanation: provider adapter, prompt construction, output validation, rate limits, safe fallback.
- Persistence: SQLAlchemy models, repositories, transaction boundaries, migrations.

Keep domain rules independent of FastAPI route handlers and external providers so they can be unit-tested directly.

## 4. Authentication and tenant authorization

Authentication establishes who the user is. Authorization establishes which tenant and resources the user may access.

### Frontend

- Use the identity provider's supported sign-in/session integration.
- Attach the access token to API requests using the documented scheme.
- Handle expired sessions through the provider SDK.
- Never expose provider secret keys in browser code.

### Backend

- Verify token signature and allowed signing algorithm.
- Verify issuer and audience according to the selected token contract.
- Verify expiry and not-before claims.
- Validate the required subject/identity claims.
- Resolve the internal user and active tenant membership server-side.
- Reject missing, invalid, expired, or unauthorized identities.

A request body containing tenant_id must not grant access to that tenant. If a user belongs to multiple tenants, tenant switching must be an explicit authorized operation and membership must be checked server-side.

Use tenant_id on tenant-owned tables, tenant-required repository methods, composite foreign keys where practical, and optionally PostgreSQL row-level security after connection-pooling behavior is tested. Add negative tests proving one tenant cannot read or mutate another tenant's uploads, rows, runs, or results.

Clerk is the current managed-auth proposal. Confirm the exact token type, issuer/audience rules, and verification implementation against the chosen Clerk integration before coding; do not assume all Clerk tokens are interchangeable.

## 5. Data model proposal

The following is conceptual. Exact fields and constraints depend on the source brief and agreed CSV contract.

| Table | Main purpose | Important fields |
|---|---|---|
| tenants | Customer workspace | id, name, created_at |
| users | Internal identity | id, identity_provider_subject, email |
| tenant_memberships | User access to tenant | tenant_id, user_id, role |
| uploads | Source file and processing lifecycle | id, tenant_id, uploader, source_type, storage_key, sha256, status, row counts |
| order_records | Parsed order rows | id, tenant_id, upload_id, source_row_number, raw_record, raw_order_id, normalized_order_id, dates, currency, amounts, status |
| payment_records | Parsed payment events | id, tenant_id, upload_id, source_row_number, raw_record, raw references, normalized reference, processed_at, currency, amount, fee, net_settled, type, status |
| reconciliation_runs | Immutable execution record | id, tenant_id, orders_upload_id, payments_upload_id, rules_version, status, timestamps |
| reconciliation_items | Result and evidence | run_id, classification, linked source records, compared values, difference, reason codes |
| audit_events | Important user/system activity | tenant_id, actor, action, resource, timestamp |

Store commonly queried fields as typed columns. A raw JSON payload can preserve source provenance and extra columns, but should not replace typed/indexed financial fields. Preserve source row number and original field values alongside normalized values.

### Money and currency

Parse amounts with Decimal from the original text. Store integer minor units with explicit currency scale or NUMERIC with validated scale. Keep gross amount, discount, net amount, fee, charge amount, refund amount, and settlement amount conceptually distinct. Never aggregate across currency codes without an agreed conversion source, timestamp, and policy.

## 6. CSV ingestion lifecycle

1. Create an upload record in UPLOADED state.
2. Persist the original file in private object storage.
3. Validate size, encoding, delimiter, headers, and required columns.
4. Parse using explicit date and decimal rules.
5. Record row-level errors and warnings with source row numbers.
6. Normalize comparison fields while preserving raw values.
7. Persist valid rows in a transaction or bounded batches.
8. Mark the upload READY, READY_WITH_WARNINGS, or FAILED.
9. Allow reconciliation only for uploads in an eligible state.

File-level errors include unreadable/empty files, unsupported encoding, missing required headers, or excessive size. Row-level errors include missing identifiers, invalid dates, malformed amounts, unsupported currencies, and invalid statuses. Warnings can include optional missing fields, duplicate identifiers, and unknown non-critical columns.

Never silently convert malformed financial values to zero or discard invalid rows without a visible validation report. Avoid logging sensitive raw row data.

### Normalization

- Trim leading/trailing whitespace.
- Apply case normalization only where identifier semantics permit it.
- Preserve the original identifier exactly.
- Do not remove punctuation or rewrite identifiers without an explicit rule.
- Parse timestamps with explicit formats and a documented timezone policy.
- Keep raw and normalized values side by side.

Use a content hash to detect identical uploads within a tenant and dataset type. Warn rather than silently discard: a customer may intentionally want a new run. Make ingestion idempotent for a given upload ID and parser version.

## 7. Reconciliation design

A reconciliation run is immutable after completion. It references the tenant, selected orders and payments uploads, rules version/configuration, engine version, initiating user, timestamps, status, and summary counters. A rerun creates a new run rather than overwriting previous results.

### Matching approach

Use staged deterministic matching and store evidence for each decision:

1. Normalize identifiers using a documented function.
2. Group payment events by normalized order reference.
3. Separate charge and refund events.
4. Interpret payment status before calculating settlement outcomes.
5. Compare values only within the same currency.
6. Account for multiple payment events, partial charges, retries, and refunds using an agreed aggregation policy.
7. Classify missing, duplicate, ambiguous, and amount-mismatch cases instead of forcing a match.

Do not assume one payment row must equal one order row. The exact matching precedence and accounting semantics remain open until confirmed against the business requirements.

### Candidate classifications

- Matched.
- Order without payment event.
- Payment event without order.
- Amount mismatch.
- Currency mismatch.
- Pending payment.
- Failed payment.
- Duplicate/repeated payment event.
- Full or partial refund.
- Ambiguous match requiring review.
- Invalid or excluded source row.

A single order may relate to multiple payment events. The result model must support one reconciliation item linked to multiple source events, or a separate evidence/link table.

### Evidence

Persist the source order and payment IDs considered, normalized keys, amounts and currencies compared, included/excluded statuses, applied rule IDs, difference amount, final classification, and reason codes. The UI should allow a reviewer to reach the same conclusion without relying on AI-generated prose.

### Concurrency

Prevent duplicate active runs for the same input pair using a database constraint, lock, or idempotency key. If processing exceeds request limits, use a background worker/queue. The job must carry run/upload IDs and tenant context, and the worker must re-check ownership.

## 8. Dashboard and investigation experience

Suggested overview metrics: orders imported, payment events imported, matched items, unmatched orders, unmatched payment events, amount discrepancies, pending/failed events, and refund counts/totals. All monetary aggregates must be grouped by currency and clearly define which statuses are included.

Provide a searchable, filterable discrepancy table; filters for run, category, currency, payment status, and date; server-side pagination/sorting; and a detail panel showing source records, related payment events, normalized values, calculations, and reason codes. Include links to the source upload and row number.

Design explicit states for no uploads, invalid files, processing, partial validation, failed processing, and completed runs with no discrepancies. A zero-discrepancy result must be distinguishable from a run with no eligible records.

## 9. Proposed API surface

| Method | Endpoint | Purpose |
|---|---|---|
| GET | /api/v1/me | Current user and tenant context |
| GET | /api/v1/tenants | List accessible tenants |
| POST | /api/v1/tenants/{tenant_id}/uploads | Create upload metadata/session |
| GET | /api/v1/uploads | List current-tenant uploads |
| GET | /api/v1/uploads/{upload_id} | Upload status and validation summary |
| POST | /api/v1/uploads/{upload_id}/process | Start/retry ingestion |
| POST | /api/v1/reconciliation-runs | Create a run for selected uploads |
| GET | /api/v1/reconciliation-runs | List runs |
| GET | /api/v1/reconciliation-runs/{run_id} | Run status and summary |
| GET | /api/v1/reconciliation-runs/{run_id}/items | Paginated results |
| GET | /api/v1/reconciliation-runs/{run_id}/items/{item_id} | Detailed evidence |
| POST | /api/v1/reconciliation-runs/{run_id}/items/{item_id}/explanation | Optional AI explanation |

A tenant ID in a URL is a selector, not proof of authorization. The API must verify membership. An active-tenant context/header may be a cleaner contract; this is an open API design choice.

## 10. AI explanation boundary

AI is optional and sits outside the critical reconciliation path. Give the model a compact, typed evidence object containing only facts needed to explain one result. Require it to explain only supplied evidence, disclose incomplete/ambiguous evidence, and never invent transactions, statuses, amounts, causes, or financial actions.

The product must remain functional if the provider is unavailable, rate-limited, or returns invalid output. Validate the response schema and use a safe fallback. Avoid sending unnecessary customer-identifying information to the provider.

## 11. Observability and operational safety

- Use structured logs with request ID and run ID; include tenant ID only where safe.
- Never log access tokens, secrets, or full raw CSV rows.
- Track upload lifecycle, run transitions, durations, row counts, and error counts.
- Define object and parsed-data retention/deletion policies.
- Store secrets in deployment secret management, never source control.
- Apply request size limits, rate limits, and strict CORS.
- Configure database backups and test restore procedures.
- Use health/readiness endpoints that do not reveal sensitive configuration.

Do not rely on an ephemeral application filesystem for customer uploads. Use private object storage; a persistent volume is an alternative only when deliberately configured and operationally managed.

## 12. Testing strategy

**Unit tests:** identifier normalization, raw-value preservation, date parsing, Decimal/currency behavior, status interpretation, charge/refund separation, matching precedence, classification, and evidence generation.

**Integration tests:** upload lifecycle, row-level validation, persistence/rollback, reconciliation against fixture datasets, dashboard aggregation by currency, token verification, tenant membership, and object-download authorization.

**Security tests:** cross-tenant list/read/update/delete attempts, guessed resource IDs, unauthorized object downloads, repeated processing requests, concurrent run creation, and role escalation attempts.

**Invariants:** a result may reference only records from the same tenant and selected uploads; completed run inputs cannot change; identical inputs and rule versions produce identical classifications/calculations; currency totals are never combined; financial calculations never use binary floats.

## 13. Deployment proposal

- Next.js frontend: Vercel.
- FastAPI API: Railway.
- PostgreSQL: Neon.
- Original CSVs: private object storage.
- Add a background worker/queue if ingestion or reconciliation exceeds synchronous request limits.

Validate platform limits, persistent storage behavior, networking, and secret management before production deployment.

## 14. Decisions to confirm before implementation

1. Is order_id/order_reference the canonical matching key? Is case/whitespace normalization allowed?
2. How should duplicate order IDs be handled: import error, ambiguous result, or another policy?
3. Can an order have retries, multiple successful charges, partial captures, or split payments?
4. Are refunds full or partial, and how should they net against the original charge?
5. Which amount is the comparison basis: order net amount, payment amount, settled charge sum, or net_settled after fees?
6. Are fees informational or part of the reconciliation target?
7. Which payment statuses count as final/eligible?
8. Is multi-currency support separate totals only, or is FX conversion required?
9. What timezone applies when a source timestamp has no offset?
10. Should valid rows be imported when other rows are invalid, or should the whole file fail?
11. Is one reconciliation result per order, per payment event, or a linked group of events?
12. Can users belong to multiple tenants? Which roles are needed?
13. What file-size/row-count limits and retention policy apply?
14. Which exact dashboard metrics and filters are required by the product brief?

## 15. Suggested delivery sequence

1. Agree on requirements, terminology, example outcomes, and edge cases.
2. Finalize identity/tenant authorization and data contracts.
3. Implement upload metadata, private storage, CSV validation, and normalized persistence.
4. Implement deterministic reconciliation with fixture-driven tests.
5. Add run history, dashboard aggregates, and discrepancy exploration.
6. Add optional AI explanations over stored evidence.
7. Add deployment hardening, observability, and tenant-isolation regression tests.

Do not treat implementation decisions as locked until the requirements and reconciliation semantics have been reviewed with concrete examples.
