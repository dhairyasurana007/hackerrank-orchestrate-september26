"""Per-request state reconstruction: the inputs one decision needs, normalised once.

Two jobs live here. The first is FX: every amount a user's events carry is converted to the
home currency exactly once, keyed by ``event_id``, so no module downstream has to know
about currencies.

The second is the blank ``amount``. **A blank amount is never zero** (PLAN.md assumption 10).
Sixteen events carry one, each linked to a document image. Until vision extraction lands
(FINAL, F1) the figure is recovered from the conservative supported estimate for the event's
own recurring group, and the fallback is recorded on the state so it appears in the decision
bundle rather than disappearing into an average.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass, field

from .records import Event, Profile, Request


@dataclass
class UserState:
    """Everything the forecaster and plan engine read for one request."""

    request: Request
    profile: Profile
    events: tuple[Event, ...]
    events_by_id: dict[str, Event]
    amounts: dict[str, float]  # event_id -> home-currency amount
    estimated: dict[str, str] = field(default_factory=dict)  # event_id -> how it was recovered
    unresolved: tuple[str, ...] = ()  # blank amounts nothing could price

    @property
    def home_currency(self) -> str:
        return self.profile.home_currency

    @property
    def minimum_balance(self) -> float:
        return self.profile.minimum_balance_to_keep

    @property
    def opening_balance(self) -> float:
        return self.profile.current_available_balance


def _group_key(event: Event) -> tuple:
    return (event.user_id, event.direction, event.category)


def build_state(
    request: Request,
    profile: Profile,
    events,
    rates,
    *,
    recovered: dict[str, float] | None = None,
) -> UserState:
    """Normalise one user's records for one request.

    ``recovered`` supplies exact amounts from evidence (a message amendment, or a document
    image in FINAL). Anything it does not cover falls back to the recurring-group estimate.
    """
    events = tuple(events)
    recovered = recovered or {}
    amounts: dict[str, float] = {}
    estimated: dict[str, str] = {}

    # Settled history in the same group, priced in the home currency, is the fallback basis.
    history: dict[tuple, list[float]] = {}
    for event in events:
        if event.amount is None or event.status != "settled":
            continue
        if event.cash_date > request.request_date:
            continue
        converted = rates.convert_event_amount(event, profile.home_currency)
        history.setdefault(_group_key(event), []).append(converted)

    unresolved = []
    for event in events:
        if event.event_id in recovered:
            amounts[event.event_id] = rates.convert(
                recovered[event.event_id], event.currency, profile.home_currency, event.cash_date
            )
            estimated[event.event_id] = "recovered from evidence"
            continue
        if event.amount is not None:
            amounts[event.event_id] = rates.convert_event_amount(event, profile.home_currency)
            continue
        basis = history.get(_group_key(event))
        if basis:
            # Mean of the group, matching how every other recurring amount is estimated, so a
            # recovered blank cannot quietly become the largest figure in its own series.
            amounts[event.event_id] = statistics.fmean(basis[-6:])
            estimated[event.event_id] = (
                f"estimated from {len(basis[-6:])} recent {event.category} row(s) "
                f"in the absence of the linked document"
            )
        else:
            unresolved.append(event.event_id)

    return UserState(
        request=request,
        profile=profile,
        events=events,
        events_by_id={event.event_id: event for event in events},
        amounts=amounts,
        estimated=estimated,
        unresolved=tuple(unresolved),
    )


def window_end(request: Request, days: int) -> dt.date:
    return request.request_date + dt.timedelta(days=days - 1)
