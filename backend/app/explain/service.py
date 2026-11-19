"""Generate a validated explanation, retrying once, else a deterministic fallback."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from . import prompts
from .provider import Provider, ProviderError
from .schemas import ItemExplanation, SummaryExplanation, json_schema

log = logging.getLogger("app.explain")

TOTAL_BUDGET = 24.0  # seconds across both attempts (design: under 25 s)
ATTEMPTS = 2


@dataclass(frozen=True)
class Result:
    output: dict
    source: str  # "llm" | "fallback"


def _attempt(provider: Provider, task: str, evidence: dict, schema: type[BaseModel], clock, attempts: int) -> dict | None:
    deadline = clock() + TOTAL_BUDGET
    for attempt in range(1, attempts + 1):
        remaining = deadline - clock()
        if remaining <= 1:
            break
        timeout = remaining / (attempts - attempt + 1)
        try:
            text = provider.generate(prompts.SYSTEM, prompts.user_message(task, evidence), json_schema(schema), timeout)
            return schema.model_validate(json.loads(text)).model_dump()
        except ProviderError as exc:
            log.warning("llm attempt %s failed: %s", attempt, exc)
        except (json.JSONDecodeError, ValidationError) as exc:
            log.warning("llm attempt %s returned invalid output: %s", attempt, type(exc).__name__)
    return None


def explain(
    provider: Provider | None,
    task: str,
    evidence: dict,
    schema: type[BaseModel],
    fallback: Callable[[dict], dict],
    clock: Callable[[], float] = time.monotonic,
) -> Result:
    if provider is not None:
        output = _attempt(provider, task, evidence, schema, clock, ATTEMPTS)
        if output is not None:
            return Result(output, "llm")
    return Result(schema.model_validate(fallback(evidence)).model_dump(), "fallback")


# ------------------------------------------------------------------ deterministic fallbacks

_ACTIONS = {
    "NEEDS_REVIEW": ["Open the source rows listed in the validation report and correct them.", "Re-upload the corrected files."],
    "CURRENCY_MISMATCH": ["Confirm which currency the customer was charged in.", "Correct the order or payment currency at the source."],
    "MISSING_PAYMENT": ["Check the processor for a charge under another reference.", "If none exists, collect payment or cancel the order."],
    "PAYMENT_FAILED": ["Contact the customer to retry payment.", "Cancel the order if payment cannot be collected."],
    "PAYMENT_PENDING": ["Wait for the pending charge to settle and re-check.", "Investigate if it stays pending for more than a few days."],
    "CANCELLED_BUT_CHARGED": ["Refund the customer for the collected amount.", "Confirm the cancellation in the order system."],
    "REFUND_INCOMPLETE": ["Refund the remaining balance to the customer.", "Or correct the order status if no refund is owed."],
    "OVER_REFUNDED": ["Confirm whether the extra refund was intended.", "Recover the excess or record it as a write-off."],
    "REFUND_NOT_IN_ORDERS": ["Confirm the refund was legitimate.", "Update the order record to reflect the refund."],
    "DUPLICATE_CHARGE": ["Refund the duplicate charge to the customer.", "Check the checkout flow for double submissions."],
    "OVERCHARGED": ["Refund the overcharged difference.", "Check pricing or discount application for this order."],
    "UNDERCHARGED": ["Collect the shortfall or approve it as a discount.", "Check pricing or discount application for this order."],
    "WITHIN_TOLERANCE": ["No action needed; the difference is rounding noise."],
    "LATE_CHARGE": ["No money is at risk; review why the charge settled outside the normal window."],
    "MATCHED": ["No action needed."],
    "ORPHAN_PAYMENT": ["Find the order this payment belongs to.", "If none exists, refund or investigate the payment."],
}


def item_fallback(evidence: dict) -> dict:
    category = evidence["category"]
    return {
        "summary": evidence["reason"][:600],
        "likely_cause": f"Classified as {category.replace('_', ' ').lower()} by the reconciliation rules; "
        "an AI explanation is not available right now.",
        "recommended_actions": _ACTIONS.get(category, ["Review the evidence for this item."])[:4],
        "confidence": "low",
    }


def summary_fallback(evidence: dict) -> dict:
    by_currency = evidence.get("metrics_by_currency", {})
    parts = []
    for cur, b in sorted(by_currency.items()):
        risk = b.get("at_risk") or {}
        parts.append(
            f"{cur}: {b.get('disputed_value', '0.00')} in dispute, "
            f"{risk.get('under_collected', '0.00')} under-collected, {risk.get('over_collected', '0.00')} over-collected"
        )
    issues = [f"{i['category']}: {i['reason']}"[:300] for i in evidence.get("top_items", [])[:5]]
    return {
        "headline": ("; ".join(parts) or "No reconciliation results in this import.")[:300],
        "top_issues": issues or ["No discrepancies found."],
        "recommended_priorities": [
            "Resolve high-severity items first, largest amount at risk first.",
            "Fix rejected rows in the validation report and re-upload.",
        ],
    }


ITEM = (prompts.ITEM_TASK, ItemExplanation, item_fallback)
SUMMARY = (prompts.SUMMARY_TASK, SummaryExplanation, summary_fallback)
