# Revenue Reconciliation Dashboard

A multi-tenant application for importing order and payment data, reconciling financial records, investigating discrepancies, and understanding settlement outcomes.

The product is designed around a simple principle: **reconciliation decisions must be deterministic, explainable, reproducible, and isolated per customer**. AI may help explain a result, but it never decides whether two financial records match.

> Project status: design agreed (see `design.md`), implementation in progress.

## Problem

Businesses often maintain order records in one system and payment processor records in another. The two sources may differ in identifiers, timestamps, statuses, currencies, fees, refunds, and settlement timing. Manually comparing exports is slow and makes it difficult to identify missing payments, duplicate transactions, amount differences, pending/failed payments, or refunds.

This application provides a repeatable workflow to:

1. Upload an orders CSV and a payments CSV.
2. Validate each file and report row-level data-quality issues.
3. Normalize source fields without losing the original values.
4. Reconcile records using explicit, deterministic rules.
5. Classify matched records and discrepancies with evidence.
6. Explore totals and exceptions in a dashboard.
7. Request an AI-generated explanation of an already-computed result.

## Product boundaries

### In scope

- Authenticated, tenant-scoped workspaces.
- Uploading separate orders and payments CSV files.
- File metadata, validation summaries, and import history.
- Parsing and normalizing source records while preserving raw values.
- Reconciliation runs that are reproducible and retain their inputs and rule version.
- Deterministic matching and discrepancy classification.
- Currency-aware dashboard summaries and searchable discrepancy details.
- Evidence-backed AI explanations for reconciliation outcomes.
- Audit-friendly status and processing history.

### Out of scope for the initial version

- Initiating payments, refunds, payouts, or bank transfers.
- Automatically changing source-system records.
- Cross-customer reporting or data visibility.
- LLM-driven matching or mutation of financial records.
- Complex accounting, tax, or general-ledger functionality unless explicitly required later.

## Core principles

1. **Tenant isolation:** every customer-owned record is scoped by a verified tenant identity.
2. **Traceability:** original uploads and raw field values remain available for audit and investigation.
3. **Determinism:** the reconciliation engine uses explicit rules and produces the same result for the same inputs and rule version.
4. **Currency safety:** amounts are never aggregated across currencies without an explicit conversion policy.
5. **Reproducibility:** each reconciliation run references immutable upload inputs and a rule version.
6. **Explainable AI:** AI can summarize evidence already computed by the system; it cannot invent evidence or decide financial outcomes.
7. **Incremental delivery:** begin with a modular monolith and evolve only when real constraints require it.

## Technology

| Layer | Choice |
|---|---|
| Web application | Next.js, React, TypeScript |
| UI | Tailwind CSS and shadcn/ui |
| Charts / tables | Recharts, TanStack Table |
| Authentication | Own: bcrypt password hashing, JWT in an httpOnly cookie |
| API | Python and FastAPI |
| Database | PostgreSQL |
| ORM and migrations | SQLAlchemy 2.x and Alembic |
| Validation | Pydantic |
| AI explanations | Gemini API (backend only, structured output, deterministic fallback) |
| Frontend hosting | Vercel |
| API hosting | Railway |
| Database hosting | Neon |
| Testing | Pytest |

The design document records the rationale for each choice and what was deliberately left out.

## High-level workflow

1. A user signs in and receives an authenticated session.
2. The API independently verifies the identity token and derives the tenant from trusted claims.
3. The user uploads the order and payment source files.
4. The API stores upload metadata and the original files in private storage.
5. The ingestion pipeline validates, parses, and normalizes the rows into tenant-scoped relational records.
6. The user starts a reconciliation run referencing selected uploads and a versioned rule set.
7. The deterministic engine matches records and records exceptions with explicit evidence.
8. Dashboard APIs aggregate the stored run results by currency and discrepancy type.
9. An optional explanation endpoint may describe a selected result using its stored evidence.

## Repository guide

- `README.md` — product overview, scope, principles, proposed stack, and workflow.
- `design.md` — architecture, data model, ingestion, reconciliation, security, API, testing, and open decisions.

## Development status

This repository currently contains planning documentation only. No application code or source datasets are included yet.

## Working agreement

Discuss requirements, edge cases, architecture, and data contracts before implementation. Treat proposed decisions as reviewable until they are explicitly agreed. Keep source datasets and assignment materials out of this repository unless separately and deliberately approved.
