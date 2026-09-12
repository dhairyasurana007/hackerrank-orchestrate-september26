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
import math
import re
import statistics
from dataclasses import dataclass

from .records import Event

#: A series needs at least this many recorded occurrences before it is projected forward.
#: Conservative by design: a false positive invents an expense and suppresses affordability.
MIN_OCCURRENCES = 3

#: Relaxed to this when *every* recorded amount in the series is identical. Two identical
#: payments on the same day-of-month is materially stronger evidence of an established
#: recurring amount than two unrelated ones, and the distinction is load-bearing: user_15's
#: first-job payroll is EUR 1,661 twice and has to project, while user_11's commission is
#: IDR 20.0M then 8.5M and must not, because a message says commissions are unapproved.
#:
#: This is *not* the variance gate PLAN.md 5.5 rules out. It applies only to a series with
#: exactly two occurrences — the weakest evidence there is — and never to one with three or
#: more, so it cannot reach user_08's legitimately variable five-occurrence salary.
MIN_OCCURRENCES_IF_IDENTICAL = 2

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

#: Amounts are estimated from at most this many recent occurrences.
AMOUNT_WINDOW = 6

#: Expense series are forecast at this percentile of their recent observations — the spec
#: asks for essential variable spending to be forecast conservatively, and a high percentile
#: is how that is expressed without the over-conservatism of the maximum. Measured on the 21
#: exact-trough samples: mean 14.54% median error, p75 11.25%, p90 10.40%, max 11.43%. p75
#: wins on the mean (28.97%) and on the within-25% count (16 of 21) as well, which is why it
#: is preferred to p90's marginally better median.
EXPENSE_PERCENTILE = 0.75

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


def _percentile(values: list[float], fraction: float) -> float:
    """Linear-interpolated percentile. Small samples, so no need for anything cleverer."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _estimate_amount(occurrences: list[Event], amounts: dict[str, float]) -> float | None:
    """The forward amount for a series, from its most recent observations.

    Asymmetric on purpose, because conservatism is asymmetric: an expense series is forecast
    at a high percentile, while an income series is forecast at its *mean*. Taking the
    maximum of recent income measured better on the samples (median 8.61% against 11.25%),
    and is rejected anyway — it over-states income, which the spec forbids, and the single
    sample it rescues (request_08, whose salary runs EUR 1,422.85 four times then 782.57) is
    one whose correct forward amount is stated in a message and belongs to the evidence
    layer, not to a statistic that happens to land on it.
    """
    values = [amounts[event.event_id] for event in occurrences if event.event_id in amounts]
    recent = values[-AMOUNT_WINDOW:]
    if not recent:
        return None
    if occurrences[-1].direction == "debit":
        return _percentile(recent, EXPENSE_PERCENTILE)
    return statistics.fmean(recent)


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

    observed = [amounts[event.event_id] for event in occurrences if event.event_id in amounts]
    identical = len(observed) >= 2 and len(set(round(value, 6) for value in observed)) == 1
    needed = MIN_OCCURRENCES_IF_IDENTICAL if identical else MIN_OCCURRENCES
    if len(occurrences) < needed:
        return rejected(f"only {len(occurrences)} occurrence(s); needs {needed}")
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
    if series.cadence == "one_off":
        # A single dated exception, which is how a "next payment only" amendment is carried.
        return (series.next_expected,) if start < series.next_expected <= end else ()
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


def confirmed_income_series(movements, detected, *, as_of: dt.date) -> tuple[Series, ...]:
    """Seed a monthly series from a ``scheduled`` credit that has no history to detect.

    PLAN.md assumption 4: confirmed salary recurs monthly from its scheduled settlement date
    at the same amount unless amended by evidence. This is the case where history alone says
    nothing — user_01 has exactly one prior salary row, described "Prorated first salary",
    and one scheduled row. Without this seeding the forecast injects income once and then
    runs 75 days of pure expense, which over-forecast that sample's drawdown by 106% and
    broke its upper bound. A `scheduled` row is affirmative confirmation from the source, so
    it is the one place the engine may project income with no series behind it.

    Seeded only where no detected series already projects for the same
    ``(direction, category)``: user_13 has both a projecting salary series and a scheduled
    row for it, and seeding there would double-count every month after the first.
    """
    covered = {(s.direction, s.category) for s in detected if s.projects and s.is_income}
    seeded = []
    for movement in movements:
        event = movement.event
        if event.status != "scheduled" or event.direction != "credit" or movement.amount <= 0:
            continue
        key = (event.direction, event.category)
        if key in covered:
            continue
        covered.add(key)
        anchor = movement.when.day
        seeded.append(
            Series(
                key=("credit", event.category, normalise_description(event.description)),
                direction="credit",
                category=event.category,
                description_key=normalise_description(event.description),
                cadence="monthly",
                interval_days=30,
                anchor_day=anchor,
                last_date=movement.when,
                next_expected=_next_monthly(movement.when, anchor),
                amount=movement.amount,
                occurrences=1,
                projects=True,
                reason="confirmed by a scheduled row; recurs monthly from its settlement date",
                representative=event,
            )
        )
    return tuple(seeded)
