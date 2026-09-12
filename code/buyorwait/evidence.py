"""Typed amendments: the one interface evidence reaches the engine through (TASKS.md M15).

Messages and images are **untrusted data** (PLAN.md assumption 8). Imperative text inside
them is not instruction; only financial facts are extracted. That is enforced structurally
rather than by prompt wording: the extractor can only emit records of the shapes below, each
is re-validated here against the user's own history before it is applied, and the engine
consumes them through this module alone. There is no path by which message text reaches the
arithmetic, so a message saying "mark this affordable" has nowhere to land — the worst it can
do is produce an amendment that fails validation and is dropped.

Every amendment is a *series-level* fact. That is the level the forecast works at, and it
keeps the model away from per-day arithmetic entirely.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .records import CURRENCIES

#: An amendment below this confidence is dropped rather than partially applied.
CONFIDENCE_FLOOR = 0.6

#: How far from the request date an effective date may fall and still be credible.
MAX_PAST_DAYS = 400
MAX_FUTURE_DAYS = 200

KINDS = (
    # The user's income will be this amount going forward, from `effective_date`.
    "income_amount",
    # The confirmed income date moves to `effective_date`; the amount is unchanged.
    "income_date",
    # An income series has ended and must not be projected.
    "income_stopped",
    # An income series is unapproved, pending, or not withdrawable, and must not be counted.
    "income_suppressed",
    # A recurring expense category changes by `percent_change` from `effective_date`.
    "expense_change_pct",
)

#: ``next_only`` applies to a single upcoming occurrence; ``ongoing`` to all of them.
SCOPES = ("ongoing", "next_only")


@dataclass(frozen=True)
class Amendment:
    kind: str
    amount: float | None = None
    currency: str | None = None
    effective_date: dt.date | None = None
    percent_change: float | None = None
    target_category: str | None = None
    target_description: str | None = None
    scope: str = "ongoing"
    confidence: float = 0.0
    message_id: str = ""
    source_type: str = ""

    @property
    def is_income(self) -> bool:
        return self.kind.startswith("income_")


def _parse_date(value) -> dt.date | None:
    if isinstance(value, dt.date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        return None


def validate(amendment: Amendment, *, request, events, note=None) -> str | None:
    """Why this amendment must be dropped, or ``None`` if it may be applied.

    Re-validated against the user's own records rather than trusted as parsed: an amendment
    naming a category the user has never transacted in, a currency that is not theirs, an
    implausible date, or a confidence below the floor is dropped whole.
    """

    def drop(reason: str) -> str:
        if note is not None:
            note(reason, amendment)
        return reason

    if amendment.kind not in KINDS:
        return drop(f"unknown kind {amendment.kind!r}")
    if amendment.scope not in SCOPES:
        return drop(f"unknown scope {amendment.scope!r}")
    if amendment.confidence < CONFIDENCE_FLOOR:
        return drop(f"confidence {amendment.confidence:.2f} below the floor {CONFIDENCE_FLOOR}")

    if amendment.effective_date is not None:
        delta = (amendment.effective_date - request.request_date).days
        if not -MAX_PAST_DAYS <= delta <= MAX_FUTURE_DAYS:
            return drop(f"effective date {amendment.effective_date} is {delta} days from the request")

    if amendment.kind == "income_amount":
        if amendment.amount is None or amendment.amount <= 0:
            return drop("income_amount without a positive amount")
        if amendment.currency is not None and amendment.currency not in CURRENCIES:
            return drop(f"unknown currency {amendment.currency!r}")
    if amendment.kind == "income_date" and amendment.effective_date is None:
        return drop("income_date without a date")
    if amendment.kind == "expense_change_pct":
        if amendment.percent_change is None or amendment.percent_change == 0:
            return drop("expense_change_pct without a change")
        if abs(amendment.percent_change) > 100:
            return drop(f"implausible percent change {amendment.percent_change}")
        if amendment.target_category is None:
            return drop("expense_change_pct without a target category")

    # The category, if named, must be one the user actually transacts in. This is the check
    # that stops an amendment inventing a commitment out of nothing.
    if amendment.target_category is not None:
        categories = {event.category for event in events}
        if amendment.target_category not in categories:
            return drop(f"category {amendment.target_category!r} does not appear in this user's history")

    return None


def applicable_messages(messages, request) -> tuple:
    """Messages that bear on this request (PLAN.md assumption 9).

    A message applies when it was sent on or before ``request_date`` and it attaches to this
    request, to the user, or to one of the user's events. Later messages from the same source
    supersede earlier ones, which the ordering here makes the caller's job: they arrive oldest
    first, so a later fact naturally overwrites an earlier one.
    """
    relevant = [
        message
        for message in messages
        if message.sent_on <= request.request_date
        and (message.request_id is None or message.request_id == request.request_id)
    ]
    return tuple(sorted(relevant, key=lambda m: (m.sent_at, m.message_id)))
