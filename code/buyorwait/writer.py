"""Output row formatting and CSV emission (PLAN.md 2, TASKS.md M12).

The two amount fields follow *different* formatting rules in the same row, and matching
ground truth on exact comparison depends on reproducing both:

* amounts inside ``payment_plan`` are rendered to exactly 2dp when fractional (``620.40``,
  ``3246.10``) and with no decimal part when whole (``38016``);
* ``amount_safe_to_pay`` is not 2dp-formatted at all — the samples carry ``433.4``,
  ``603.3``, ``17229139.2``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .contract import OUTPUT_COLUMNS

# Beyond this many decimal places a value is binary-representation noise rather than money.
_SAFE_AMOUNT_DP = 6


def format_plan_amount(amount: float) -> str:
    """``payment_plan`` amounts: 2dp when fractional, bare integer when whole."""
    rounded = round(float(amount) + 0.0, 2)
    if abs(rounded - round(rounded)) < 5e-9:
        return str(int(round(rounded)))
    return f"{rounded:.2f}"


def format_safe_amount(amount: float) -> str:
    """``amount_safe_to_pay``: shortest exact-looking decimal, not padded to 2dp."""
    value = round(float(amount) + 0.0, _SAFE_AMOUNT_DP)
    if abs(value - round(value)) < 5e-9:
        return str(int(round(value)))
    text = f"{value:.{_SAFE_AMOUNT_DP}f}".rstrip("0").rstrip(".")
    return text if text else "0"


def format_date(value) -> str:
    """ISO ``YYYY-MM-DD``, or the empty string for a missing date."""
    if value is None or value == "":
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


@dataclass
class OutputRow:
    """One emitted prediction, already decided; formatting happens on the way out."""

    request_id: str
    amount_safe_to_pay: float = 0.0
    affordability_status: str = "not_affordable"
    recommended_payment_method: str = "not_recommended"
    payment_plan: Sequence[tuple] = field(default_factory=tuple)
    earliest_date_for_full_payment: object = None
    spending_changes_needed: Sequence[str] = field(default_factory=tuple)
    decision_explanation: str = ""
    from_fallback: bool = False

    def payment_plan_text(self) -> str:
        if not self.payment_plan:
            return "none"
        return "|".join(
            f"{format_date(when)}:{format_plan_amount(amount)}" for when, amount in self.payment_plan
        )

    def spending_changes_text(self) -> str:
        return "|".join(self.spending_changes_needed) if self.spending_changes_needed else "none"

    def as_csv_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "amount_safe_to_pay": format_safe_amount(self.amount_safe_to_pay),
            "affordability_status": self.affordability_status,
            "recommended_payment_method": self.recommended_payment_method,
            "payment_plan": self.payment_plan_text(),
            "earliest_date_for_full_payment": format_date(self.earliest_date_for_full_payment),
            "spending_changes_needed": self.spending_changes_text(),
            "decision_explanation": self.decision_explanation,
        }


def fallback_row(request_id: str, explanation: str = "") -> OutputRow:
    """The conservative, contract-valid row used when a request cannot be processed."""
    return OutputRow(
        request_id=request_id,
        amount_safe_to_pay=0.0,
        affordability_status="not_affordable",
        recommended_payment_method="not_recommended",
        payment_plan=(),
        earliest_date_for_full_payment=None,
        spending_changes_needed=(),
        decision_explanation=explanation
        or "Unable to establish a safe payment for this request from the available records.",
        from_fallback=True,
    )


def write_output(path: str | Path, rows: Iterable[OutputRow]) -> int:
    """Write ``output.csv`` with the exact contract column order. Returns the row count."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(OUTPUT_COLUMNS), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_csv_dict())
            count += 1
    return count
