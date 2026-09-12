"""The target side of the drawdown harness (PLAN.md 5.4, TASKS.md M5a).

Because a payment on the request date shifts the whole forecast curve down uniformly, the
ground-truth ``amount_safe_to_pay`` pins the depth of the curve's trough below the opening
balance:

    target_drawdown = current_available_balance - (amount_safe_to_pay + minimum_balance_to_keep)

That is a single scalar per sample, comparable directly against a forecaster's output, which
is what lets recurrence detection and the variable-spend estimator be tuned against 25 known
values before any plan logic exists.

**The identity is exact only when ``amount_safe_to_pay < requested_amount``.** When the two
are equal the figure was capped by the size of the request, so the derived drawdown is
merely an *upper bound* and scoring it as an equality is wrong. That splits the samples into
21 exact-trough cases and 4 bounded ones (request_01, request_09, request_12, request_16).

This module deliberately depends on nothing but the loaders: it is pure arithmetic over
``sample_requests.csv`` and ``financial_profiles.csv``, with no forecaster involved, so it is
never blocked by forecaster work and can be verified independently of it.
"""

from __future__ import annotations

from dataclasses import dataclass

EXACT = "exact"
CAPPED = "capped"


@dataclass(frozen=True)
class DrawdownTarget:
    """One sample's implied trough depth, and whether that figure is exact or a bound."""

    request_id: str
    user_id: str
    currency: str
    opening_balance: float
    minimum_balance_to_keep: float
    amount_safe_to_pay: float
    requested_amount: float
    target_drawdown: float
    kind: str

    @property
    def is_exact(self) -> bool:
        return self.kind == EXACT

    def relative_error(self, predicted_drawdown: float) -> float:
        """Predicted versus target, scaled by the target.

        Scaled by the target rather than by the balance because the target is what is being
        predicted; a zero target falls back to an absolute comparison so the metric stays
        defined.
        """
        if abs(self.target_drawdown) < 1e-9:
            return abs(predicted_drawdown)
        return (predicted_drawdown - self.target_drawdown) / self.target_drawdown

    def violates_bound(self, predicted_drawdown: float, tolerance: float = 1e-6) -> bool:
        """For a capped sample, the only testable claim: the trough is no deeper than this.

        A deeper predicted drawdown would have forced ``amount_safe_to_pay`` below
        ``requested_amount``, contradicting the ground truth.
        """
        return predicted_drawdown > self.target_drawdown + tolerance


def target_for(request, profile, truth) -> DrawdownTarget:
    """Derive one sample's target. Pure arithmetic over the three supplied records."""
    target = profile.current_available_balance - (
        truth.amount_safe_to_pay + profile.minimum_balance_to_keep
    )
    capped = abs(truth.amount_safe_to_pay - request.requested_amount) < 1e-9
    return DrawdownTarget(
        request_id=request.request_id,
        user_id=request.user_id,
        currency=profile.home_currency,
        opening_balance=profile.current_available_balance,
        minimum_balance_to_keep=profile.minimum_balance_to_keep,
        amount_safe_to_pay=truth.amount_safe_to_pay,
        requested_amount=request.requested_amount,
        target_drawdown=target,
        kind=CAPPED if capped else EXACT,
    )


def targets_from(data) -> tuple[DrawdownTarget, ...]:
    """Every sample's target, in request-id order."""
    return tuple(
        target_for(request, data.profiles[request.user_id], data.sample_truth[request.request_id])
        for request in sorted(data.samples, key=lambda r: r.request_id)
    )


def split(targets) -> tuple[tuple[DrawdownTarget, ...], tuple[DrawdownTarget, ...]]:
    """``(exact, capped)`` — the two sets are scored by different rules and never pooled."""
    return (
        tuple(t for t in targets if t.is_exact),
        tuple(t for t in targets if not t.is_exact),
    )
