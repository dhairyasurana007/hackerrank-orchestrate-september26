"""Spending-change search (TASKS.md M10, PLAN.md 2).

When full payment is not safe today, a set of permitted changes to the user's own recurring
spending may make it safe. The contract is narrow and the search respects all of it:

* only **recurring** series may be changed — 2,505 expense rows carry ``reducible`` without
  all of them being recurring, and the README's checklist states the recurring qualifier
  explicitly;
* the series' category must be outside ``expense_categories_to_protect`` and inside the
  relevant willingness list — ``..._willing_to_stop`` for ``stop``,
  ``..._willing_to_reduce`` for ``reduce_to``;
* the event's own ``flexibility`` must permit the action;
* ``reduce_to`` never goes below ``minimum_allowed_amount``;
* at most three actions, and ``stop`` and ``reduce_to`` never target the same event.

**The objective is the smallest total cut to recurring spending, not the fewest actions.**
That is read off the samples rather than assumed, and the two disagree:

* request_21 could be fixed by one action — stopping the EUR 47 streaming subscription — and
  the ground truth instead uses *two*, stopping EUR 11 of cloud storage and reducing
  streaming to its EUR 23.50 floor, for a total cut of EUR 34.50 against 47. Fewest-actions
  ranking would have picked the single larger cut.
* request_11 could be fixed by reducing either dining (a cut of IDR 497,580 per cycle) or
  entertainment (IDR 845,676 per cycle). The ground truth reduces dining — the smaller cut,
  even though both are single actions and entertainment's event id sorts first.

Each action names a **specific event**: the most recent occurrence of its series, which is
what all three sample rows do (``event_476``, ``event_989``, ``event_1815``/``event_1816``).
And ``reduce_to`` always targets ``minimum_allowed_amount`` exactly, never an intermediate
figure — again what the samples show.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from . import safety
from .contract import MAX_SPENDING_CHANGES
from .forecast import Curve
from .planner import Candidate
from .records import Profile, Request
from .writer import format_plan_amount

#: Cap on how many eligible actions enter the combination search. A user has far fewer
#: flexible recurring series than this; the cap only bounds the worst case.
MAX_ACTIONS_CONSIDERED = 12


@dataclass(frozen=True)
class Action:
    """One permitted change to one recurring series."""

    kind: str  # "stop" or "reduce_to"
    event_id: str
    series_key: tuple
    category: str
    new_amount: float  # home currency, for simulation; 0.0 for a stop
    emitted_amount: float | None  # the event's own currency, for the row
    saving: float  # home-currency cut per occurrence

    def render(self) -> str:
        if self.kind == "stop":
            return f"stop:{self.event_id}"
        return f"reduce_to:{self.event_id}:{format_plan_amount(self.emitted_amount)}"


def eligible_actions(profile: Profile, curve: Curve, rates=None) -> tuple[Action, ...]:
    """Every change the user's own preferences and the event's flexibility allow."""
    actions: list[Action] = []
    for series in curve.series:
        if not series.projects or series.is_income:
            continue
        category = series.category
        if category in profile.expense_categories_to_protect:
            continue
        event = series.representative
        current = series.amount
        if current <= 0:
            continue

        if category in profile.categories_willing_to_stop and event.is_stoppable:
            actions.append(
                Action(
                    kind="stop",
                    event_id=event.event_id,
                    series_key=series.key,
                    category=category,
                    new_amount=0.0,
                    emitted_amount=None,
                    saving=current,
                )
            )

        floor = event.minimum_allowed_amount
        if (
            category in profile.categories_willing_to_reduce
            and event.is_reducible
            and floor is not None
        ):
            converted = (
                rates.convert(floor, event.currency, profile.home_currency, event.cash_date)
                if rates is not None
                else floor
            )
            # A "reduction" that raises the amount is not a reduction.
            if converted < current:
                actions.append(
                    Action(
                        kind="reduce_to",
                        event_id=event.event_id,
                        series_key=series.key,
                        category=category,
                        new_amount=converted,
                        emitted_amount=floor,
                        saving=current - converted,
                    )
                )
    actions.sort(key=lambda a: (a.saving, a.event_id, a.kind))
    return tuple(actions[:MAX_ACTIONS_CONSIDERED])


def _combinations(actions):
    """Sets of 1 to 3 actions, at most one per event, cheapest total cut first."""
    sets = []
    for size in range(1, MAX_SPENDING_CHANGES + 1):
        for combination in itertools.combinations(actions, size):
            if len({action.event_id for action in combination}) != size:
                continue  # stop and reduce_to must never target the same event
            sets.append(combination)
    sets.sort(key=lambda c: (sum(a.saving for a in c), len(c), tuple(a.event_id for a in c)))
    return sets


def search(
    *,
    request: Request,
    profile: Profile,
    curve: Curve,
    amount_safe: float,
    rates=None,
) -> Candidate | None:
    """The smallest set of permitted changes that makes full payment safe today.

    Returns the ``affordable_with_plan`` / ``full_payment`` candidate, whose plan is a single
    payment of the full amount on the request date. ``amount_safe_to_pay`` is *not* touched:
    the contract measures it before the changes, which is why that row's safe amount is
    strictly less than the request while the plan pays in full.
    """
    if not profile.accepts("full_payment"):
        return None
    actions = eligible_actions(profile, curve, rates)
    if not actions:
        return None

    payment = ((request.request_date, request.requested_amount),)
    for combination in _combinations(actions):
        overrides = {action.series_key: action.new_amount for action in combination}
        if len(overrides) != len(combination):
            continue  # two actions on one series cannot both apply
        adjusted = curve.repriced(overrides)
        if safety.is_safe(
            adjusted,
            profile.minimum_balance_to_keep,
            payment,
            through=request.desired_completion_date,
        ):
            return Candidate(
                method="full_payment",
                status="affordable_with_plan",
                payments=payment,
                total_payable=request.requested_amount,
                spending_changes=tuple(action.render() for action in combination),
                completes_by_deadline=True,
                note="full payment becomes safe with permitted spending changes",
            )
    return None
