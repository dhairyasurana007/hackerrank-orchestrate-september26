"""Cash-state semantics: which events move cash, in which direction, and when
(TASKS.md M3, PLAN.md assumptions 2, 3 and 7).

``current_available_balance`` is the balance as of ``request_date`` and already reflects
every ``settled`` event, so settled history is never re-applied — it is read only to infer
recurrence. Verified across the dataset: every ``settled``, ``cancelled``, ``failed`` and
``unrealized`` row falls on or before its user's ``request_date``, and every ``pending`` and
``scheduled`` row falls after it. The forward curve is therefore built from pending and
scheduled rows alone.

The status rules:

===============  ===========  ==========================================================
status           direction    forward treatment
===============  ===========  ==========================================================
``settled``      any          historical; already in the balance, never re-applied
``pending``      debit        reserved on its settlement date (63 rows)
``pending``      credit       ignored — an unsettled credit is not cash (8 rows)
``scheduled``    debit        applied (23 rows, all essential categories)
``scheduled``    credit       applied as confirmed income (47 rows, all salary)
``cancelled``    any          never moves cash
``failed``       any          never moves cash
``unrealized``   any          never moves cash
any              ``non_cash`` never moves cash
===============  ===========  ==========================================================
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .records import Event

#: Statuses whose rows never move cash, whatever their direction or link.
INERT_STATUSES = ("cancelled", "failed", "unrealized")

#: Codes returned by :func:`disposition`.
FORWARD = "forward"
HISTORICAL = "historical"
IGNORED = "ignored"


@dataclass(frozen=True)
class Disposition:
    """What an event does to the forecast, and why."""

    code: str
    reason: str
    sign: int = 0

    @property
    def moves_cash_forward(self) -> bool:
        return self.code == FORWARD


@dataclass(frozen=True)
class CashMovement:
    """A signed movement on a dated day, already converted to the home currency."""

    event_id: str
    when: dt.date
    amount: float  # positive for a credit, negative for a debit
    reason: str
    event: Event

    @property
    def is_credit(self) -> bool:
        return self.amount > 0


def sign_of(direction: str) -> int:
    if direction == "debit":
        return -1
    if direction == "credit":
        return 1
    return 0  # non_cash


def collapses_into_linked_settlement(event: Event, events_by_id: dict[str, Event]) -> bool:
    """True when a pending row is an authorisation its linked settled row already completed.

    An authorisation *precedes* its settlement, so the pair only collapses when the linked
    settled row's cash date is not earlier than this row's own event date. That ordering
    test is what keeps the dataset's six "Possible duplicate card charge" rows out of this
    branch: each links *back* to an "Original card charge" settled eleven days earlier, so
    it is a second charge rather than the same one seen twice — and the accompanying bank
    message confirms the dispute is open with no reversal posted, which is the financially
    safer reading as well as the evidenced one.
    """
    if event.status != "pending" or not event.linked_event_id:
        return False
    linked = events_by_id.get(event.linked_event_id)
    if linked is None or linked.status != "settled":
        return False
    if linked.direction != event.direction:
        return False
    if event.amount is None or linked.amount is None or linked.currency != event.currency:
        return False
    if abs(linked.amount - event.amount) > 1e-9:
        return False
    return linked.cash_date >= event.event_date


def collapses_as_reversal(event: Event, events_by_id: dict[str, Event]) -> bool:
    """True when this row is the reversed half of a settled reversal pair.

    A refund joined to the expense it reverses nets to zero. The link alone does not decide
    it — the statuses do: a refund that has not settled is not cash, and is ignored by the
    pending-credit rule instead.
    """
    if event.event_type != "refund" or not event.linked_event_id:
        return False
    if event.status != "settled":
        return False
    linked = events_by_id.get(event.linked_event_id)
    return linked is not None and linked.status == "settled"


def disposition(event: Event, events_by_id: dict[str, Event] | None = None) -> Disposition:
    """Classify one event's effect on the forward curve."""
    events_by_id = events_by_id or {}

    if event.direction == "non_cash":
        return Disposition(IGNORED, "non_cash never moves cash")
    if event.status in INERT_STATUSES:
        return Disposition(IGNORED, f"{event.status} never moves cash")
    if event.status == "settled":
        if collapses_as_reversal(event, events_by_id):
            return Disposition(HISTORICAL, "settled reversal pair nets to zero")
        return Disposition(HISTORICAL, "settled; already reflected in the balance")
    if event.status == "pending":
        if event.direction == "credit":
            return Disposition(IGNORED, "pending credit is not cash until it settles")
        if collapses_into_linked_settlement(event, events_by_id):
            return Disposition(IGNORED, "authorisation already settled by its linked row")
        return Disposition(FORWARD, "pending debit reserved on its settlement date", sign=-1)
    if event.status == "scheduled":
        reason = (
            "scheduled credit applied as confirmed income"
            if event.direction == "credit"
            else "scheduled debit applied"
        )
        return Disposition(FORWARD, reason, sign=sign_of(event.direction))
    raise ValueError(f"{event.event_id}: unhandled status {event.status!r}")


def explicit_movements(
    events,
    *,
    home_currency: str,
    rates,
    events_by_id: dict[str, Event] | None = None,
    amounts: dict[str, float] | None = None,
) -> tuple[CashMovement, ...]:
    """Every explicit forward movement for a user, signed and in the home currency.

    ``amounts`` supplies a recovered figure for an event whose ``amount`` is blank, keyed by
    ``event_id``; a blank amount with no recovered figure is skipped rather than treated as
    zero, and the caller is responsible for having recorded the fallback.
    """
    events_by_id = events_by_id or {event.event_id: event for event in events}
    amounts = amounts or {}
    movements = []
    for event in events:
        verdict = disposition(event, events_by_id)
        if not verdict.moves_cash_forward:
            continue
        value = amounts.get(event.event_id, event.amount)
        if value is None:
            continue
        converted = rates.convert(value, event.currency, home_currency, event.cash_date)
        movements.append(
            CashMovement(
                event_id=event.event_id,
                when=event.cash_date,
                amount=verdict.sign * converted,
                reason=verdict.reason,
                event=event,
            )
        )
    return tuple(sorted(movements, key=lambda m: (m.when, m.event_id)))


def settled_history(events) -> tuple[Event, ...]:
    """The settled rows recurrence inference reads. Never applied to the curve."""
    return tuple(event for event in events if event.status == "settled" and event.direction != "non_cash")
