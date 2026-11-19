"""Print the classification of the sample exports, optionally pinning it.

Usage: uv run python scripts/classify.py [--write-golden]
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingestion import parse_orders, parse_payments  # noqa: E402
from app.reconcile import compute_metrics, reconcile_files  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def main() -> None:
    o = parse_orders((FIXTURES / "orders.csv").read_bytes())
    p = parse_payments((FIXTURES / "payments.csv").read_bytes())
    items = reconcile_files(o, p)
    print(f"{'item':<24}{'category':<24}{'cur':<6}{'expected':>11}{'actual':>11}{'at risk':>10}  direction")
    for i in items:
        print(
            f"{i.item_key:<24}{i.category:<24}{i.currency or '-':<6}{str(i.expected_amount or '-'):>11}"
            f"{str(i.actual_amount if i.actual_amount is not None else '-'):>11}{str(i.amount_at_risk):>10}  {i.risk_direction or '-'}"
        )
    print(json.dumps({"orders": o.report(), "payments": p.report()}, indent=2))
    print(json.dumps(compute_metrics(items, o, p), indent=2))
    if "--write-golden" in sys.argv:
        golden = {i.item_key: i.category for i in items}
        (FIXTURES / "golden_categories.json").write_text(json.dumps(golden, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
