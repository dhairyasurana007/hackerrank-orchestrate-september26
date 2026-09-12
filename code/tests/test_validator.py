"""The validator (TASKS.md M11).

Written against deliberately invalid rows. Proving that it *accepts* valid input is the easy
half and would pass with an empty function; the point of every test here is that a specific
corruption is rejected with a message naming it.
"""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from buyorwait import loaders, paths, validator
from buyorwait.records import PaymentOption, Profile, Request

DATASET = paths.find_dataset()
START = dt.date(2026, 3, 2)


def _profile(methods=("full_payment", "partial_payment", "installments"), cap=12) -> Profile:
    return Profile(
        user_id="user_x",
        home_currency="EUR",
        current_available_balance=10000.0,
        minimum_balance_to_keep=1000.0,
        financial_priorities=("savings",),
        expense_categories_to_protect=("rent",),
        categories_willing_to_reduce=("dining",),
        categories_willing_to_stop=("gym",),
        payment_methods_user_will_consider=tuple(methods),
        max_installment_months=cap,
    )


def _request(requested=1000.0, allows_partial=True, completion_days=60) -> Request:
    return Request(
        request_id="request_x",
        user_id="user_x",
        request_date=START,
        request_type="purchase",
        requested_amount=requested,
        desired_completion_date=START + dt.timedelta(days=completion_days),
        allows_partial_payment=allows_partial,
        request_text="",
    )


def _options():
    return (
        PaymentOption(
            payment_option_id="payment_option_01",
            request_id="request_x",
            payment_method="full_payment",
            payment_amount=1000.0,
            number_of_payments=1,
            first_payment_date=START,
            payment_frequency_days=None,
            financing_fee=0.0,
            total_payable_amount=1000.0,
        ),
        PaymentOption(
            payment_option_id="payment_option_02",
            request_id="request_x",
            payment_method="installments",
            payment_amount=350.0,
            number_of_payments=3,
            first_payment_date=START + dt.timedelta(days=3),
            payment_frequency_days=30,
            financing_fee=50.0,
            total_payable_amount=1050.0,
        ),
    )


def _row(**overrides) -> dict:
    base = {
        "request_id": "request_x",
        "amount_safe_to_pay": "1000",
        "affordability_status": "affordable_now",
        "recommended_payment_method": "full_payment",
        "payment_plan": f"{START.isoformat()}:1000",
        "earliest_date_for_full_payment": START.isoformat(),
        "spending_changes_needed": "none",
        "decision_explanation": "Pay EUR 1,000 today.",
    }
    base.update(overrides)
    return base


def _check(row, request=None, profile=None, options=None):
    return validator.validate_row(
        row, request or _request(), profile or _profile(), options or _options()
    )


class TestAValidRowPasses(unittest.TestCase):
    """The baseline, so every rejection below is attributable to its own corruption."""

    def test_affordable_now(self):
        self.assertEqual(_check(_row()), [])

    def test_affordable_later(self):
        later = (START + dt.timedelta(days=20)).isoformat()
        self.assertEqual(
            _check(
                _row(
                    amount_safe_to_pay="400",
                    affordability_status="affordable_later",
                    recommended_payment_method="wait",
                    payment_plan=f"{later}:1000",
                    earliest_date_for_full_payment=later,
                )
            ),
            [],
        )

    def test_not_affordable(self):
        self.assertEqual(
            _check(
                _row(
                    amount_safe_to_pay="0",
                    affordability_status="not_affordable",
                    recommended_payment_method="not_recommended",
                    payment_plan="none",
                    earliest_date_for_full_payment="",
                )
            ),
            [],
        )

    def test_installments(self):
        plan = "|".join(
            f"{when.isoformat()}:350" for when, _ in _options()[1].schedule()
        )
        self.assertEqual(
            _check(
                _row(
                    amount_safe_to_pay="500",
                    affordability_status="affordable_with_plan",
                    recommended_payment_method="installments",
                    payment_plan=plan,
                    earliest_date_for_full_payment=(START + dt.timedelta(days=10)).isoformat(),
                )
            ),
            [],
        )

    def test_partial_payment(self):
        second = (START + dt.timedelta(days=30)).isoformat()
        self.assertEqual(
            _check(
                _row(
                    amount_safe_to_pay="400",
                    affordability_status="affordable_with_plan",
                    recommended_payment_method="partial_payment",
                    payment_plan=f"{START.isoformat()}:400|{second}:600",
                    earliest_date_for_full_payment=second,
                )
            ),
            [],
        )

    def test_the_spending_changes_branch(self):
        self.assertEqual(
            _check(
                _row(
                    amount_safe_to_pay="900",
                    affordability_status="affordable_with_plan",
                    recommended_payment_method="full_payment",
                    payment_plan=f"{START.isoformat()}:1000",
                    earliest_date_for_full_payment=(START + dt.timedelta(days=13)).isoformat(),
                    spending_changes_needed="stop:event_1|reduce_to:event_2:23.50",
                )
            ),
            [],
        )


class TestVocabularyAndCoupling(unittest.TestCase):
    def test_an_unknown_status_is_rejected(self):
        self.assertTrue(_check(_row(affordability_status="probably_fine")))

    def test_an_unknown_method_is_rejected(self):
        self.assertTrue(_check(_row(recommended_payment_method="bank_transfer")))

    def test_every_illegal_status_method_pair_is_rejected(self):
        illegal = [
            ("affordable_now", "wait"),
            ("affordable_now", "installments"),
            ("affordable_now", "partial_payment"),
            ("affordable_later", "full_payment"),
            ("affordable_later", "installments"),
            ("not_affordable", "full_payment"),
            ("not_affordable", "partial_payment"),
            ("affordable_with_plan", "wait"),
            ("affordable_with_plan", "not_recommended"),
        ]
        for status, method in illegal:
            with self.subTest(status=status, method=method):
                problems = _check(_row(affordability_status=status, recommended_payment_method=method))
                self.assertTrue(any("may not recommend" in p.message for p in problems))

    def test_a_method_the_user_does_not_consider_is_rejected(self):
        problems = _check(_row(), profile=_profile(methods=("installments",), cap=6))
        self.assertTrue(any("payment_methods_user_will_consider" in p.message for p in problems))


class TestAmountRange(unittest.TestCase):
    def test_a_negative_safe_amount_is_rejected(self):
        problems = _check(_row(amount_safe_to_pay="-1"))
        self.assertTrue(any("negative" in p.message for p in problems))

    def test_a_safe_amount_above_the_request_is_rejected(self):
        problems = _check(_row(amount_safe_to_pay="1500"))
        self.assertTrue(any("exceeds requested_amount" in p.message for p in problems))

    def test_a_non_numeric_safe_amount_is_rejected(self):
        self.assertTrue(any("is not a number" in p.message for p in _check(_row(amount_safe_to_pay="lots"))))


class TestPlanShape(unittest.TestCase):
    def test_a_malformed_plan_entry_is_rejected(self):
        problems = _check(_row(payment_plan="2026-03-02=1000"))
        self.assertTrue(any("chronological" in p.message for p in problems))

    def test_an_out_of_order_plan_is_rejected(self):
        plan = f"{(START + dt.timedelta(days=30)).isoformat()}:350|{(START + dt.timedelta(days=3)).isoformat()}:350|{(START + dt.timedelta(days=60)).isoformat()}:350"
        problems = _check(
            _row(
                affordability_status="affordable_with_plan",
                recommended_payment_method="installments",
                amount_safe_to_pay="500",
                payment_plan=plan,
            )
        )
        self.assertTrue(any("chronological order" in p.message for p in problems))

    def test_a_non_positive_payment_is_rejected(self):
        problems = _check(_row(payment_plan=f"{START.isoformat()}:0"))
        self.assertTrue(any("non-positive" in p.message for p in problems))


class TestPerStatusRowShapes(unittest.TestCase):
    def test_affordable_now_must_pay_the_full_amount_today(self):
        self.assertTrue(_check(_row(payment_plan=f"{START.isoformat()}:500")))

    def test_affordable_now_must_set_the_earliest_date_to_the_request_date(self):
        problems = _check(
            _row(earliest_date_for_full_payment=(START + dt.timedelta(days=5)).isoformat())
        )
        self.assertTrue(any("affordable_now must set" in p.message for p in problems))

    def test_affordable_now_must_not_carry_spending_changes(self):
        problems = _check(_row(spending_changes_needed="stop:event_1"))
        self.assertTrue(any("must not require spending changes" in p.message for p in problems))

    def test_affordable_now_requires_the_safe_amount_to_equal_the_request(self):
        problems = _check(_row(amount_safe_to_pay="900"))
        self.assertTrue(any("equal requested_amount" in p.message for p in problems))

    def test_affordable_later_must_not_emit_none(self):
        later = (START + dt.timedelta(days=20)).isoformat()
        problems = _check(
            _row(
                affordability_status="affordable_later",
                recommended_payment_method="wait",
                amount_safe_to_pay="400",
                payment_plan="none",
                earliest_date_for_full_payment=later,
            )
        )
        self.assertTrue(any("not 'none'" in p.message for p in problems))

    def test_affordable_later_must_be_dated_after_the_request_date(self):
        problems = _check(
            _row(
                affordability_status="affordable_later",
                recommended_payment_method="wait",
                amount_safe_to_pay="400",
                payment_plan=f"{START.isoformat()}:1000",
                earliest_date_for_full_payment=START.isoformat(),
            )
        )
        self.assertTrue(any("later than the request date" in p.message for p in problems))

    def test_not_affordable_must_emit_no_plan(self):
        problems = _check(
            _row(
                affordability_status="not_affordable",
                recommended_payment_method="not_recommended",
                amount_safe_to_pay="0",
                payment_plan=f"{START.isoformat()}:1000",
                earliest_date_for_full_payment="",
            )
        )
        self.assertTrue(any("must emit payment_plan 'none'" in p.message for p in problems))

    def test_not_affordable_must_leave_the_earliest_date_empty(self):
        problems = _check(
            _row(
                affordability_status="not_affordable",
                recommended_payment_method="not_recommended",
                amount_safe_to_pay="0",
                payment_plan="none",
                earliest_date_for_full_payment=START.isoformat(),
            )
        )
        self.assertTrue(any("leave earliest_date_for_full_payment empty" in p.message for p in problems))

    def test_the_spending_changes_branch_needs_changes(self):
        problems = _check(
            _row(
                affordability_status="affordable_with_plan",
                recommended_payment_method="full_payment",
                amount_safe_to_pay="900",
                earliest_date_for_full_payment=(START + dt.timedelta(days=13)).isoformat(),
                spending_changes_needed="none",
            )
        )
        self.assertTrue(any("needs changes" in p.message for p in problems))

    def test_the_spending_changes_branch_needs_a_safe_amount_below_the_request(self):
        problems = _check(
            _row(
                affordability_status="affordable_with_plan",
                recommended_payment_method="full_payment",
                amount_safe_to_pay="1000",
                earliest_date_for_full_payment=(START + dt.timedelta(days=13)).isoformat(),
                spending_changes_needed="stop:event_1",
            )
        )
        self.assertTrue(any("below requested_amount" in p.message for p in problems))


class TestPartialPaymentInvariants(unittest.TestCase):
    def _partial(self, **overrides):
        second = (START + dt.timedelta(days=30)).isoformat()
        row = _row(
            amount_safe_to_pay="400",
            affordability_status="affordable_with_plan",
            recommended_payment_method="partial_payment",
            payment_plan=f"{START.isoformat()}:400|{second}:600",
            earliest_date_for_full_payment=second,
        )
        row.update(overrides)
        return row

    def test_three_payments_are_rejected(self):
        plan = f"{START.isoformat()}:400|{(START + dt.timedelta(days=20)).isoformat()}:300|{(START + dt.timedelta(days=40)).isoformat()}:300"
        problems = _check(self._partial(payment_plan=plan))
        self.assertTrue(any("exactly two payments" in p.message for p in problems))

    def test_payments_that_do_not_sum_to_the_request_are_rejected(self):
        plan = f"{START.isoformat()}:400|{(START + dt.timedelta(days=30)).isoformat()}:500"
        problems = _check(self._partial(payment_plan=plan))
        self.assertTrue(any("sum to" in p.message for p in problems))

    def test_a_first_payment_not_equal_to_the_safe_amount_is_rejected(self):
        plan = f"{START.isoformat()}:300|{(START + dt.timedelta(days=30)).isoformat()}:700"
        problems = _check(self._partial(payment_plan=plan))
        self.assertTrue(any("does not equal amount_safe_to_pay" in p.message for p in problems))

    def test_a_second_payment_after_the_deadline_is_rejected(self):
        second = (START + dt.timedelta(days=70)).isoformat()
        problems = _check(
            self._partial(payment_plan=f"{START.isoformat()}:400|{second}:600")
        )
        self.assertTrue(any("after the desired completion" in p.message for p in problems))

    def test_partial_payment_on_a_request_that_forbids_it_is_rejected(self):
        problems = _check(self._partial(), request=_request(allows_partial=False))
        self.assertTrue(any("does not allow it" in p.message for p in problems))

    def test_a_zero_safe_amount_cannot_support_partial_payment(self):
        plan = f"{START.isoformat()}:0|{(START + dt.timedelta(days=30)).isoformat()}:1000"
        problems = _check(self._partial(amount_safe_to_pay="0", payment_plan=plan))
        self.assertTrue(problems)


class TestInstallmentInvariants(unittest.TestCase):
    def _installments(self, plan=None, **overrides):
        plan = plan or "|".join(f"{when.isoformat()}:350" for when, _ in _options()[1].schedule())
        row = _row(
            amount_safe_to_pay="500",
            affordability_status="affordable_with_plan",
            recommended_payment_method="installments",
            payment_plan=plan,
            earliest_date_for_full_payment=(START + dt.timedelta(days=10)).isoformat(),
        )
        row.update(overrides)
        return row

    def test_a_plan_matching_no_supplied_option_is_rejected(self):
        plan = f"{START.isoformat()}:500|{(START + dt.timedelta(days=30)).isoformat()}:500"
        problems = _check(self._installments(plan=plan))
        self.assertTrue(any("does not match any supplied payment option" in p.message for p in problems))

    def test_an_option_with_the_right_dates_but_wrong_amounts_is_rejected(self):
        plan = "|".join(f"{when.isoformat()}:400" for when, _ in _options()[1].schedule())
        problems = _check(self._installments(plan=plan))
        self.assertTrue(any("does not match any supplied payment option" in p.message for p in problems))

    def test_installments_with_a_blank_month_cap_are_rejected(self):
        problems = _check(self._installments(), profile=_profile(cap=None))
        self.assertTrue(any("max_installment_months is blank" in p.message for p in problems))

    def test_an_option_past_the_month_cap_is_rejected(self):
        problems = _check(self._installments(), profile=_profile(cap=2))
        self.assertTrue(any("above the cap" in p.message for p in problems))


class TestSpendingChangeSyntax(unittest.TestCase):
    def _with_changes(self, changes):
        return _row(
            affordability_status="affordable_with_plan",
            recommended_payment_method="full_payment",
            amount_safe_to_pay="900",
            earliest_date_for_full_payment=(START + dt.timedelta(days=13)).isoformat(),
            spending_changes_needed=changes,
        )

    def test_a_malformed_action_is_rejected(self):
        problems = _check(self._with_changes("cancel:event_1"))
        self.assertTrue(any("is not stop:" in p.message for p in problems))

    def test_a_reduce_without_an_amount_is_rejected(self):
        problems = _check(self._with_changes("reduce_to:event_1"))
        self.assertTrue(any("is not stop:" in p.message for p in problems))

    def test_more_than_three_actions_are_rejected(self):
        changes = "|".join(f"stop:event_{i}" for i in range(4))
        problems = _check(self._with_changes(changes))
        self.assertTrue(any("exceeds the limit" in p.message for p in problems))

    def test_stop_and_reduce_on_the_same_event_are_rejected(self):
        problems = _check(self._with_changes("stop:event_1|reduce_to:event_1:10"))
        self.assertTrue(any("same event" in p.message for p in problems))


class TestAgainstTheRealDatasetAndGroundTruth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = loaders.load_dataset(DATASET)

    def test_the_25_solved_sample_rows_are_accepted(self):
        """The strongest evidence the invariants are the contract and not our own habits."""
        rows = {
            request_id: {
                "request_id": request_id,
                "amount_safe_to_pay": str(truth.amount_safe_to_pay),
                "affordability_status": truth.affordability_status,
                "recommended_payment_method": truth.recommended_payment_method,
                "payment_plan": truth.payment_plan,
                "earliest_date_for_full_payment": truth.earliest_date_for_full_payment,
                "spending_changes_needed": truth.spending_changes_needed,
                "decision_explanation": truth.decision_explanation,
            }
            for request_id, truth in self.data.sample_truth.items()
        }
        self.assertEqual([str(v) for v in validator.validate_rows(rows, self.data)], [])

    def test_request_12_is_accepted_despite_an_earliest_date_equal_to_the_request_date(self):
        """The invariant is one-directional; enforcing the biconditional rejects this row."""
        truth = self.data.sample_truth["request_12"]
        self.assertEqual(truth.affordability_status, "affordable_with_plan")
        request = self.data.request("request_12")
        self.assertEqual(truth.earliest_date_for_full_payment, request.request_date.isoformat())

    def test_the_engines_own_250_rows_validate_clean(self):
        from buyorwait.pipeline import Engine, run

        engine = Engine.build(self.data, use_llm=False)
        report = run(engine, self.data.requests)
        rows = {row.request_id: row.as_csv_dict() for row in report.rows}
        self.assertEqual([str(v) for v in validator.validate_rows(rows, self.data)], [])

    def test_an_unknown_request_id_in_a_file_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.csv"
            path.write_text(
                ",".join(
                    [
                        "request_id",
                        "amount_safe_to_pay",
                        "affordability_status",
                        "recommended_payment_method",
                        "payment_plan",
                        "earliest_date_for_full_payment",
                        "spending_changes_needed",
                        "decision_explanation",
                    ]
                )
                + "\nrequest_9999,0,not_affordable,not_recommended,none,,none,x\n",
                encoding="utf-8",
            )
            problems = validator.validate_output_file(path, self.data)
        self.assertTrue(any("no such request" in p.message for p in problems))

    def test_a_file_with_the_wrong_header_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.csv"
            path.write_text("request_id,amount\nrequest_26,1\n", encoding="utf-8")
            problems = validator.validate_output_file(path, self.data)
        self.assertTrue(any("header is not the output contract" in p.message for p in problems))


if __name__ == "__main__":
    unittest.main()
