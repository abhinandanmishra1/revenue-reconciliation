"""Versioned reconciliation constants.

These are code, not configuration: a given RULES_VERSION must always behave the
same, so changing any value here means bumping the version.
"""

from datetime import timedelta
from decimal import Decimal

RULES_VERSION = "2026.11.1"

AMOUNT_TOLERANCE = Decimal("0.05")
LATE_AFTER = timedelta(days=7)
EARLY_BEFORE = timedelta(hours=1)

# Category -> (severity, severity_rank). Lower rank sorts first.
SEVERITY_RANK = {"high": 1, "medium": 2, "low": 3, "info": 4, "none": 5}

CATEGORY_SEVERITY = {
    "NEEDS_REVIEW": "high",
    "CURRENCY_MISMATCH": "high",
    "MISSING_PAYMENT": "high",
    "PAYMENT_FAILED": "high",
    "PAYMENT_PENDING": "low",
    "CANCELLED_BUT_CHARGED": "high",
    "REFUND_INCOMPLETE": "high",
    "OVER_REFUNDED": "high",
    "REFUND_NOT_IN_ORDERS": "medium",
    "DUPLICATE_CHARGE": "high",
    "OVERCHARGED": "high",
    "UNDERCHARGED": "medium",
    "WITHIN_TOLERANCE": "info",
    "LATE_CHARGE": "low",
    "MATCHED": "none",
    "ORPHAN_PAYMENT": "medium",
}

# Precedence of the classification table (section 6.3); first match wins.
RULE_NUMBER = {
    "NEEDS_REVIEW": 0,
    "CURRENCY_MISMATCH": 1,
    "MISSING_PAYMENT": 2,
    "PAYMENT_FAILED": 3,
    "PAYMENT_PENDING": 4,
    "CANCELLED_BUT_CHARGED": 5,
    "REFUND_INCOMPLETE": 6,
    "OVER_REFUNDED": 7,
    "REFUND_NOT_IN_ORDERS": 8,
    "DUPLICATE_CHARGE": 9,
    "OVERCHARGED": 10,
    "UNDERCHARGED": 11,
    "WITHIN_TOLERANCE": 12,
    "LATE_CHARGE": 13,
    "MATCHED": 14,
    "ORPHAN_PAYMENT": None,
}

RECONCILED_CATEGORIES = frozenset({"MATCHED", "WITHIN_TOLERANCE", "LATE_CHARGE"})
CATEGORIES = tuple(CATEGORY_SEVERITY)
DIRECTIONS = ("under_collected", "over_collected", "unverified")
