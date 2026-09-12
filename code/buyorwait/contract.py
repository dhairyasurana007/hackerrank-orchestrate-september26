"""The output contract: column order, vocabularies, and the status/method coupling.

Every one of these is derived from the brief or verified across all 25 solved samples
(PLAN.md 2). They live in one module so the writer, the validator and the tests cannot
drift from each other.
"""

from __future__ import annotations

OUTPUT_COLUMNS = (
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
)

AFFORDABILITY_STATUSES = (
    "affordable_now",
    "affordable_with_plan",
    "affordable_later",
    "not_affordable",
)

PAYMENT_METHODS = (
    "full_payment",
    "partial_payment",
    "installments",
    "wait",
    "not_recommended",
)

# Verified across all 25 samples: only these six combinations occur.
STATUS_METHODS = {
    "affordable_now": ("full_payment",),
    "affordable_later": ("wait",),
    "not_affordable": ("not_recommended",),
    "affordable_with_plan": ("installments", "partial_payment", "full_payment"),
}

FLEXIBLE = ("reducible", "stoppable", "reducible_or_stoppable")
REDUCIBLE = ("reducible", "reducible_or_stoppable")
STOPPABLE = ("stoppable", "reducible_or_stoppable")

MAX_SPENDING_CHANGES = 3
FORECAST_DAYS = 90
