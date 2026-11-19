from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.ingestion import parse_orders, parse_payments
from app.ingestion.models import OrderRecord, PaymentRecord
from app.reconcile import reconcile, reconcile_files

D = Decimal
T0 = datetime(2024, 3, 1, 12, 0)
_ref_counter = iter(range(1, 10_000))


def order(key="O1", net="100.00", status="completed", currency="USD", date=T0) -> OrderRecord:
    return OrderRecord(
        row_numbers=(2,), order_id_raw=key, order_key=key, order_date=date, customer_email="c@x.io",
        currency=currency, gross_amount=D(net), discount=D("0"), net_amount=D(net), status=status,
        raw={}, dq_flags=(),
    )


def pay(amount, type="charge", status="settled", key="O1", currency="USD", at=T0 + timedelta(minutes=5), ref=None):
    return PaymentRecord(
        row_numbers=(next(_ref_counter),), transaction_ref=ref or f"T{next(_ref_counter)}",
        order_reference_raw=key, order_key=key, processed_at=at, currency=currency, amount=D(amount),
        fee=D("0"), net_settled=D(amount), type=type, status=status, raw={}, dq_flags=(),
    )


def one(orders, payments):
    (item,) = reconcile(orders, payments)
    return item


# ------------------------------------------------------------------ tolerance


@pytest.mark.parametrize(
    ("charged", "category"),
    [("100.05", "WITHIN_TOLERANCE"), ("99.95", "WITHIN_TOLERANCE"), ("100.06", "OVERCHARGED"), ("99.94", "UNDERCHARGED")],
)
def test_tolerance_edges(charged, category):
    item = one([order()], [pay(charged)])
    assert item.category == category


def test_within_tolerance_is_reconciled_with_no_risk():
    item = one([order()], [pay("100.05")])
    assert (item.reconciled_value, item.disputed_value, item.amount_at_risk) == (D("100.00"), 0, 0)


# ------------------------------------------------------------------ correct cases


def test_split_payment_matches():
    assert one([order()], [pay("60"), pay("40")]).category == "MATCHED"


def test_duplicate_charge_later_refunded_matches():
    assert one([order()], [pay("100"), pay("100"), pay("100", type="refund")]).category == "MATCHED"


def test_overcharge_corrected_by_refund_matches():
    assert one([order()], [pay("125"), pay("25", type="refund")]).category == "MATCHED"


def test_failed_then_successful_charge_matches():
    assert one([order()], [pay("100", status="failed"), pay("100")]).category == "MATCHED"


# ------------------------------------------------------------------ cancelled / refunded


def test_cancelled_with_only_failed_attempt_matches():
    item = one([order(status="cancelled")], [pay("100", status="failed")])
    assert item.category == "MATCHED"
    assert item.reconciled_value == 0


def test_cancelled_with_pending_charge_is_pending_over_collected():
    item = one([order(status="cancelled")], [pay("100", status="pending")])
    assert (item.category, item.risk_direction, item.amount_at_risk) == ("PAYMENT_PENDING", "over_collected", D("100"))


def test_refunded_order_with_no_events_matches():
    assert one([order(status="refunded")], []).category == "MATCHED"


def test_cancelled_but_charged():
    item = one([order(status="cancelled")], [pay("100")])
    assert (item.category, item.amount_at_risk, item.risk_direction) == ("CANCELLED_BUT_CHARGED", D("100"), "over_collected")


# ------------------------------------------------------------------ refund boundaries (order net 100)


@pytest.mark.parametrize(
    ("status", "charge", "refund", "category", "at_risk", "excess"),
    [
        ("refunded", "100", "80", "REFUND_INCOMPLETE", "20", "0"),
        ("refunded", "100", "120", "OVER_REFUNDED", "20", "20"),
        ("completed", "100", "20", "REFUND_NOT_IN_ORDERS", "20", "0"),
        ("completed", "100", "100", "REFUND_NOT_IN_ORDERS", "100", "0"),
        ("completed", "100", "120", "OVER_REFUNDED", "120", "20"),
        ("completed", None, "30", "OVER_REFUNDED", "130", "30"),
    ],
)
def test_refund_boundaries(status, charge, refund, category, at_risk, excess):
    events = [pay(refund, type="refund")] + ([pay(charge)] if charge else [])
    item = one([order(status=status)], events)
    assert item.category == category
    assert item.amount_at_risk == D(at_risk)
    assert D(item.evidence["excess_refund"]) == D(excess)


# ------------------------------------------------------------------ other categories


def test_missing_payment():
    item = one([order()], [])
    assert (item.category, item.amount_at_risk, item.risk_direction) == ("MISSING_PAYMENT", D("100"), "under_collected")


def test_payment_failed():
    item = one([order()], [pay("100", status="failed"), pay("100", status="failed")])
    assert (item.category, item.amount_at_risk) == ("PAYMENT_FAILED", D("100"))


def test_failed_plus_pending_is_pending():
    item = one([order()], [pay("100", status="failed"), pay("100", status="pending")])
    assert (item.category, item.risk_direction) == ("PAYMENT_PENDING", "under_collected")


def test_currency_mismatch_beats_amount_rules():
    item = one([order()], [pay("100", currency="EUR")])
    assert (item.category, item.amount_at_risk, item.risk_direction) == ("CURRENCY_MISMATCH", D("100"), "unverified")
    assert item.disputed_value == D("100") and item.currency == "USD"


def test_failed_foreign_currency_event_is_not_a_mismatch():
    assert one([order()], [pay("100"), pay("90", currency="EUR", status="failed")]).category == "MATCHED"


def test_duplicate_charge():
    item = one([order()], [pay("100"), pay("100")])
    assert (item.category, item.amount_at_risk, item.risk_direction) == ("DUPLICATE_CHARGE", D("100"), "over_collected")


def test_overcharged_example_from_design():
    item = one([order(net="92.81")], [pay("117.81")])
    assert (item.category, item.disputed_value, item.amount_at_risk) == ("OVERCHARGED", D("92.81"), D("25.00"))


def test_undercharged():
    item = one([order()], [pay("81.50")])
    assert (item.category, item.amount_at_risk) == ("UNDERCHARGED", D("18.50"))


# ------------------------------------------------------------------ timing


@pytest.mark.parametrize(
    ("offset", "category"),
    [
        (timedelta(days=7), "MATCHED"),
        (timedelta(days=7, minutes=1), "LATE_CHARGE"),
        (-timedelta(hours=1), "MATCHED"),
        (-timedelta(hours=1, minutes=1), "LATE_CHARGE"),
    ],
)
def test_timing_rule_edges(offset, category):
    item = one([order()], [pay("100", at=T0 + offset)])
    assert item.category == category
    if category == "LATE_CHARGE":
        assert item.amount_at_risk == 0 and item.reconciled_value == D("100")


def test_timing_breach_is_a_flag_when_another_category_wins():
    item = one([order()], [pay("130", at=T0 + timedelta(days=9))])
    assert item.category == "OVERCHARGED" and "timing_breach" in item.dq_flags


def test_refunds_are_exempt_from_timing_and_blank_dates_skip_it():
    late_refund = pay("100", type="refund", at=T0 + timedelta(days=30))
    assert one([order(status="refunded")], [pay("100"), late_refund]).category == "MATCHED"
    assert one([order(date=None)], [pay("100", at=T0 + timedelta(days=30))]).category == "MATCHED"


def test_pending_next_to_settled_is_flagged():
    item = one([order()], [pay("100"), pay("100", status="pending")])
    assert item.category == "MATCHED" and "pending_alongside_settled" in item.dq_flags


# ------------------------------------------------------------------ orphans


def test_refund_only_orphan_is_under_collected():
    (item,) = reconcile([], [pay("40", type="refund", key="NOPE")])
    assert (item.category, item.amount_at_risk, item.risk_direction) == ("ORPHAN_PAYMENT", D("40"), "under_collected")


def test_orphans_split_by_currency_and_blank_reference_per_payment():
    blank = PaymentRecord(
        row_numbers=(9,), transaction_ref="TB", order_reference_raw="", order_key=None, processed_at=None,
        currency="USD", amount=D("5"), fee=None, net_settled=None, type="charge", status="settled", raw={}, dq_flags=(),
    )
    items = reconcile([], [pay("10", key="X"), pay("7", key="X", currency="EUR"), blank])
    assert sorted(i.item_key for i in items) == ["T:TB", "X:X:EUR", "X:X:USD"]
    assert all(i.category == "ORPHAN_PAYMENT" and i.risk_direction == "over_collected" for i in items)


def test_orphan_with_zero_net_has_no_direction():
    (item,) = reconcile([], [pay("10", key="X", status="pending")])
    assert (item.amount_at_risk, item.risk_direction, item.disputed_value) == (0, None, 0)


# ------------------------------------------------------------------ needs review (via the parser)

OH = "order_id,order_date,customer_email,currency,gross_amount,discount,net_amount,status\n"
PH = "transaction_ref,order_reference,processed_at,currency,amount,fee,net_settled,type,status\n"


def test_rejected_payment_with_readable_key_makes_order_needs_review():
    o = parse_orders((OH + "O1,2024-01-01 10:00:00,a@x.io,USD,10,0,10,completed\n").encode())
    p = parse_payments((PH + "T1,O1,01/01/2024 10:05,USD,1O,0,10,charge,settled\n").encode())
    (item,) = reconcile_files(o, p)
    assert item.category == "NEEDS_REVIEW" and item.disputed_value == D("10") and item.amount_at_risk == 0


def test_rejected_payment_with_blank_key_is_report_only():
    o = parse_orders((OH + "O1,2024-01-01 10:00:00,a@x.io,USD,10,0,10,completed\n").encode())
    p = parse_payments((PH + "T1,,01/01/2024 10:05,USD,1O,0,10,charge,settled\n").encode())
    (item,) = reconcile_files(o, p)
    assert item.category == "MISSING_PAYMENT"
    assert p.rejected[0].linked_order_key is None


def test_order_id_conflict_makes_key_needs_review_with_payment_amount():
    o = parse_orders(
        (OH + "O1,2024-01-01 10:00:00,a@x.io,USD,10,0,10,completed\nO1,2024-01-01 10:00:00,a@x.io,USD,12,0,12,completed\n").encode()
    )
    p = parse_payments((PH + "T1,O1,01/01/2024 10:05,USD,10,0,10,charge,settled\n").encode())
    (item,) = reconcile_files(o, p)
    assert (item.category, item.currency, item.disputed_value) == ("NEEDS_REVIEW", "USD", D("10"))


def test_needs_review_with_mixed_currencies_has_unknown_currency():
    items = reconcile([], [pay("10", key="K"), pay("10", key="K", currency="EUR")], review_keys={"K": ["x"]})
    (item,) = items
    assert (item.currency, item.disputed_value) == (None, 0)


# ------------------------------------------------------------------ invariants


def test_every_order_and_payment_belongs_to_exactly_one_item():
    orders = [order("A"), order("B", status="cancelled"), order("C")]
    payments = [pay("100", key="A"), pay("5", key="B"), pay("1", key="Z"), pay("1", key="Z", currency="EUR")]
    items = reconcile(orders, payments, review_keys={"C": ["r"]})
    order_keys = [k for i in items for k in i.order_keys]
    refs = [r for i in items for r in i.transaction_refs]
    assert sorted(order_keys) == ["A", "B", "C"]
    assert sorted(refs) == sorted(p.transaction_ref for p in payments)


def test_reconciled_and_disputed_are_exclusive():
    items = reconcile([order("A"), order("B")], [pay("100", key="A"), pay("150", key="B")])
    assert all(i.reconciled_value == 0 or i.disputed_value == 0 for i in items)


def test_sort_order_is_severity_then_risk_then_key():
    items = reconcile(
        [order("A"), order("B"), order("C"), order("D")],
        [pay("100", key="A"), pay("110", key="B"), pay("150", key="C"), pay("90", key="D")],
    )
    assert [i.order_key for i in items] == ["C", "B", "D", "A"]
