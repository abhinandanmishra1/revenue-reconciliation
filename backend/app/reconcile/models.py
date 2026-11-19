from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ResultItem:
    item_key: str
    order_key: str | None
    category: str
    severity: str
    severity_rank: int
    currency: str | None
    expected_amount: Decimal | None
    # Signed: collected cash can be negative when refunds exceed charges.
    actual_amount: Decimal | None
    difference: Decimal | None
    reconciled_value: Decimal
    disputed_value: Decimal
    amount_at_risk: Decimal
    risk_direction: str | None
    reason: str
    dq_flags: tuple[str, ...]
    evidence: dict
    # Business keys of the source records that belong to this item.
    order_keys: tuple[str, ...]
    transaction_refs: tuple[str, ...]

    @property
    def sort_key(self) -> tuple:
        return (self.severity_rank, -self.amount_at_risk, self.order_key or "", self.item_key)
