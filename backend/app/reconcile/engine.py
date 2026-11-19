"""Deterministic reconciliation engine: (orders, payments, rules) -> results.

Pure: no database, HTTP, LLM, clock or randomness. Inputs are grouped into
dicts and every output is sorted by a stable key, so row order never matters.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal

from app.ingestion.models import OrderRecord, ParsedFile, PaymentRecord

from .models import ResultItem
from .rules import (
    AMOUNT_TOLERANCE,
    CATEGORY_SEVERITY,
    EARLY_BEFORE,
    LATE_AFTER,
    RECONCILED_CATEGORIES,
    RULE_NUMBER,
    RULES_VERSION,
    SEVERITY_RANK,
)

ZERO = Decimal("0.00")
UNDER, OVER, UNVERIFIED = "under_collected", "over_collected", "unverified"


# ------------------------------------------------------------------ helpers


def _sum(values: Iterable[Decimal]) -> Decimal:
    return sum(values, ZERO)


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def _event_evidence(p: PaymentRecord) -> dict:
    return {
        "transaction_ref": p.transaction_ref,
        "row_numbers": list(p.row_numbers),
        "type": p.type,
        "status": p.status,
        "currency": p.currency,
        "amount": _money(p.amount),
        "fee": _money(p.fee),
        "net_settled": _money(p.net_settled),
        "processed_at": p.processed_at.isoformat() if p.processed_at else None,
        "order_reference_raw": p.order_reference_raw,
        "dq_flags": list(p.dq_flags),
    }


def _order_evidence(o: OrderRecord) -> dict:
    return {
        "order_id_raw": o.order_id_raw,
        "row_numbers": list(o.row_numbers),
        "status": o.status,
        "currency": o.currency,
        "gross_amount": _money(o.gross_amount),
        "discount": _money(o.discount),
        "net_amount": _money(o.net_amount),
        "order_date": o.order_date.isoformat() if o.order_date else None,
        "customer_email": o.customer_email,
        "dq_flags": list(o.dq_flags),
    }


def _events_sorted(events: Iterable[PaymentRecord]) -> list[PaymentRecord]:
    return sorted(events, key=lambda p: (p.processed_at is None, p.processed_at or 0, p.transaction_ref))


def _item(
    *,
    item_key: str,
    order_key: str | None,
    category: str,
    currency: str | None,
    expected: Decimal | None,
    actual: Decimal | None,
    reconciled: Decimal,
    disputed: Decimal,
    at_risk: Decimal,
    direction: str | None,
    reason: str,
    flags: Iterable[str],
    evidence: dict,
    order_keys: Iterable[str],
    transaction_refs: Iterable[str],
) -> ResultItem:
    severity = CATEGORY_SEVERITY[category]
    difference = None if expected is None or actual is None else actual - expected
    evidence = {
        "rule": {"number": RULE_NUMBER[category], "category": category, "rules_version": RULES_VERSION},
        "tolerance": _money(AMOUNT_TOLERANCE),
        **evidence,
    }
    return ResultItem(
        item_key=item_key,
        order_key=order_key,
        category=category,
        severity=severity,
        severity_rank=SEVERITY_RANK[severity],
        currency=currency,
        expected_amount=expected,
        actual_amount=actual,
        difference=difference,
        reconciled_value=reconciled,
        disputed_value=disputed,
        amount_at_risk=at_risk,
        risk_direction=direction,
        reason=reason,
        dq_flags=tuple(sorted(set(flags))),
        evidence=evidence,
        order_keys=tuple(sorted(order_keys)),
        transaction_refs=tuple(sorted(transaction_refs)),
    )


# ------------------------------------------------------------------ order items


def _timing_breaches(order: OrderRecord, charges: list[PaymentRecord]) -> list[dict]:
    if order.order_date is None:
        return []
    breaches = []
    for p in charges:
        if p.processed_at is None:
            continue
        delta = p.processed_at - order.order_date
        if delta > LATE_AFTER or delta < -EARLY_BEFORE:
            breaches.append(
                {
                    "transaction_ref": p.transaction_ref,
                    "hours_after_order": round(delta.total_seconds() / 3600, 2),
                }
            )
    return breaches


def _classify_order(order: OrderRecord, events: list[PaymentRecord]) -> ResultItem:
    cur = order.currency
    net = order.net_amount
    expected = net if order.status == "completed" else ZERO

    foreign = [p for p in events if p.currency != cur and p.status in ("settled", "pending")]
    same = [p for p in events if p.currency == cur]
    settled_charges = [p for p in same if p.type == "charge" and p.status == "settled"]
    settled_refunds = [p for p in same if p.type == "refund" and p.status == "settled"]
    pending_charges = [p for p in same if p.type == "charge" and p.status == "pending"]
    failed_charges = [p for p in same if p.type == "charge" and p.status == "failed"]

    charged = _sum(p.amount for p in settled_charges)
    refunded = _sum(p.amount for p in settled_refunds)
    pending = _sum(p.amount for p in pending_charges)
    collected = charged - refunded
    difference = collected - expected
    excess_refund = max(refunded - charged, ZERO)
    tol = AMOUNT_TOLERANCE
    breaches = _timing_breaches(order, settled_charges)

    flags: set[str] = set(order.dq_flags)
    for p in events:
        flags.update(p.dq_flags)
    if pending_charges and settled_charges:
        flags.add("pending_alongside_settled")

    s = order.status
    if foreign:
        category, at_risk, direction = "CURRENCY_MISMATCH", net, UNVERIFIED
        codes = ", ".join(sorted({p.currency for p in foreign}))
        reason = f"Order is in {cur} but has settled or pending payments in {codes}; amounts cannot be compared."
    elif expected > 0 and not same:
        category, at_risk, direction = "MISSING_PAYMENT", expected, UNDER
        reason = f"Completed order for {expected} {cur} has no payment events."
    elif expected > 0 and not settled_charges and failed_charges and not pending_charges:
        category, at_risk, direction = "PAYMENT_FAILED", expected, UNDER
        reason = f"Every charge attempt for this {expected} {cur} order failed; nothing was collected."
    elif not settled_charges and pending_charges:
        category, at_risk = "PAYMENT_PENDING", pending
        direction = UNDER if s == "completed" else OVER
        reason = f"No settled charge yet; {pending} {cur} is still pending on a {s} order."
    elif s == "cancelled" and collected > tol:
        category, at_risk, direction = "CANCELLED_BUT_CHARGED", collected, OVER
        reason = f"Order was cancelled but {collected} {cur} was collected."
    elif s == "refunded" and collected > tol:
        category, at_risk, direction = "REFUND_INCOMPLETE", collected, OVER
        reason = f"Order is marked refunded but {collected} {cur} is still held (charged {charged}, refunded {refunded})."
    elif collected < -tol:
        category, at_risk, direction = "OVER_REFUNDED", -difference, UNDER
        reason = f"Refunds exceed charges by {excess_refund} {cur} (charged {charged}, refunded {refunded})."
    elif s == "completed" and settled_refunds and difference < -tol:
        category, at_risk, direction = "REFUND_NOT_IN_ORDERS", -difference, UNDER
        reason = (
            f"{refunded} {cur} was refunded in the processor but the order still shows "
            f"completed for {net}; revenue is overstated by {-difference}."
        )
    elif difference > tol and sum(1 for p in settled_charges if abs(p.amount - expected) <= tol) >= 2:
        category, at_risk, direction = "DUPLICATE_CHARGE", difference, OVER
        reason = f"{len(settled_charges)} settled charges for a {expected} {cur} order; {difference} {cur} collected twice."
    elif difference > tol:
        category, at_risk, direction = "OVERCHARGED", difference, OVER
        reason = f"Expected {expected} {cur} but collected {collected}; overcharged by {difference}."
    elif difference < -tol:
        category, at_risk, direction = "UNDERCHARGED", -difference, UNDER
        reason = f"Expected {expected} {cur} but collected {collected}; short by {-difference}."
    elif difference != 0:
        category, at_risk, direction = "WITHIN_TOLERANCE", ZERO, None
        reason = f"Collected {collected} {cur} vs expected {expected}; difference {difference} is within {tol}."
    elif breaches:
        category, at_risk, direction = "LATE_CHARGE", ZERO, None
        reason = "Amount agrees, but a charge settled outside the expected window (more than 7 days after, or 1 hour before, the order)."
    else:
        category, at_risk, direction = "MATCHED", ZERO, None
        reason = f"Collected {collected} {cur} matches the expected {expected}."

    if breaches and category != "LATE_CHARGE":
        flags.add("timing_breach")

    reconciled = expected if category in RECONCILED_CATEGORIES else ZERO
    disputed = ZERO if category in RECONCILED_CATEGORIES else net

    return _item(
        item_key=f"O:{order.order_key}",
        order_key=order.order_key,
        category=category,
        currency=cur,
        expected=expected,
        actual=collected,
        reconciled=reconciled,
        disputed=disputed,
        at_risk=at_risk,
        direction=direction,
        reason=reason,
        flags=flags,
        evidence={
            "order": _order_evidence(order),
            "events": [_event_evidence(p) for p in _events_sorted(events)],
            "expected": _money(expected),
            "settled_charges": _money(charged),
            "settled_refunds": _money(refunded),
            "pending_charges": _money(pending),
            "collected": _money(collected),
            "difference": _money(difference),
            "excess_refund": _money(excess_refund),
            "timing_breaches": breaches,
        },
        order_keys=[order.order_key],
        transaction_refs=[p.transaction_ref for p in events],
    )


# ------------------------------------------------------------------ review / orphan items


def _review_item(
    item_key: str,
    order_key: str | None,
    order: OrderRecord | None,
    events: list[PaymentRecord],
    reasons: Iterable[str],
) -> ResultItem:
    reasons = sorted(set(reasons))
    if order is not None:
        currency, disputed = order.currency, order.net_amount
    else:
        settled = [p for p in events if p.status == "settled"]
        currencies = {p.currency for p in settled}
        if len(currencies) == 1:
            currency, disputed = currencies.pop(), _sum(p.amount for p in settled)
        else:
            currency, disputed = None, ZERO

    flags: set[str] = set(order.dq_flags if order else ())
    for p in events:
        flags.update(p.dq_flags)

    return _item(
        item_key=item_key,
        order_key=order_key,
        category="NEEDS_REVIEW",
        currency=currency,
        expected=None,
        actual=None,
        reconciled=ZERO,
        disputed=disputed,
        at_risk=ZERO,
        direction=UNVERIFIED,
        reason="Evidence is incomplete: " + "; ".join(reasons) + ".",
        flags=flags,
        evidence={
            "order": _order_evidence(order) if order else None,
            "events": [_event_evidence(p) for p in _events_sorted(events)],
            "review_reasons": reasons,
        },
        order_keys=[order.order_key] if order else [],
        transaction_refs=[p.transaction_ref for p in events],
    )


def _orphan_item(item_key: str, order_key: str | None, currency: str, events: list[PaymentRecord]) -> ResultItem:
    settled = [p for p in events if p.status == "settled"]
    charged = _sum(p.amount for p in settled if p.type == "charge")
    refunded = _sum(p.amount for p in settled if p.type == "refund")
    net = charged - refunded
    direction = OVER if net > 0 else UNDER if net < 0 else None
    flags: set[str] = set()
    for p in events:
        flags.update(p.dq_flags)
    target = f"order reference {order_key}" if order_key else "a blank order reference"
    return _item(
        item_key=item_key,
        order_key=order_key,
        category="ORPHAN_PAYMENT",
        currency=currency,
        expected=ZERO,
        actual=net,
        reconciled=ZERO,
        disputed=charged + refunded,
        at_risk=abs(net),
        direction=direction,
        reason=f"Payments for {target} have no matching order; net settled {net} {currency}.",
        flags=flags,
        evidence={
            "order": None,
            "events": [_event_evidence(p) for p in _events_sorted(events)],
            "settled_charges": _money(charged),
            "settled_refunds": _money(refunded),
            "net_settled": _money(net),
        },
        order_keys=[],
        transaction_refs=[p.transaction_ref for p in events],
    )


# ------------------------------------------------------------------ public API


def reconcile(
    orders: Iterable[OrderRecord],
    payments: Iterable[PaymentRecord],
    review_keys: Mapping[str, Iterable[str]] | None = None,
    review_refs: Mapping[str, Iterable[str]] | None = None,
) -> list[ResultItem]:
    """Classify every order key, orphan key + currency and blank-reference payment.

    review_keys: order key -> reasons it needs review (id conflicts, linked rejections).
    review_refs: transaction ref -> reasons, for conflicts with no order reference.
    """
    review_keys = {k: list(v) for k, v in (review_keys or {}).items()}
    review_refs = {k: list(v) for k, v in (review_refs or {}).items()}

    by_key: dict[str, OrderRecord] = {o.order_key: o for o in orders}
    events_by_key: dict[str, list[PaymentRecord]] = defaultdict(list)
    blank_ref: list[PaymentRecord] = []
    for p in payments:
        if p.order_key is None:
            blank_ref.append(p)
        else:
            events_by_key[p.order_key].append(p)

    items: list[ResultItem] = []
    for key in sorted(set(by_key) | set(events_by_key) | set(review_keys)):
        order = by_key.get(key)
        events = events_by_key.get(key, [])
        if key in review_keys:
            items.append(_review_item(f"O:{key}", key, order, events, review_keys[key]))
        elif order is not None:
            items.append(_classify_order(order, events))
        else:
            by_currency: dict[str, list[PaymentRecord]] = defaultdict(list)
            for p in events:
                by_currency[p.currency].append(p)
            for cur in sorted(by_currency):
                items.append(_orphan_item(f"X:{key}:{cur}", key, cur, by_currency[cur]))

    for p in blank_ref:
        items.append(_orphan_item(f"T:{p.transaction_ref}", None, p.currency, [p]))
    for ref in sorted(review_refs):
        items.append(_review_item(f"T:{ref}", None, None, [], review_refs[ref]))

    return sorted(items, key=lambda i: i.sort_key)


def review_signals(orders: ParsedFile, payments: ParsedFile) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Collect NEEDS_REVIEW reasons from id conflicts and linked rejected rows."""
    keys: dict[str, list[str]] = defaultdict(list)
    refs: dict[str, list[str]] = defaultdict(list)
    for c in orders.conflicts:
        keys[c.key].append(f"order id conflict in orders rows {', '.join(map(str, c.row_numbers))}")
    for c in payments.conflicts:
        reason = f"transaction {c.key} conflicts across payments rows {', '.join(map(str, c.row_numbers))}"
        if c.order_keys:
            for k in c.order_keys:
                keys[k].append(reason)
        else:
            refs[c.key].append(reason)
    for parsed in (orders, payments):
        for r in parsed.rejected:
            if r.linked_order_key:
                keys[r.linked_order_key].append(f"{parsed.kind} row {r.row_number} was rejected ({'; '.join(r.reasons)})")
    return dict(keys), dict(refs)


def reconcile_files(orders: ParsedFile, payments: ParsedFile) -> list[ResultItem]:
    keys, refs = review_signals(orders, payments)
    return reconcile(orders.records, payments.records, keys, refs)
