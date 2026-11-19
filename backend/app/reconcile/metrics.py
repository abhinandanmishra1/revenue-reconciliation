"""Dashboard metrics: plain per-currency sums over result items.

Never sums across currencies. Every figure is a sum over items, so a KPI click
can filter the table to exactly the rows behind it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal

from app.ingestion.models import ParsedFile

from .models import ResultItem
from .rules import RECONCILED_CATEGORIES

ZERO = Decimal("0.00")
UNKNOWN = "unknown"


def _bucket() -> dict:
    return {
        "orders": 0,
        "reconciled_value": ZERO,
        "reconciled_orders": 0,
        "disputed_value": ZERO,
        "at_risk": {"under_collected": ZERO, "under_collected_pending": ZERO, "over_collected": ZERO, "unverified": ZERO},
        "settled_charge_value": ZERO,
        "categories": defaultdict(lambda: {"count": 0, "amount_at_risk": ZERO}),
    }


def compute_metrics(items: Iterable[ResultItem], orders: ParsedFile, payments: ParsedFile) -> dict:
    items = list(items)
    by_currency: dict[str, dict] = defaultdict(_bucket)

    for item in items:
        b = by_currency[item.currency or UNKNOWN]
        if item.order_keys:
            b["orders"] += 1
        b["reconciled_value"] += item.reconciled_value
        b["disputed_value"] += item.disputed_value
        if item.category in RECONCILED_CATEGORIES:
            b["reconciled_orders"] += 1
        if item.risk_direction:
            b["at_risk"][item.risk_direction] += item.amount_at_risk
            if item.category == "PAYMENT_PENDING" and item.risk_direction == "under_collected":
                b["at_risk"]["under_collected_pending"] += item.amount_at_risk
        cat = b["categories"][item.category]
        cat["count"] += 1
        cat["amount_at_risk"] += item.amount_at_risk

    events_by_type_status: dict[str, int] = defaultdict(int)
    for p in payments.records:
        events_by_type_status[f"{p.type}_{p.status}"] += 1
        if p.type == "charge" and p.status == "settled":
            by_currency[p.currency]["settled_charge_value"] += p.amount

    def ser(value):
        if isinstance(value, Decimal):
            return f"{value:.2f}"
        if isinstance(value, dict):
            return {k: ser(v) for k, v in sorted(value.items())}
        return value

    category_counts: dict[str, int] = defaultdict(int)
    for item in items:
        category_counts[item.category] += 1

    return {
        "totals": {
            "unique_orders": len(orders.records),
            "order_rows": orders.row_count,
            "payment_events": len(payments.records),
            "payment_rows": payments.row_count,
            "payment_events_by_type_status": dict(sorted(events_by_type_status.items())),
            "items": len(items),
            "needs_review": category_counts.get("NEEDS_REVIEW", 0),
            "category_counts": dict(sorted(category_counts.items())),
        },
        "by_currency": {cur: ser(b) for cur, b in sorted(by_currency.items())},
    }
