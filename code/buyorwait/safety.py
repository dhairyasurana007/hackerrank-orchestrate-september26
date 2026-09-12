"""The safety predicate and the two closed-form solvers (TASKS.md M8, PLAN.md 5.2).

Everything the engine reports comes from one curve, and the two headline figures need no
search at all. Because a payment on day ``d`` lowers the curve uniformly from ``d`` onward:

* ``amount_safe_to_pay = clamp(min(C) - minimum_balance_to_keep, 0, requested_amount)`` —
  a payment today shifts the whole curve down, so the binding constraint is its trough;
* ``earliest_date_for_full_payment`` is the first day ``d`` whose suffix minimum, less the
  requested amount, still clears the floor — one right-to-left pass.

Multi-payment plans have no such shortcut and go through :func:`is_safe`, which replays the
curve with the payments applied.

**Both solvers take a horizon**, because the two figures are asked over different periods
(see ``forecast.py``): the safe amount is asked through ``desired_completion_date``, and a
payment on a day past that deadline is judged from that day onward. The suffix-minimum array
encodes both at once — inside the horizon each entry is the minimum from that day to the
deadline, and beyond it each entry is the day's own balance.

The closed form is cross-checked against a brute-force day-by-day search in the test suite.
That cross-check is what licenses using the closed form at all.
"""

from __future__ import annotations

import datetime as dt

from .forecast import Curve


def is_safe(
    curve: Curve,
    minimum_balance: float,
    payments=(),
    *,
    through: dt.date | None = None,
    tolerance: float = 1e-6,
) -> bool:
    """Does the balance stay at or above the floor on every day inside the horizon?

    Payments dated beyond the curve cannot be checked and are not counted as unsafe; the
    forecast window is the scope of the claim (PLAN.md 5.2).
    """
    shifted = curve.shifted(payments) if payments else curve
    limit = shifted.horizon_index(through)
    for index in range(limit + 1):
        if shifted.values[index] < minimum_balance - tolerance:
            return False
    # A payment landing after the horizon still has to clear the floor on its own day.
    for when, _amount in payments:
        index = shifted.index_of(when)
        if index is not None and index > limit:
            if shifted.values[index] < minimum_balance - tolerance:
                return False
    return True


def safe_amount_today(
    curve: Curve,
    minimum_balance: float,
    cap: float,
    *,
    through: dt.date | None = None,
) -> float:
    """The largest amount payable on ``curve.start`` that keeps the floor, capped at ``cap``.

    Closed form: the trough of the curve over the horizon, less the floor, clamped into
    ``[0, cap]``. ``cap`` is the requested amount, which is what makes the contract's
    ``0 <= amount_safe_to_pay <= requested_amount`` hold by construction.
    """
    headroom = curve.minimum(through) - minimum_balance
    return max(0.0, min(headroom, cap))


def earliest_full_payment_date(
    curve: Curve,
    minimum_balance: float,
    amount: float,
    *,
    through: dt.date | None = None,
    tolerance: float = 1e-6,
) -> dt.date | None:
    """First day a single payment of ``amount`` is safe, or ``None`` if never in the window.

    One right-to-left pass of suffix minima, no search. Empty means no safe full payment
    exists inside the forecast, which is exactly what the contract's empty
    ``earliest_date_for_full_payment`` asserts.
    """
    minima = curve.suffix_minima(through)
    required = minimum_balance + amount
    for index, floor in enumerate(minima):
        if floor >= required - tolerance:
            return curve.date_at(index)
    return None


def brute_force_earliest(
    curve: Curve,
    minimum_balance: float,
    amount: float,
    *,
    through: dt.date | None = None,
    tolerance: float = 1e-6,
) -> dt.date | None:
    """The same answer by rescanning every day from each candidate payment date onward.

    Deliberately naive, and deliberately kept in the shipped module rather than in the test
    file: it is the definition the closed form claims to implement, and the suite asserts the
    two agree over randomised curves.

    Two things it does *not* do, both on purpose:

    * It does not look at days **before** the candidate payment date. A balance that already
      dips below the floor on day 3 is neither helped nor harmed by a payment on day 40, and
      the request cannot repair the past — so ``earliest_date_for_full_payment`` asks only
      whether the payment is survivable from the day it is made. The closed form has the same
      scope, which is the point of the cross-check.
    * It does not reuse :func:`is_safe`, which checks the whole horizon from day zero and
      would therefore answer a different question.

    The comparison is written as ``balance >= floor + amount``, the same form the closed
    version uses, rather than as ``balance - amount >= floor``. Algebraically identical, but
    not in floating point: with balances in the hundreds of thousands and a payment of
    similar size, the subtraction loses enough precision to flip the comparison and the two
    implementations disagree on curves where both are in fact right.
    """
    required = minimum_balance + amount
    limit = curve.horizon_index(through)
    for index in range(len(curve)):
        end = max(limit, index)
        if all(curve.values[day] >= required - tolerance for day in range(index, end + 1)):
            return curve.date_at(index)
    return None
