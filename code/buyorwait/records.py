"""Typed records for the nine dataset inputs (TASKS.md M1).

Every record is frozen: the pipeline downstream of the loaders is a chain of pure functions
over these values (PLAN.md 5.1), and immutability is what keeps that true.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from .contract import REDUCIBLE, STOPPABLE

EVENT_TYPES = (
    "expense",
    "subscription",
    "income",
    "debt_payment",
    "refund",
    "investment_purchase",
    "investment_sale",
    "investment_valuation",
)
DIRECTIONS = ("debit", "credit", "non_cash")
STATUSES = ("settled", "pending", "scheduled", "cancelled", "failed", "unrealized")
FLEXIBILITIES = ("fixed", "reducible", "stoppable", "reducible_or_stoppable")
CURRENCIES = ("EUR", "USD", "ZAR", "INR", "IDR")
REQUEST_TYPES = (
    "purchase",
    "travel",
    "family_transfer",
    "education",
    "debt_repayment",
    "emergency_expense",
    "housing",
    "investment",
    "other",
)
SOURCE_TYPES = ("bank", "employer", "financial_service", "merchant", "service_provider")


class DatasetError(ValueError):
    """A dataset row that cannot be parsed. Raised rather than defaulted (PLAN.md 6.1)."""


@dataclass(frozen=True)
class Profile:
    user_id: str
    home_currency: str
    current_available_balance: float
    minimum_balance_to_keep: float
    financial_priorities: tuple[str, ...]
    expense_categories_to_protect: tuple[str, ...]
    categories_willing_to_reduce: tuple[str, ...]
    categories_willing_to_stop: tuple[str, ...]
    payment_methods_user_will_consider: tuple[str, ...]
    max_installment_months: int | None

    def accepts(self, method: str) -> bool:
        return method in self.payment_methods_user_will_consider

    @property
    def considers_installments(self) -> bool:
        """Blank ``max_installment_months`` means the user will not consider installments."""
        return self.accepts("installments") and self.max_installment_months is not None


@dataclass(frozen=True)
class Event:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str
    amount: float | None
    currency: str
    event_date: dt.date
    settlement_date: dt.date | None
    status: str
    linked_event_id: str | None
    flexibility: str
    minimum_allowed_amount: float | None

    @property
    def cash_date(self) -> dt.date:
        """The date cash moves: the settlement date, falling back to the event date."""
        return self.settlement_date or self.event_date

    @property
    def is_flexible(self) -> bool:
        return self.flexibility != "fixed"

    @property
    def is_reducible(self) -> bool:
        return self.flexibility in REDUCIBLE

    @property
    def is_stoppable(self) -> bool:
        return self.flexibility in STOPPABLE


@dataclass(frozen=True)
class Request:
    request_id: str
    user_id: str
    request_date: dt.date
    request_type: str
    requested_amount: float
    desired_completion_date: dt.date
    allows_partial_payment: bool
    request_text: str


@dataclass(frozen=True)
class SampleTruth:
    """The solved fields of a ``sample_requests.csv`` row, kept raw for exact comparison."""

    request_id: str
    amount_safe_to_pay: float
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: str
    spending_changes_needed: str
    decision_explanation: str


@dataclass(frozen=True)
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str
    payment_amount: float
    number_of_payments: int
    first_payment_date: dt.date
    payment_frequency_days: int | None
    financing_fee: float
    total_payable_amount: float

    def schedule(self) -> tuple[tuple[dt.date, float], ...]:
        """The option's payments, dated from ``first_payment_date`` by its frequency.

        Verified against sample_02: 3 payments at 30-day spacing land on 2025-08-08,
        2025-09-07 and 2025-10-07, exactly as the ground truth plan reads.
        """
        step = self.payment_frequency_days or 0
        return tuple(
            (self.first_payment_date + dt.timedelta(days=step * index), self.payment_amount)
            for index in range(self.number_of_payments)
        )


@dataclass(frozen=True)
class Message:
    message_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    sent_at: dt.datetime
    source_type: str
    message_text: str

    @property
    def sent_on(self) -> dt.date:
        return self.sent_at.date()


@dataclass(frozen=True)
class ImageRef:
    image_id: str
    user_id: str
    request_id: str
    related_event_id: str

    def path(self, dataset: Path) -> Path:
        """``image_07`` resolves to ``dataset/media/images/image_07.png``."""
        return Path(dataset) / "media" / "images" / f"{self.image_id}.png"
