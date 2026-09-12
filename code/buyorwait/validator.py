"""Output validation: every contract invariant, enforced (TASKS.md M11, PLAN.md 2).

The validator exists to reject, not to reassure, so its tests are written against
deliberately invalid rows rather than only against valid ones.

**One invariant is deliberately weaker than PLAN.md 2 states it.** That section reads
``affordable_now`` ⟺ ``earliest_date_for_full_payment == request_date``, but the samples do
not support the biconditional: request_12 is ``affordable_with_plan`` / ``installments`` with
``earliest_date_for_full_payment`` equal to ``request_date``, because full payment today is
safe while the user will not consider full payment. Only the forward direction is enforced —
``affordable_now`` implies the dates match — and the reverse is documented here so it is not
"fixed" later into a rule that rejects a correct row.
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from .contract import (
    AFFORDABILITY_STATUSES,
    MAX_SPENDING_CHANGES,
    OUTPUT_COLUMNS,
    PAYMENT_METHODS,
    STATUS_METHODS,
)

#: Money is compared to the cent; the emitted figures are rounded text, so the tolerance has
#: to absorb the rounding rather than the arithmetic.
TOLERANCE = 0.011

_PLAN_ENTRY = re.compile(r"^(\d{4}-\d{2}-\d{2}):(-?\d+(?:\.\d+)?)$")
_STOP = re.compile(r"^stop:(\S+)$")
_REDUCE = re.compile(r"^reduce_to:([^:]+):(-?\d+(?:\.\d+)?)$")


@dataclass(frozen=True)
class Violation:
    request_id: str
    message: str

    def __str__(self) -> str:
        return f"{self.request_id}: {self.message}"


def _parse_plan(text: str):
    """``[(date, amount)]``, or ``None`` when the field is malformed. ``none`` is ``[]``."""
    text = (text or "").strip()
    if text in ("", "none"):
        return []
    entries = []
    for part in text.split("|"):
        match = _PLAN_ENTRY.match(part.strip())
        if match is None:
            return None
        try:
            entries.append((dt.date.fromisoformat(match.group(1)), float(match.group(2))))
        except ValueError:
            return None
    return entries


def validate_row(row: dict, request, profile, options) -> list[Violation]:
    """Every invariant, for one emitted row against the inputs that produced it."""
    request_id = row.get("request_id", "?")
    problems: list[Violation] = []

    def fail(message: str) -> None:
        problems.append(Violation(request_id, message))

    status = (row.get("affordability_status") or "").strip()
    method = (row.get("recommended_payment_method") or "").strip()
    plan_text = (row.get("payment_plan") or "").strip()
    earliest_text = (row.get("earliest_date_for_full_payment") or "").strip()
    changes_text = (row.get("spending_changes_needed") or "").strip()

    if status not in AFFORDABILITY_STATUSES:
        fail(f"affordability_status {status!r} is not one of {AFFORDABILITY_STATUSES}")
    if method not in PAYMENT_METHODS:
        fail(f"recommended_payment_method {method!r} is not one of {PAYMENT_METHODS}")
    if status in STATUS_METHODS and method not in STATUS_METHODS[status]:
        fail(f"{status} may not recommend {method!r}")

    try:
        amount_safe = float(row.get("amount_safe_to_pay", ""))
    except (TypeError, ValueError):
        fail(f"amount_safe_to_pay {row.get('amount_safe_to_pay')!r} is not a number")
        amount_safe = None
    if amount_safe is not None:
        if amount_safe < -TOLERANCE:
            fail(f"amount_safe_to_pay {amount_safe} is negative")
        if amount_safe > request.requested_amount + TOLERANCE:
            fail(
                f"amount_safe_to_pay {amount_safe} exceeds requested_amount "
                f"{request.requested_amount}"
            )

    plan = _parse_plan(plan_text)
    if plan is None:
        fail(f"payment_plan {plan_text!r} is not chronological YYYY-MM-DD:amount joined by '|'")
        plan = []
    elif plan:
        dates = [when for when, _ in plan]
        if dates != sorted(dates):
            fail("payment_plan is not in chronological order")
        if any(amount <= 0 for _, amount in plan):
            fail("payment_plan carries a non-positive amount")

    earliest = None
    if earliest_text:
        try:
            earliest = dt.date.fromisoformat(earliest_text)
        except ValueError:
            fail(f"earliest_date_for_full_payment {earliest_text!r} is not an ISO date")
        else:
            if earliest < request.request_date:
                fail("earliest_date_for_full_payment is before the request date")

    # Only the forward direction; see the module docstring for why the converse is not a rule.
    if status == "affordable_now" and earliest != request.request_date:
        fail("affordable_now must set earliest_date_for_full_payment to the request date")

    changes = _validate_changes(changes_text, fail)

    if status == "affordable_now":
        if amount_safe is not None and abs(amount_safe - request.requested_amount) > TOLERANCE:
            fail("affordable_now requires amount_safe_to_pay to equal requested_amount")
        _expect_single_payment(plan, request.request_date, request.requested_amount, fail,
                              "affordable_now")
        if changes:
            fail("affordable_now must not require spending changes")

    if status == "affordable_later":
        if not plan:
            fail("affordable_later must carry one payment, not 'none'")
        else:
            _expect_single_payment(plan, earliest, request.requested_amount, fail, "affordable_later")
            if earliest is None:
                fail("affordable_later requires an earliest_date_for_full_payment")
            elif earliest <= request.request_date:
                fail("affordable_later requires a date later than the request date")

    if status == "not_affordable":
        if plan:
            fail("not_affordable must emit payment_plan 'none'")
        if earliest_text:
            fail("not_affordable must leave earliest_date_for_full_payment empty")
        if changes:
            fail("not_affordable must not require spending changes")

    if method == "partial_payment":
        _validate_partial(plan, row, request, profile, amount_safe, fail)
    if method == "installments":
        _validate_installments(plan, options, profile, fail)
    if method == "full_payment" and status == "affordable_with_plan":
        _expect_single_payment(plan, request.request_date, request.requested_amount, fail,
                               "affordable_with_plan + full_payment")
        if not changes:
            fail("affordable_with_plan + full_payment is the spending-changes branch and needs changes")
        if amount_safe is not None and amount_safe >= request.requested_amount - TOLERANCE:
            fail(
                "affordable_with_plan + full_payment requires amount_safe_to_pay below "
                "requested_amount, since it is measured before the changes"
            )

    if method in ("full_payment", "partial_payment", "installments") and not profile.accepts(method):
        fail(f"recommended {method!r}, which is not in payment_methods_user_will_consider")

    return problems


def _expect_single_payment(plan, when, amount, fail, label) -> None:
    if len(plan) != 1:
        fail(f"{label} requires exactly one payment, found {len(plan)}")
        return
    paid_on, paid = plan[0]
    if when is not None and paid_on != when:
        fail(f"{label} payment is dated {paid_on}, expected {when}")
    if abs(paid - amount) > TOLERANCE:
        fail(f"{label} payment is {paid}, expected {amount}")


def _validate_changes(text: str, fail) -> list[str]:
    if text in ("", "none"):
        return []
    actions = [part.strip() for part in text.split("|") if part.strip()]
    if len(actions) > MAX_SPENDING_CHANGES:
        fail(f"{len(actions)} spending changes exceeds the limit of {MAX_SPENDING_CHANGES}")
    targets = []
    for action in actions:
        stop = _STOP.match(action)
        reduce_to = _REDUCE.match(action)
        if stop:
            targets.append(stop.group(1))
        elif reduce_to:
            targets.append(reduce_to.group(1))
        else:
            fail(f"spending change {action!r} is not stop:<event_id> or reduce_to:<event_id>:<amount>")
    if len(targets) != len(set(targets)):
        fail("stop and reduce_to must not target the same event")
    return actions


def _validate_partial(plan, row, request, profile, amount_safe, fail) -> None:
    if not request.allows_partial_payment:
        fail("partial_payment on a request that does not allow it")
    if (row.get("affordability_status") or "").strip() != "affordable_with_plan":
        fail("partial_payment requires affordability_status affordable_with_plan")
    if len(plan) != 2:
        fail(f"partial_payment requires exactly two payments, found {len(plan)}")
        return
    (first_on, first), (second_on, second) = plan
    if first_on != request.request_date:
        fail(f"partial_payment first payment is dated {first_on}, expected {request.request_date}")
    if abs(first + second - request.requested_amount) > TOLERANCE:
        fail(f"partial_payment payments sum to {first + second}, expected {request.requested_amount}")
    if amount_safe is not None and abs(first - amount_safe) > TOLERANCE:
        fail(f"partial_payment first payment {first} does not equal amount_safe_to_pay {amount_safe}")
    if amount_safe is not None and not 0 < amount_safe < request.requested_amount:
        fail("partial_payment requires 0 < amount_safe_to_pay < requested_amount")
    if second_on > request.desired_completion_date:
        fail(
            f"partial_payment second payment {second_on} falls after the desired completion "
            f"date {request.desired_completion_date}"
        )


def _validate_installments(plan, options, profile, fail) -> None:
    if profile.max_installment_months is None:
        fail("installments recommended while max_installment_months is blank")
        return
    matches = [
        option
        for option in options
        if option.payment_method == "installments"
        and len(plan) == option.number_of_payments
        and all(
            entry[0] == scheduled[0] and abs(entry[1] - scheduled[1]) <= TOLERANCE
            for entry, scheduled in zip(plan, option.schedule())
        )
    ]
    if not matches:
        fail("installments plan does not match any supplied payment option exactly")
        return
    option = matches[0]
    if option.number_of_payments > profile.max_installment_months:
        fail(
            f"installments option {option.payment_option_id} has {option.number_of_payments} "
            f"payments, above the cap of {profile.max_installment_months}"
        )


def validate_rows(rows, data) -> list[Violation]:
    """Validate emitted rows (``request_id`` -> field dict) against the dataset."""
    problems: list[Violation] = []
    for request_id, row in rows.items():
        try:
            request = data.request(request_id)
        except KeyError:
            problems.append(Violation(request_id, "no such request in the dataset"))
            continue
        problems.extend(
            validate_row(
                row,
                request,
                data.profiles[request.user_id],
                data.options(request_id),
            )
        )
    return problems


def validate_output_file(path: str | Path, data) -> list[Violation]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != OUTPUT_COLUMNS:
            return [Violation("<file>", f"header is not the output contract: {reader.fieldnames}")]
        rows = {row["request_id"]: row for row in reader}
    return validate_rows(rows, data)
