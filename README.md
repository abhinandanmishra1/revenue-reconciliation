# Revenue Reconciliation Dashboard

A per-user workspace for importing order and payment data, reconciling financial records, investigating discrepancies, and understanding settlement outcomes. Each user's imports and results are private to that user; there are no shared or organisation workspaces.

The product is designed around a simple principle: **reconciliation decisions must be deterministic, explainable, reproducible, and isolated per user**. AI may help explain a result, but it never decides whether two financial records match.

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

- Email + password accounts; each user sees only their own data.
- Uploading an orders CSV and a payments CSV together as one import.
- Validation reports with row-level reasons, and import history.
- Parsing and normalizing source records while preserving raw values and the original files.
- Reproducible reconciliation: each import records its file hashes and rules version.
- Deterministic matching and discrepancy classification with stored evidence.
- Currency-aware dashboard summaries, a priority-ordered and searchable discrepancy table, and drill-down to source rows.
- AI explanations of individual discrepancies and of a whole import, with a deterministic fallback.

### Out of scope for the initial version

- Initiating payments, refunds, payouts, or bank transfers.
- Automatically changing source-system records.
- Organisations, shared workspaces, and roles.
- FX conversion between currencies.
- LLM-driven matching or mutation of financial records.
- Complex accounting, tax, or general-ledger functionality.

## Core principles

1. **Data isolation:** every import belongs to one user; all data is reached only through an import owned by the requesting user.
2. **Traceability:** original uploads and raw field values remain available for audit and investigation.
3. **Determinism:** the reconciliation engine uses explicit rules and produces the same result for the same inputs and rules version, regardless of row order.
4. **Currency safety:** amounts are never aggregated across currencies.
5. **Idempotency:** repeating a request (signup, upload, explain) never repeats its side effects; a failed request leaves no partial state.
6. **Explainable AI:** AI can summarize evidence already computed by the system; it cannot invent evidence or decide financial outcomes.
7. **Incremental delivery:** a modular monolith, extended only when real constraints require it.

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

1. A user signs up or logs in and receives an httpOnly session cookie.
2. The user uploads the orders file and the payments file together.
3. The API validates and parses both files in memory, then reconciles them with the deterministic engine.
4. In one transaction, it stores the import (original files, hashes, rules version), the parsed rows, every reconciliation result, and the summary. Uploading the same file pair again returns the existing import.
5. The dashboard shows headline figures by currency, a breakdown by discrepancy type, and a priority-ordered table that drills down to source rows.
6. On request, the API asks the LLM to explain a result or the whole import from the stored evidence, with a deterministic fallback if the LLM fails.

## Repository guide

- `README.md` — product overview, scope, principles, stack, and workflow.
- `design.md` — goal, architecture, key decisions, data model, ingestion, reconciliation rules, metrics, security, LLM approach, API, UI, testing, and configuration.
