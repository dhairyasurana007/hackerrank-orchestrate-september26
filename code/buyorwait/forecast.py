"""The 90-day daily balance curve (TASKS.md M7a, PLAN.md 5.2).

One curve answers every question the engine asks, which is what guarantees no two reported
numbers can disagree. The curve is a pure function of the user's state: ``C[i]`` is the
projected balance at the end of day ``request_date + i``, for ``i`` in ``0..89``.

Because every explicit forward movement in the dataset falls strictly after ``request_date``
and ``current_available_balance`` already reflects all settled history, ``C[0]`` is exactly
the opening balance.

The window is 90 days, and the data confirms that is enough for the questions asked of it:
across all 250 requests the furthest ``desired_completion_date`` is 86 days out, and across
the solved samples the furthest ``earliest_date_for_full_payment`` is 69. Installment
schedules can run far past it — up to 727 days — so a plan's payments beyond day 89 are not
floor-checked; PLAN.md 5.2 scopes the safety predicate to the forecast window deliberately,
since projecting recurrence two years forward compounds error faster than it adds signal.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .cashstate import CashMovement, explicit_movements
from .contract import FORECAST_DAYS


@dataclass
class Curve:
    """A daily balance curve and the movements that shaped it."""

    start: dt.date
    opening: float
    values: list[float]
    explicit: tuple[CashMovement, ...] = ()
    notes: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.values)

    @property
    def end(self) -> dt.date:
        return self.start + dt.timedelta(days=len(self.values) - 1)

    def date_at(self, index: int) -> dt.date:
        return self.start + dt.timedelta(days=index)

    def index_of(self, when: dt.date) -> int | None:
        """The curve index for a date, or ``None`` when it falls outside the window."""
        offset = (when - self.start).days
        return offset if 0 <= offset < len(self.values) else None

    def minimum(self) -> float:
        return min(self.values)

    def trough_index(self) -> int:
        return min(range(len(self.values)), key=lambda i: (self.values[i], i))

    def trough_date(self) -> dt.date:
        return self.date_at(self.trough_index())

    def drawdown(self) -> float:
        """Depth of the trough below the opening balance — the figure the harness grades."""
        return self.opening - self.minimum()

    def suffix_minima(self) -> list[float]:
        """``S[i] = min(C[i:])``, computed right to left. Non-decreasing in ``i``."""
        minima = [0.0] * len(self.values)
        running = float("inf")
        for index in range(len(self.values) - 1, -1, -1):
            running = min(running, self.values[index])
            minima[index] = running
        return minima

    def shifted(self, payments) -> "Curve":
        """The curve with payments applied: each lowers every point from its date onward.

        Payments dated outside the window are ignored rather than clamped, which keeps the
        shift identity exact for everything inside it.
        """
        values = list(self.values)
        for when, amount in payments:
            index = self.index_of(when)
            if index is None:
                continue
            for position in range(index, len(values)):
                values[position] -= amount
        return Curve(
            start=self.start,
            opening=self.opening,
            values=values,
            explicit=self.explicit,
            notes=list(self.notes),
        )


def _accumulate(start: dt.date, opening: float, days: int, deltas: dict[int, float]) -> list[float]:
    values = []
    balance = opening
    for index in range(days):
        balance += deltas.get(index, 0.0)
        values.append(balance)
    return values


def build_explicit(
    *,
    request,
    profile,
    events,
    rates,
    events_by_id=None,
    amounts=None,
    days: int = FORECAST_DAYS,
) -> Curve:
    """The curve from explicit future events alone — no recurrence projection whatsoever.

    Pending debits reserved on their settlement date, scheduled debits and credits applied.
    A user whose every event predates ``request_date`` gets a flat curve here, and therefore
    zero drawdown; that is correct at this stage and it is what makes the size of the
    projection job visible.
    """
    movements = explicit_movements(
        events,
        home_currency=profile.home_currency,
        rates=rates,
        events_by_id=events_by_id,
        amounts=amounts,
    )
    deltas: dict[int, float] = {}
    kept = []
    for movement in movements:
        offset = (movement.when - request.request_date).days
        if not 0 <= offset < days:
            continue
        deltas[offset] = deltas.get(offset, 0.0) + movement.amount
        kept.append(movement)
    return Curve(
        start=request.request_date,
        opening=profile.current_available_balance,
        values=_accumulate(request.request_date, profile.current_available_balance, days, deltas),
        explicit=tuple(kept),
    )
