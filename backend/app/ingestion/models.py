"""Typed records produced by the ingestion parser.

Everything here is immutable and free of I/O so the engine can be tested
directly on these values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

FileKind = Literal["orders", "payments"]

ORDER_STATUSES = frozenset({"completed", "cancelled", "refunded"})
PAYMENT_TYPES = frozenset({"charge", "refund"})
PAYMENT_STATUSES = frozenset({"settled", "pending", "failed"})
# ISO 4217 codes with two minor units. Amounts are validated to 2 dp.
CURRENCIES = frozenset({"USD", "EUR", "GBP", "INR", "AUD", "CAD", "SGD"})

MAX_FILE_BYTES = 2 * 1024 * 1024


class FileRejected(Exception):
    """The whole file is unusable. Nothing from the upload may be saved."""

    def __init__(self, file: FileKind, code: str, message: str) -> None:
        super().__init__(message)
        self.file = file
        self.code = code
        self.message = message


@dataclass(frozen=True)
class OrderRecord:
    row_numbers: tuple[int, ...]
    order_id_raw: str
    order_key: str
    order_date: datetime | None
    customer_email: str | None
    currency: str
    gross_amount: Decimal | None
    discount: Decimal | None
    net_amount: Decimal
    status: str
    raw: dict[str, str]
    dq_flags: tuple[str, ...]


@dataclass(frozen=True)
class PaymentRecord:
    row_numbers: tuple[int, ...]
    transaction_ref: str
    order_reference_raw: str | None
    order_key: str | None
    processed_at: datetime | None
    currency: str
    amount: Decimal
    fee: Decimal | None
    net_settled: Decimal | None
    type: str
    status: str
    raw: dict[str, str]
    dq_flags: tuple[str, ...]


@dataclass(frozen=True)
class RejectedRow:
    row_number: int
    reasons: tuple[str, ...]
    fields: tuple[str, ...]
    # Order key this rejection is attached to, or None when it stays report-only.
    linked_order_key: str | None
    raw: dict[str, str]


@dataclass(frozen=True)
class IdConflict:
    key: str
    row_numbers: tuple[int, ...]
    # Order keys that become NEEDS_REVIEW because of this conflict.
    order_keys: tuple[str, ...]


@dataclass(frozen=True)
class ParsedFile:
    kind: FileKind
    headers: tuple[str, ...]
    row_count: int
    records: tuple  # tuple[OrderRecord, ...] | tuple[PaymentRecord, ...]
    rejected: tuple[RejectedRow, ...] = field(default_factory=tuple)
    conflicts: tuple[IdConflict, ...] = field(default_factory=tuple)
    duplicate_rows_collapsed: int = 0

    def report(self) -> dict:
        """JSON-safe validation report for this file."""
        return {
            "file": self.kind,
            "row_count": self.row_count,
            "accepted": len(self.records),
            "rejected_count": len(self.rejected),
            "duplicate_rows_collapsed": self.duplicate_rows_collapsed,
            "rejected": [
                {
                    "row_number": r.row_number,
                    "reasons": list(r.reasons),
                    "linked_order_key": r.linked_order_key,
                }
                for r in self.rejected
            ],
            "id_conflicts": [
                {"key": c.key, "row_numbers": list(c.row_numbers), "order_keys": list(c.order_keys)}
                for c in self.conflicts
            ],
        }
