"""SQLAlchemy models. Constraints mirror design.md section 4.

Ownership lives only on `imports.user_id`; child tables reach a user through
their import, so ownership cannot drift out of sync.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.reconcile.rules import CATEGORIES, DIRECTIONS, SEVERITY_RANK

Money = Numeric(14, 2)


def _in(column: str, values) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


CURRENCY_CHECK = "currency ~ '^[A-Z]{3}$'"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="ck_users_email_lower"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Import(Base):
    __tablename__ = "imports"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "orders_sha256", "payments_sha256", "rules_version", name="uq_imports_user_files_rules"
        ),
        Index("ix_imports_user_created", "user_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    orders_filename: Mapped[str] = mapped_column(Text)
    payments_filename: Mapped[str] = mapped_column(Text)
    orders_sha256: Mapped[str] = mapped_column(Text)
    payments_sha256: Mapped[str] = mapped_column(Text)
    orders_raw: Mapped[str] = mapped_column(Text, deferred=True)
    payments_raw: Mapped[str] = mapped_column(Text, deferred=True)
    rules_version: Mapped[str] = mapped_column(Text)
    summary: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("import_id", "order_key", name="uq_orders_import_key"),
        CheckConstraint(CURRENCY_CHECK, name="ck_orders_currency"),
        CheckConstraint(_in("status", ("completed", "cancelled", "refunded")), name="ck_orders_status"),
        CheckConstraint(
            "net_amount >= 0 AND (gross_amount IS NULL OR gross_amount >= 0) AND (discount IS NULL OR discount >= 0)",
            name="ck_orders_money",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    import_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("imports.id", ondelete="CASCADE"))
    row_numbers: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    order_id_raw: Mapped[str] = mapped_column(Text)
    order_key: Mapped[str] = mapped_column(Text)
    order_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    customer_email: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text)
    gross_amount: Mapped[Decimal | None] = mapped_column(Money)
    discount: Mapped[Decimal | None] = mapped_column(Money)
    net_amount: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSONB)
    dq_flags: Mapped[list[str]] = mapped_column(ARRAY(Text))


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("import_id", "transaction_ref", name="uq_payments_import_ref"),
        Index("ix_payments_import_order_key", "import_id", "order_key"),
        CheckConstraint(CURRENCY_CHECK, name="ck_payments_currency"),
        CheckConstraint(_in("type", ("charge", "refund")), name="ck_payments_type"),
        CheckConstraint(_in("status", ("settled", "pending", "failed")), name="ck_payments_status"),
        CheckConstraint(
            "amount >= 0 AND (fee IS NULL OR fee >= 0) AND (net_settled IS NULL OR net_settled >= 0)",
            name="ck_payments_money",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    import_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("imports.id", ondelete="CASCADE"))
    row_numbers: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    transaction_ref: Mapped[str] = mapped_column(Text)
    order_reference_raw: Mapped[str | None] = mapped_column(Text)
    order_key: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    currency: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Money)
    fee: Mapped[Decimal | None] = mapped_column(Money)
    net_settled: Mapped[Decimal | None] = mapped_column(Money)
    type: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSONB)
    dq_flags: Mapped[list[str]] = mapped_column(ARRAY(Text))


class ReconciliationItem(Base):
    __tablename__ = "reconciliation_items"
    __table_args__ = (
        UniqueConstraint("import_id", "item_key", name="uq_items_import_key"),
        UniqueConstraint("id", "import_id", name="uq_items_id_import"),
        Index("ix_items_priority", "import_id", "severity_rank", text("amount_at_risk DESC")),
        Index("ix_items_category", "import_id", "category"),
        CheckConstraint(_in("category", CATEGORIES), name="ck_items_category"),
        CheckConstraint(_in("severity", SEVERITY_RANK), name="ck_items_severity"),
        CheckConstraint(f"risk_direction IS NULL OR {_in('risk_direction', DIRECTIONS)}", name="ck_items_direction"),
        CheckConstraint(f"currency IS NULL OR {CURRENCY_CHECK}", name="ck_items_currency"),
        CheckConstraint("currency IS NOT NULL OR category = 'NEEDS_REVIEW'", name="ck_items_currency_known"),
        CheckConstraint(
            "(expected_amount IS NULL OR expected_amount >= 0) AND reconciled_value >= 0 "
            "AND disputed_value >= 0 AND amount_at_risk >= 0",
            name="ck_items_money",
        ),
        CheckConstraint("reconciled_value = 0 OR disputed_value = 0", name="ck_items_one_bucket"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    import_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("imports.id", ondelete="CASCADE"))
    item_key: Mapped[str] = mapped_column(Text)
    order_key: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    severity_rank: Mapped[int] = mapped_column(SmallInteger)
    currency: Mapped[str | None] = mapped_column(Text)
    expected_amount: Mapped[Decimal | None] = mapped_column(Money)
    actual_amount: Mapped[Decimal | None] = mapped_column(Money)
    difference: Mapped[Decimal | None] = mapped_column(Money)
    reconciled_value: Mapped[Decimal] = mapped_column(Money)
    disputed_value: Mapped[Decimal] = mapped_column(Money)
    amount_at_risk: Mapped[Decimal] = mapped_column(Money)
    risk_direction: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    dq_flags: Mapped[list[str]] = mapped_column(ARRAY(Text))
    evidence: Mapped[dict] = mapped_column(JSONB)


class Explanation(Base):
    __tablename__ = "explanations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "import_id"],
            ["reconciliation_items.id", "reconciliation_items.import_id"],
            ondelete="CASCADE",
            name="fk_explanations_item_same_import",
        ),
        UniqueConstraint(
            "import_id", "item_id", "prompt_version", "model",
            name="uq_explanations_cache", postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    import_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("imports.id", ondelete="CASCADE"))
    item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    prompt_version: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    output: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
