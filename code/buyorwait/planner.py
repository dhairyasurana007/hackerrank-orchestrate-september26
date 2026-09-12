"""Candidate enumeration, eligibility gating and the six-level ranking (TASKS.md M9).

Every eligible way of completing the request is enumerated, the unsafe ones are dropped, and
the survivors are ranked by the published preference order (PLAN.md 5.3):

1. completes by ``desired_completion_date``
2. needs no spending changes
3. lowest ``total_payable_amount``
4. starts earliest
5. fewest payments
6. lowest ``payment_option_id``

Eligibility is gated on ``payment_methods_user_will_consider`` and ``max_installment_months``
*before* safety is tested — a plan the user would not accept is not a plan, however safe.

**Two independences that the samples make explicit and that are easy to get wrong.**

``amount_safe_to_pay`` and ``earliest_date_for_full_payment`` measure financial capacity and
are computed without reference to the user's method preferences. So:

* ``earliest_date_for_full_payment`` can equal ``request_date`` while the recommendation is
  ``installments`` — request_12, where full payment today is safe but the user will not
  consider full payment at all. ``affordable_now`` implies ``earliest == request_date``, but
  the converse does not hold, and treating it as a biconditional would mis-label that row.
* ``earliest_date_for_full_payment`` can fall *after* ``desired_completion_date`` —
  request_06, request_11 and request_21, the last by 33 days.

The one place the contract does override the computed figure is ``not_recommended``: there
the emitted plan is ``none`` and the emitted date is empty, always. That case only arises
when a date exists but the user will not consider the method that would use it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from . import safety
from .forecast import Curve
from .records import PaymentOption, Profile, Request

#: Money comparisons are made to the cent; below that is float representation noise.
EPSILON = 1e-6


@dataclass(frozen=True)
class Candidate:
    """One way of completing the request, with everything ranking needs to compare it."""

    method: str
    status: str
    payments: tuple[tuple[dt.date, float], ...]
    total_payable: float
    option: PaymentOption | None = None
    spending_changes: tuple[str, ...] = ()
    completes_by_deadline: bool = True
    note: str = ""

    @property
    def start(self) -> dt.date | None:
        return self.payments[0][0] if self.payments else None

    @property
    def payment_count(self) -> int:
        return len(self.payments)

    @property
    def option_id(self) -> str:
        return self.option.payment_option_id if self.option else ""

    def rank_key(self, request: Request):
        """The six criteria, as a sort key. Lower is better on every component."""
        return (
            0 if self.completes_by_deadline else 1,
            1 if self.spending_changes else 0,
            self.total_payable,
            self.start or request.request_date,
            self.payment_count,
            # An option-less candidate has no id to tie-break on; sorting it first keeps the
            # comparison total without letting the empty string beat a real id on cost.
            self.option_id or "",
        )


@dataclass
class Decision:
    """The chosen candidate plus the two capacity figures, which stand on their own."""

    request: Request
    amount_safe_to_pay: float
    earliest_full_payment: dt.date | None
    chosen: Candidate
    considered: tuple[Candidate, ...] = ()
    rejected: tuple[tuple[str, str], ...] = ()  # (what, why)
    curve: Curve | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def emitted_earliest(self) -> dt.date | None:
        """``not_recommended`` emits no date, whatever capacity says (PLAN.md 2)."""
        if self.chosen.method == "not_recommended":
            return None
        return self.earliest_full_payment


def eligible_installment_options(request: Request, profile: Profile, options) -> tuple:
    """Supplied installment options the user would actually consider.

    Blank ``max_installment_months`` rejects every installment option outright, which is 119
    of the 275 profiles. ``payment_frequency_days`` is only ever 28, 30 or 31 in this dataset
    — never weekly — so one payment is one month and comparing ``number_of_payments`` against
    a month cap is sound.
    """
    if not profile.considers_installments:
        return ()
    cap = profile.max_installment_months
    return tuple(
        option
        for option in options
        if option.payment_method == "installments" and option.number_of_payments <= cap
    )


def _completes_by(payments, deadline: dt.date) -> bool:
    return bool(payments) and payments[-1][0] <= deadline


def _safety_horizon(request: Request, payments) -> dt.date:
    """A plan must hold from now through the later of the deadline and its last payment."""
    latest = max((when for when, _ in payments), default=request.request_date)
    return max(request.desired_completion_date, latest)


def full_payment_candidate(request: Request, profile: Profile, options) -> Candidate | None:
    """Pay the whole amount on the request date."""
    if not profile.accepts("full_payment"):
        return None
    option = next((o for o in options if o.payment_method == "full_payment"), None)
    total = option.total_payable_amount if option else request.requested_amount
    return Candidate(
        method="full_payment",
        status="affordable_now",
        payments=((request.request_date, request.requested_amount),),
        total_payable=total,
        option=option,
        completes_by_deadline=True,
    )


def installment_candidates(request: Request, profile: Profile, options) -> list[Candidate]:
    candidates = []
    for option in eligible_installment_options(request, profile, options):
        schedule = option.schedule()
        candidates.append(
            Candidate(
                method="installments",
                status="affordable_with_plan",
                payments=schedule,
                total_payable=option.total_payable_amount,
                option=option,
                completes_by_deadline=_completes_by(schedule, request.desired_completion_date),
            )
        )
    return candidates


def partial_payment_candidate(
    request: Request,
    profile: Profile,
    curve: Curve,
    minimum_balance: float,
    amount_safe: float,
) -> Candidate | None:
    """Pay what is safe today, then the remainder on the first safe day before the deadline.

    Recommended only when the request allows partial payment, the user accepts it, the safe
    amount is a strict fraction of the request, and the second payment lands on or before
    ``desired_completion_date``. The two payments sum to exactly ``requested_amount``.
    """
    if not request.allows_partial_payment or not profile.accepts("partial_payment"):
        return None
    if not (EPSILON < amount_safe < request.requested_amount - EPSILON):
        return None
    remainder = request.requested_amount - amount_safe
    # The first payment shifts the curve, and the second is judged against the shifted one.
    shifted = curve.shifted([(request.request_date, amount_safe)])
    second = safety.earliest_full_payment_date(
        shifted, minimum_balance, remainder, through=request.desired_completion_date
    )
    if second is None or second <= request.request_date or second > request.desired_completion_date:
        return None
    payments = ((request.request_date, amount_safe), (second, remainder))
    return Candidate(
        method="partial_payment",
        status="affordable_with_plan",
        payments=payments,
        total_payable=request.requested_amount,
        completes_by_deadline=True,
    )


def wait_candidate(
    request: Request, profile: Profile, earliest: dt.date | None
) -> Candidate | None:
    """Pay the whole amount later, in one payment, on the first date capacity allows.

    The plan is exactly one payment of the full amount on that date — never ``none``. Six of
    the 25 samples take this branch.
    """
    if not profile.accepts("full_payment"):
        return None
    if earliest is None or earliest <= request.request_date:
        return None
    return Candidate(
        method="wait",
        status="affordable_later",
        payments=((earliest, request.requested_amount),),
        total_payable=request.requested_amount,
        completes_by_deadline=earliest <= request.desired_completion_date,
    )


def not_recommended_candidate(request: Request) -> Candidate:
    return Candidate(
        method="not_recommended",
        status="not_affordable",
        payments=(),
        total_payable=request.requested_amount,
        completes_by_deadline=False,
        note="nothing eligible is safe",
    )


def decide(
    *,
    request: Request,
    profile: Profile,
    curve: Curve,
    options,
    spending_change_search=None,
) -> Decision:
    """Enumerate, gate, drop the unsafe, and rank."""
    horizon = request.desired_completion_date
    floor = profile.minimum_balance_to_keep
    amount_safe = safety.safe_amount_today(curve, floor, request.requested_amount, through=horizon)
    earliest = safety.earliest_full_payment_date(
        curve, floor, request.requested_amount, through=horizon
    )

    proposals: list[Candidate] = []
    rejected: list[tuple[str, str]] = []

    full_now = full_payment_candidate(request, profile, options)
    if full_now is None:
        rejected.append(("full_payment", "not in payment_methods_user_will_consider"))
    elif amount_safe >= request.requested_amount - EPSILON:
        proposals.append(full_now)
    else:
        rejected.append(
            ("full_payment", f"only {amount_safe:.2f} of {request.requested_amount:.2f} is safe today")
        )

    if not profile.considers_installments:
        reason = (
            "max_installment_months is blank"
            if profile.max_installment_months is None
            else "installments not in payment_methods_user_will_consider"
        )
        rejected.append(("installments", reason))
    for option in options:
        if option.payment_method != "installments":
            continue
        cap = profile.max_installment_months
        if not profile.accepts("installments") or cap is None:
            continue
        if option.number_of_payments > cap:
            rejected.append(
                (option.payment_option_id, f"{option.number_of_payments} payments exceeds the cap of {cap}")
            )
    for candidate in installment_candidates(request, profile, options):
        if safety.is_safe(
            curve, floor, candidate.payments, through=_safety_horizon(request, candidate.payments)
        ):
            proposals.append(candidate)
        else:
            rejected.append((candidate.option_id, "schedule breaches the minimum balance"))

    partial = partial_payment_candidate(request, profile, curve, floor, amount_safe)
    if partial is not None:
        proposals.append(partial)
    elif request.allows_partial_payment and profile.accepts("partial_payment"):
        rejected.append(("partial_payment", "no second payment date on or before the deadline"))
    elif not request.allows_partial_payment:
        rejected.append(("partial_payment", "the request does not allow partial payment"))
    else:
        rejected.append(("partial_payment", "not in payment_methods_user_will_consider"))

    if spending_change_search is not None and amount_safe < request.requested_amount - EPSILON:
        changed = spending_change_search(
            request=request, profile=profile, curve=curve, amount_safe=amount_safe
        )
        if changed is not None:
            proposals.append(changed)

    waiting = wait_candidate(request, profile, earliest)
    if waiting is not None:
        proposals.append(waiting)

    if not proposals:
        chosen = not_recommended_candidate(request)
        proposals = [chosen]
    else:
        chosen = min(proposals, key=lambda c: c.rank_key(request))

    return Decision(
        request=request,
        amount_safe_to_pay=amount_safe,
        earliest_full_payment=earliest,
        chosen=chosen,
        considered=tuple(proposals),
        rejected=tuple(rejected),
        curve=curve,
    )
