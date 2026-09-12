"""Deterministic explanation templates (TASKS.md M16, PLAN.md assumption 15).

Six templates, in the families the 25 sample explanations use, each grounded in figures the
engine actually computed. Every wording and every formatting rule below is read off those
rows rather than invented.

**Grounding is enforced, not intended.** A template receives named figures and nothing else,
and :func:`figures_in` re-reads every number out of the rendered sentence so the suite can
assert that no template emits a figure it was not given. That check is what makes the FINAL
version's LLM polish safe to add later: the same function decides whether polished text is
allowed to replace the template.

Two families have a second variant in the samples, and only one of the two is distinguishable
from the inputs:

* ``not_affordable`` uses "Do not proceed with the X request. Although Y is available
  today..." for request_14 and request_24, and "Do not make this payment by D..." for the
  other five. The separator that fits all seven is that the first form appears exactly when
  partial payment is the user's *only* avenue and the request allows it — the one route was
  open and still could not complete.
* ``affordable_now`` uses "This leaves at least X available over the next 90 days" twice and
  "This keeps the X minimum available over the next 90 days" once (request_09), with nothing
  in the inputs separating them. The majority form is used and the variant is recorded here
  rather than guessed at.
"""

from __future__ import annotations

import datetime as dt
import re

#: Amounts inside explanations carry thousands separators, and decimals only when fractional:
#: "ZAR 25,256", "IDR 15,952,906.67", "USD 23.50", "EUR 996.60".
def money(currency: str, amount: float) -> str:
    rounded = round(float(amount) + 0.0, 2)
    if abs(rounded - round(rounded)) < 5e-9:
        return f"{currency} {int(round(rounded)):,}"
    return f"{currency} {rounded:,.2f}"


def long_date(when: dt.date) -> str:
    """"8 August 2025" — no leading zero, which ``%-d`` cannot do portably on Windows."""
    return f"{when.day} {when:%B %Y}"


def describe(event) -> str:
    """An event's own description, lowercased, as the samples phrase it."""
    return (event.description or "commitment").strip().lower()


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def figures_in(text: str) -> set[str]:
    """Every number in a rendered explanation, normalised for comparison.

    Used by the suite to assert that a template emitted only figures it was given, and by
    FINAL's polish step to decide whether rewritten text may replace the template.
    """
    found = set()
    for match in _NUMBER.finditer(text or ""):
        raw = match.group(0).replace(",", "")
        value = float(raw)
        found.add(f"{value:.2f}".rstrip("0").rstrip("."))
    return found


def _action_phrase(action_texts, currency: str, changes) -> str:
    """"Stop the online backup subscription and reduce the streaming subscription to USD 23.50"."""
    phrases = []
    for text, event in zip(action_texts, changes):
        if text.startswith("stop:"):
            phrases.append(f"stop the {describe(event)}")
        else:
            amount = float(text.split(":")[2])
            phrases.append(f"reduce the {describe(event)} to {money(currency, amount)}")
    if len(phrases) == 1:
        joined = phrases[0]
    elif len(phrases) == 2:
        joined = f"{phrases[0]} and {phrases[1]}"
    else:
        joined = ", ".join(phrases[:-1]) + f" and {phrases[-1]}"
    return joined[0].upper() + joined[1:]


def affordable_now(*, currency: str, requested: float, minimum: float) -> str:
    return (
        f"Pay {money(currency, requested)} today. "
        f"This leaves at least {money(currency, minimum)} available over the next 90 days."
    )


def wait_until(*, currency: str, requested: float, minimum: float, when: dt.date) -> str:
    return (
        f"Pay {money(currency, requested)} in full on {long_date(when)}. "
        f"Paying earlier would take the balance below the {money(currency, minimum)} minimum."
    )


def installments(*, currency: str, count: int, payment: float, minimum: float, start: dt.date) -> str:
    return (
        f"Use {count} installments of {money(currency, payment)}, starting {long_date(start)}. "
        f"This leaves at least {money(currency, minimum)} available."
    )


def partial_payment(
    *, currency: str, today: float, remainder: float, minimum: float, when: dt.date
) -> str:
    return (
        f"Pay {money(currency, today)} today and the remaining {money(currency, remainder)} "
        f"on {long_date(when)}. This completes the full request and keeps the "
        f"{money(currency, minimum)} minimum protected."
    )


def with_spending_changes(
    *, currency: str, requested: float, minimum: float, action_texts, changed_events
) -> str:
    return (
        f"{_action_phrase(action_texts, currency, changed_events)}, then pay "
        f"{money(currency, requested)} today. "
        f"This leaves at least {money(currency, minimum)} available."
    )


def not_affordable_by_deadline(*, currency: str, minimum: float, deadline: dt.date) -> str:
    return (
        f"Do not make this payment by {long_date(deadline)}. "
        f"None of the available options keeps the {money(currency, minimum)} minimum protected."
    )


def not_affordable_partial_only(*, currency: str, requested: float, available: float) -> str:
    return (
        f"Do not proceed with the {money(currency, requested)} request. "
        f"Although {money(currency, available)} is available today, the full amount cannot be "
        f"completed safely within 90 days."
    )


def render(decision, request, profile) -> str:
    """The explanation for a decision, from the family its shape belongs to."""
    currency = profile.home_currency
    minimum = profile.minimum_balance_to_keep
    chosen = decision.chosen

    if chosen.method == "not_recommended":
        # See the module docstring: the second form appears exactly where partial payment was
        # the user's only avenue and the request allowed it.
        partial_only = (
            tuple(profile.payment_methods_user_will_consider) == ("partial_payment",)
            and request.allows_partial_payment
        )
        if partial_only:
            return not_affordable_partial_only(
                currency=currency,
                requested=request.requested_amount,
                available=decision.amount_safe_to_pay,
            )
        return not_affordable_by_deadline(
            currency=currency, minimum=minimum, deadline=request.desired_completion_date
        )

    if chosen.method == "wait":
        return wait_until(
            currency=currency,
            requested=request.requested_amount,
            minimum=minimum,
            when=chosen.payments[0][0],
        )

    if chosen.method == "installments":
        option = chosen.option
        return installments(
            currency=currency,
            count=option.number_of_payments,
            payment=option.payment_amount,
            minimum=minimum,
            start=option.first_payment_date,
        )

    if chosen.method == "partial_payment":
        (_, today), (when, remainder) = chosen.payments
        return partial_payment(
            currency=currency,
            today=today,
            remainder=remainder,
            minimum=minimum,
            when=when,
        )

    if chosen.spending_changes:
        return with_spending_changes(
            currency=currency,
            requested=request.requested_amount,
            minimum=minimum,
            action_texts=chosen.spending_changes,
            changed_events=decision.changed_events,
        )

    return affordable_now(
        currency=currency, requested=request.requested_amount, minimum=minimum
    )
