"""Recurrence detection and income gating (TASKS.md M6, PLAN.md 5.5).

This is the single largest accuracy lever in the build, and the diagnosis behind it is
specific: every catastrophic sample error traced to *income* projection, not to expense
conservatism. Four decisions follow from it.

**1. Series identity splits by direction.** Income series key on
``(category, normalised description)`` so base pay, commission, second-household income and
gig payouts stay separate — ``salary`` otherwise lumps together series with completely
different forward semantics. Expenses key on ``category`` alone, because measurement showed
description-keying *fragments* expense series and makes accuracy worse (median 21.6% ->
32.6%): a grocery series legitimately varies its description week to week.

**2. A series projects only while it is ongoing.** The staleness test is not a multiple of
the interval but a missed occurrence: *has the next occurrence this series' own cadence
predicts already fallen, with nothing recorded?* That is what separates the four diagnosed
terminations from the guard case, and it does so on each series' own phase rather than on a
tuned constant:

======================================  ==============================  =============
series                                  next expected vs request_date    verdict
======================================  ==============================  =============
user_05 "Payroll credit"                2025-10-15 < 2025-11-06          stale
user_11 "Performance commission"        2025-03-24 < 2025-05-03          stale
user_13 "Second household income"       2024-02-20 < 2024-03-07          stale
user_08 "Payroll credit"                2025-02-15 > 2025-02-07          ongoing
======================================  ==============================  =============

**3. Termination wording suppresses.** ``final`` or ``last`` in an income description is the
only signal user_05 gives that employment ended; ``status`` says nothing.

**4. No variance gate.** A coefficient-of-variation test fixed every diagnosed case and
regressed user_08 from +19% to +895%, because that user's salary legitimately runs
EUR 1,422.85 then 782.57 and still continues. Variance cannot separate "irregular but
ongoing" from "not coming back"; description semantics and message evidence can. Nothing in
this module may key on amount variability.
"""

from __future__ import annotations

import collections
import datetime as dt
import re
import statistics
from dataclasses import dataclass

from .records import Event

#: A series needs at least this many recorded occurrences before it is projected forward.
#: Conservative by design: a false positive invents an expense and suppresses affordability.
MIN_OCCURRENCES = 3

#: Interval band a series must fall inside to be projected, in days. The upper bound is set
#: just above a month; anything slower than that produces at most two occurrences in a
#: 90-day window and is better treated as one-off.
MIN_INTERVAL_DAYS = 2
MAX_INTERVAL_DAYS = 35

#: A median gap in this band means "the same day every month", and is phase-anchored to the
#: modal day-of-month rather than stepped, because month lengths differ.
MONTHLY_BAND = (26, 32)

#: Fraction of consecutive gaps that must sit near the median for a cadence to be accepted.
CADENCE_CONSISTENCY = 0.6

#: How far a single gap may stray from the median and still count as on-cadence.
def _gap_tolerance(interval: int) -> int:
    return max(2, round(interval * 0.25))

#: Amounts are estimated from at most this many recent occurrences, at their mean.
#: Conservatism lives in the gating, not in inflating the amount: blanket max-selection was
#: measured and made accuracy worse.
AMOUNT_WINDOW = 6

#: Event types that can form a recurring series. Refunds and investment rows are one-offs.
RECURRING_EVENT_TYPES = ("expense", "subscription", "income", "debt_payment")

#: Wording that marks an income series as ended. The only signal user_05 gives.
_TERMINATION = re.compile(r"\b(final|last|closing|終|terakhir)\b", re.IGNORECASE)

_DESCRIPTION_NOISE = re.compile(r"[^a-z\s]+")


def normalise_description(text: str) -> str:
    """Collapse a description to its identity, so amounts and refs cannot fragment a series."""
    return " ".join(_DESCRIPTION_NOISE.sub(" ", (text or "").lower()).split())


def describes_termination(text: str) -> bool:
    return bool(_TERMINATION.search(text or ""))


def _grace_days(interval: int) -> int:
    """Jitter a series is allowed before a missed occurrence counts as termination.

    Scaled to the cadence and capped: a week of slack on a monthly series, two days on a
    weekly one. Capping matters — a grace of half the interval would have let user_13's
    second household income through by a single day.
    """
    return min(7, max(2, interval // 3))


@dataclass(frozen=True)
class Series:
    """A detected recurring series, and whether it projects forward."""

    key: tuple
    direction: str
    category: str
    description_key: str
    cadence: str
    interval_days: int
    anchor_day: int | None  # modal day-of-month, for monthly series
    last_date: dt.date
    next_expected: dt.date
    amount: float  # home currency
    occurrences: int
    projects: bool
    reason: str
    representative: Event

    @property
    def is_income(self) -> bool:
        return self.direction == "credit"

    def signed_amount(self) -> float:
        return self.amount if self.is_income else -self.amount


def _classify_cadence(gaps: list[int]) -> tuple[str, int] | None:
    """Name the cadence a gap sequence follows, or ``None`` if it follows none.

    Deliberately *not* a fixed menu of weekly/fortnightly/monthly. Measured against the
    dataset, a fixed menu rejected 280 debit series that are perfectly regular at other
    intervals — user_11's groceries land every 10 days and that user's dining every 21,
    both with zero jitter. Rejecting them silently dropped real recurring spending from
    every one of those forecasts. Any consistent interval inside the band is accepted; only
    the monthly band is treated specially, because month lengths differ and the phase has to
    come from the day-of-month rather than from a step.
    """
    if not gaps:
        return None
    median = int(round(statistics.median(gaps)))
    if not MIN_INTERVAL_DAYS <= median <= MAX_INTERVAL_DAYS:
        return None
    tolerance = _gap_tolerance(median)
    near = sum(1 for gap in gaps if abs(gap - median) <= tolerance)
    if near / len(gaps) < CADENCE_CONSISTENCY:
        return None
    if MONTHLY_BAND[0] <= median <= MONTHLY_BAND[1]:
        return "monthly", 30
    return f"every {median}d", median


def _modal_day(dates: list[dt.date]) -> int | None:
    """The day-of-month a monthly series lands on.

    Phase beats amount: mis-phasing salary by one cycle moves the trough by a full month of
    net flow, while a 10% amount error barely moves it. Anchoring to the modal day rather
    than to last-occurrence-plus-median-gap measurably improved accuracy (25.6% -> 22.6%).
    """
    counts = collections.Counter(date.day for date in dates)
    best = counts.most_common(1)[0]
    return best[0] if best[1] >= 2 else dates[-1].day


def _next_monthly(after: dt.date, day: int) -> dt.date:
    """The first date strictly after ``after`` landing on day-of-month ``day``.

    Clamped to the length of the month, so a series anchored on the 31st still lands in
    February.
    """
    year, month = after.year, after.month
    for _ in range(14):
        last_day = _days_in_month(year, month)
        candidate = dt.date(year, month, min(day, last_day))
        if candidate > after:
            return candidate
        month += 1
        if month > 12:
            month, year = 1, year + 1
    raise AssertionError("unreachable: a monthly anchor always recurs within a year")


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (dt.date(year, month + 1, 1) - dt.timedelta(days=1)).day


def _estimate_amount(occurrences: list[Event], amounts: dict[str, float]) -> float | None:
    """Mean of the most recent observations, conservative by gating rather than by inflation."""
    values = [amounts[event.event_id] for event in occurrences if event.event_id in amounts]
    recent = values[-AMOUNT_WINDOW:]
    return statistics.fmean(recent) if recent else None


def detect(
    events,
    *,
    as_of: dt.date,
    amounts: dict[str, float],
) -> tuple[Series, ...]:
    """Detect every recurring series in a user's settled history.

    ``amounts`` maps ``event_id`` to the home-currency amount, so this module never has to
    know about FX. Events with no entry there — a blank amount with nothing recovered — are
    excluded from the estimate rather than counted as zero.
    """
    groups: dict[tuple, list[Event]] = {}
    for event in events:
        if event.status != "settled" or event.direction not in ("debit", "credit"):
            continue
        if event.event_type not in RECURRING_EVENT_TYPES:
            continue
        if event.cash_date > as_of:
            continue
        key = series_key(event)
        groups.setdefault(key, []).append(event)

    series = []
    for key, occurrences in sorted(groups.items(), key=lambda item: str(item[0])):
        occurrences.sort(key=lambda e: (e.cash_date, e.event_id))
        detected = _build(key, occurrences, as_of=as_of, amounts=amounts)
        if detected is not None:
            series.append(detected)
    return tuple(series)


def series_key(event: Event) -> tuple:
    """Income keys on category *and* description; expenses on category alone."""
    if event.direction == "credit":
        return ("credit", event.category, normalise_description(event.description))
    return ("debit", event.category, "")


def _build(key, occurrences, *, as_of: dt.date, amounts) -> Series | None:
    dates = [event.cash_date for event in occurrences]
    representative = occurrences[-1]
    direction = representative.direction

    gaps = [(dates[index + 1] - dates[index]).days for index in range(len(dates) - 1)]
    cadence = _classify_cadence(gaps)
    amount = _estimate_amount(occurrences, amounts)

    def rejected(reason: str, interval: int = 0, anchor=None, expected=None) -> Series:
        return Series(
            key=key,
            direction=direction,
            category=representative.category,
            description_key=key[2],
            cadence=cadence[0] if cadence else "none",
            interval_days=interval,
            anchor_day=anchor,
            last_date=dates[-1],
            next_expected=expected or dates[-1],
            amount=amount or 0.0,
            occurrences=len(occurrences),
            projects=False,
            reason=reason,
            representative=representative,
        )

    if len(occurrences) < MIN_OCCURRENCES:
        return rejected(f"only {len(occurrences)} occurrence(s); needs {MIN_OCCURRENCES}")
    if cadence is None:
        return rejected("no stable interval")
    if amount is None:
        return rejected("no usable amount in the series' history")

    name, interval = cadence
    anchor = _modal_day(dates) if name == "monthly" else None
    if name == "monthly":
        next_expected = _next_monthly(dates[-1], anchor)
    else:
        next_expected = dates[-1] + dt.timedelta(days=interval)

    # A missed occurrence, judged on this series' own phase, is what marks termination.
    if next_expected + dt.timedelta(days=_grace_days(interval)) < as_of:
        return rejected(
            f"stale: expected {next_expected.isoformat()} but nothing recorded since "
            f"{dates[-1].isoformat()}",
            interval,
            anchor,
            next_expected,
        )

    if direction == "credit" and describes_termination(representative.description):
        return rejected(
            f"description marks the series as ended: {representative.description!r}",
            interval,
            anchor,
            next_expected,
        )

    return Series(
        key=key,
        direction=direction,
        category=representative.category,
        description_key=key[2],
        cadence=name,
        interval_days=interval,
        anchor_day=anchor,
        last_date=dates[-1],
        next_expected=next_expected,
        amount=amount,
        occurrences=len(occurrences),
        projects=True,
        reason="recurring and ongoing",
        representative=representative,
    )


def occurrences_in_window(series: Series, start: dt.date, end: dt.date) -> tuple[dt.date, ...]:
    """The dates a projecting series lands on within ``(start, end]``.

    Monthly series are anchored to their modal day-of-month; sub-monthly series step by
    their own interval from the last recorded occurrence.
    """
    if not series.projects:
        return ()
    dates = []
    if series.cadence == "monthly":
        current = _next_monthly(series.last_date, series.anchor_day or series.last_date.day)
        while current <= end:
            if current > start:
                dates.append(current)
            current = _next_monthly(current, series.anchor_day or current.day)
    else:
        current = series.last_date + dt.timedelta(days=series.interval_days)
        while current <= end:
            if current > start:
                dates.append(current)
            current = current + dt.timedelta(days=series.interval_days)
    return tuple(dates)
