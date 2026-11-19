"""Import write path (design section 8).

Everything is parsed, validated and reconciled in memory first. The database
work is one transaction that either stores the whole import or nothing.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import Import, Order, Payment, ReconciliationItem
from app.ingestion import parse_orders, parse_payments
from app.ingestion.models import FileRejected, ParsedFile
from app.reconcile import RULES_VERSION, compute_metrics, reconcile_files
from app.reconcile.models import ResultItem


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    data: bytes


@dataclass(frozen=True)
class ImportOutcome:
    import_id: uuid.UUID
    created: bool


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _order_rows(import_id: uuid.UUID, parsed: ParsedFile) -> list[dict]:
    return [
        {
            "import_id": import_id, "row_numbers": list(o.row_numbers), "order_id_raw": o.order_id_raw,
            "order_key": o.order_key, "order_date": o.order_date, "customer_email": o.customer_email,
            "currency": o.currency, "gross_amount": o.gross_amount, "discount": o.discount,
            "net_amount": o.net_amount, "status": o.status, "raw": o.raw, "dq_flags": list(o.dq_flags),
        }
        for o in parsed.records
    ]


def _payment_rows(import_id: uuid.UUID, parsed: ParsedFile) -> list[dict]:
    return [
        {
            "import_id": import_id, "row_numbers": list(p.row_numbers), "transaction_ref": p.transaction_ref,
            "order_reference_raw": p.order_reference_raw, "order_key": p.order_key,
            "processed_at": p.processed_at, "currency": p.currency, "amount": p.amount, "fee": p.fee,
            "net_settled": p.net_settled, "type": p.type, "status": p.status, "raw": p.raw,
            "dq_flags": list(p.dq_flags),
        }
        for p in parsed.records
    ]


def _item_rows(import_id: uuid.UUID, items: list[ResultItem]) -> list[dict]:
    return [
        {
            "id": uuid.uuid4(), "import_id": import_id, "item_key": i.item_key, "order_key": i.order_key,
            "category": i.category, "severity": i.severity, "severity_rank": i.severity_rank,
            "currency": i.currency, "expected_amount": i.expected_amount, "actual_amount": i.actual_amount,
            "difference": i.difference, "reconciled_value": i.reconciled_value,
            "disputed_value": i.disputed_value, "amount_at_risk": i.amount_at_risk,
            "risk_direction": i.risk_direction, "reason": i.reason, "dq_flags": list(i.dq_flags),
            "evidence": {**i.evidence, "transaction_refs": list(i.transaction_refs)},
        }
        for i in items
    ]


def _bulk(db: Session, model, rows: list[dict]) -> None:
    if rows:
        db.execute(insert(model), rows)


def insert_children(db: Session, import_id: uuid.UUID, orders: ParsedFile, payments: ParsedFile, items) -> None:
    _bulk(db, Order, _order_rows(import_id, orders))
    _bulk(db, Payment, _payment_rows(import_id, payments))
    _bulk(db, ReconciliationItem, _item_rows(import_id, items))


def create_import(db: Session, user_id: uuid.UUID, orders_file: UploadedFile, payments_file: UploadedFile) -> ImportOutcome:
    # 1. Pure work in memory. FileRejected propagates before any database access.
    orders = parse_orders(orders_file.data)
    payments = parse_payments(payments_file.data)
    items = reconcile_files(orders, payments)
    summary = {
        "metrics": compute_metrics(items, orders, payments),
        "validation": {"orders": orders.report(), "payments": payments.report()},
    }

    # 2. One transaction. ON CONFLICT closes the check-then-insert race: a
    # concurrent identical upload waits on the unique index, then sees ours.
    try:
        new_id = db.scalar(
            pg_insert(Import)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                orders_filename=orders_file.filename,
                payments_filename=payments_file.filename,
                orders_sha256=_sha256(orders_file.data),
                payments_sha256=_sha256(payments_file.data),
                orders_raw=orders_file.data.decode("utf-8-sig"),
                payments_raw=payments_file.data.decode("utf-8-sig"),
                rules_version=RULES_VERSION,
                summary=summary,
            )
            .on_conflict_do_nothing(constraint="uq_imports_user_files_rules")
            .returning(Import.id)
        )
        if new_id is None:
            db.rollback()
            existing = db.scalar(
                select(Import.id).where(
                    Import.user_id == user_id,
                    Import.orders_sha256 == _sha256(orders_file.data),
                    Import.payments_sha256 == _sha256(payments_file.data),
                    Import.rules_version == RULES_VERSION,
                )
            )
            return ImportOutcome(existing, created=False)

        insert_children(db, new_id, orders, payments, items)
        db.commit()
        return ImportOutcome(new_id, created=True)
    except Exception:
        db.rollback()
        raise


__all__ = ["FileRejected", "ImportOutcome", "UploadedFile", "create_import"]
