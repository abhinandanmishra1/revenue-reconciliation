"""Golden tests on the sample exports, plus row-order determinism.

The per-order categories are pinned in fixtures/golden_categories.json, which is
generated once with scripts/classify.py and then reviewed by hand.
"""

import json
import random
from decimal import Decimal
from pathlib import Path

import pytest

from app.ingestion import parse_orders, parse_payments
from app.reconcile import compute_metrics, reconcile_files

FIXTURES = Path(__file__).parent / "fixtures"
ORDERS, PAYMENTS, GOLDEN = FIXTURES / "orders.csv", FIXTURES / "payments.csv", FIXTURES / "golden_categories.json"

needs_fixtures = pytest.mark.skipif(
    not (ORDERS.exists() and PAYMENTS.exists()), reason="sample exports not added to tests/fixtures yet"
)


def run(orders_bytes: bytes, payments_bytes: bytes):
    o, p = parse_orders(orders_bytes), parse_payments(payments_bytes)
    items = reconcile_files(o, p)
    return items, compute_metrics(items, o, p)


def shuffled(data: bytes, seed: int) -> bytes:
    header, *rows = data.decode("utf-8-sig").splitlines(keepends=True)
    rows = [r if r.endswith("\n") else r + "\n" for r in rows]
    random.Random(seed).shuffle(rows)
    return (header + "".join(rows)).encode()


def snapshot(items) -> list:
    """Everything about the result except source row numbers, which move on shuffle."""
    out = []
    for i in items:
        evidence = json.loads(json.dumps(i.evidence))
        for part in [evidence.get("order") or {}, *evidence.get("events", [])]:
            part.pop("row_numbers", None)
        reason = i.reason if i.category != "NEEDS_REVIEW" else None  # mentions row numbers
        out.append((i.item_key, i.category, str(i.amount_at_risk), i.risk_direction, reason, i.dq_flags,
                    json.dumps(evidence if i.category != "NEEDS_REVIEW" else None, sort_keys=True)))
    return out


@needs_fixtures
def test_every_order_category_is_pinned():
    items, _ = run(ORDERS.read_bytes(), PAYMENTS.read_bytes())
    actual = {i.item_key: i.category for i in items}
    assert GOLDEN.exists(), "run `uv run python scripts/classify.py --write-golden` and review the file"
    assert actual == json.loads(GOLDEN.read_text())


@needs_fixtures
def test_pinned_totals():
    _, metrics = run(ORDERS.read_bytes(), PAYMENTS.read_bytes())
    usd, eur = metrics["by_currency"]["USD"], metrics["by_currency"]["EUR"]
    assert Decimal(usd["at_risk"]["under_collected"]) == Decimal("886.85")
    assert Decimal(usd["at_risk"]["over_collected"]) == Decimal("936.58")
    assert Decimal(usd["at_risk"]["unverified"]) == Decimal("210.00")
    assert Decimal(eur["at_risk"]["unverified"]) == Decimal("145.00")
    assert Decimal(usd["reconciled_value"]) == Decimal("39963.28")
    assert usd["reconciled_orders"] == 168
    assert Decimal(usd["disputed_value"]) == Decimal("2469.37")
    assert Decimal(eur["disputed_value"]) == Decimal("145.00")


@needs_fixtures
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_fixture_result_is_independent_of_row_order(seed):
    o, p = ORDERS.read_bytes(), PAYMENTS.read_bytes()
    base_items, base_metrics = run(o, p)
    items, metrics = run(shuffled(o, seed), shuffled(p, seed + 100))
    assert snapshot(items) == snapshot(base_items)
    assert metrics == base_metrics


SYNTHETIC_ORDERS = b"""order_id,order_date,customer_email,currency,gross_amount,discount,net_amount,status
A,2024-03-01 10:00:00,a@x.io,USD,100,0,100,completed
B,2024-03-01 10:00:00,b@x.io,USD,50,,50,cancelled
C,2024-03-01 10:00:00,c@x.io,EUR,80,0,80,completed
D,2024-03-01 10:00:00,d@x.io,USD,100,0,100,refunded
E,2024-03-01 10:00:00,e@x.io,USD,20,0,20,completed
E,2024-03-01 10:00:00,e@x.io,USD,20,0,20,completed
F,2024-03-01 10:00:00,f@x.io,USD,20,0,20,completed
F,2024-03-01 10:00:00,f@x.io,USD,25,0,25,completed
"""
SYNTHETIC_PAYMENTS = b"""transaction_ref,order_reference,processed_at,currency,amount,fee,net_settled,type,status
T1,A,01/03/2024 10:05,USD,60,0,60,charge,settled
T2,a ,01/03/2024 10:06,USD,40,0,40,charge,settled
T3,B,01/03/2024 10:05,USD,50,0,50,charge,settled
T4,C,01/03/2024 10:05,USD,80,0,80,charge,settled
T5,D,01/03/2024 10:05,USD,100,0,100,charge,settled
T6,D,02/03/2024 10:05,USD,80,0,80,refund,settled
T7,ZZ,02/03/2024 10:05,USD,5,0,5,charge,settled
T8,,02/03/2024 10:05,USD,7,0,7,charge,settled
T9,E,02/03/2024 10:05,USD,x,0,7,charge,settled
"""


@pytest.mark.parametrize("seed", range(5))
def test_synthetic_result_is_independent_of_row_order(seed):
    base_items, base_metrics = run(SYNTHETIC_ORDERS, SYNTHETIC_PAYMENTS)
    items, metrics = run(shuffled(SYNTHETIC_ORDERS, seed), shuffled(SYNTHETIC_PAYMENTS, seed + 10))
    assert snapshot(items) == snapshot(base_items)
    assert [i.category for i in items] == [i.category for i in base_items]
    assert metrics == base_metrics
