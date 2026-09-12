"""Plan enumeration, eligibility gating and the six-level ranking (TASKS.md M9)."""

from __future__ import annotations

import dataclasses
import datetime as dt
import unittest

from buyorwait import forecast, loaders, paths, planner
from buyorwait.fx import RateTable
from buyorwait.records import PaymentOption, Profile, Request
from buyorwait.state import build_state

DATASET = paths.find_dataset()
START = dt.date(2026, 1, 5)


def _profile(methods=("full_payment",), cap=None, minimum=1000.0, balance=50000.0) -> Profile:
    return Profile(
        user_id="user_x",
        home_currency="EUR",
        current_available_balance=balance,
        minimum_balance_to_keep=minimum,
        financial_priorities=("savings",),
        expense_categories_to_protect=("rent",),
        categories_willing_to_reduce=("dining",),
        categories_willing_to_stop=("gym",),
        payment_methods_user_will_consider=tuple(methods),
        max_installment_months=cap,
    )


def _request(requested=10000.0, completion_days=60, allows_partial=True) -> Request:
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


def _option(option_id, method, amount, count, first_offset=0, freq=30, fee=0.0) -> PaymentOption:
    return PaymentOption(
        payment_option_id=option_id,
        request_id="request_x",
        payment_method=method,
        payment_amount=amount,
        number_of_payments=count,
        first_payment_date=START + dt.timedelta(days=first_offset),
        payment_frequency_days=freq if count > 1 else None,
        financing_fee=fee,
        total_payable_amount=amount * count,
    )


def _curve(values=None, balance=50000.0):
    values = values if values is not None else [balance] * 90
    return forecast.Curve(start=START, opening=values[0], values=list(values))


def _dip_curve(high=50000.0, low=8000.0):
    """High, then a 20-day dip inside the deadline, then a recovery.

    The recovery matters: a curve that dips and stays down leaves no second payment date, so
    partial payment would be refused for the wrong reason and the fixture would prove nothing.
    """
    return _curve([high] * 30 + [low] * 20 + [high] * 40)


def _decide(profile, request, options=(), curve=None, search=None):
    return planner.decide(
        request=request,
        profile=profile,
        curve=curve or _curve(balance=profile.current_available_balance),
        options=options,
        spending_change_search=search,
    )


class TestMethodGating(unittest.TestCase):
    def test_full_payment_is_offered_only_when_the_user_considers_it(self):
        request = _request()
        offered = _decide(_profile(("full_payment",)), request)
        self.assertEqual(offered.chosen.method, "full_payment")
        refused = _decide(_profile(("partial_payment",), cap=None), request)
        self.assertNotEqual(refused.chosen.method, "full_payment")
        self.assertIn(
            ("full_payment", "not in payment_methods_user_will_consider"), refused.rejected
        )

    def test_partial_payment_is_offered_only_when_the_user_considers_it(self):
        # Trough leaves 7,000 of headroom against a 10,000 request.
        curve = _dip_curve()
        request = _request()
        offered = _decide(_profile(("partial_payment",)), request, curve=curve)
        self.assertEqual(offered.chosen.method, "partial_payment")
        refused = _decide(_profile(("installments",), cap=6), request, curve=curve)
        self.assertNotEqual(refused.chosen.method, "partial_payment")

    def test_partial_payment_is_refused_when_the_request_forbids_it(self):
        curve = _dip_curve()
        decision = _decide(
            _profile(("partial_payment",)), _request(allows_partial=False), curve=curve
        )
        self.assertNotEqual(decision.chosen.method, "partial_payment")
        self.assertIn(
            ("partial_payment", "the request does not allow partial payment"), decision.rejected
        )

    def test_installments_are_offered_only_when_the_user_considers_them(self):
        options = (_option("payment_option_02", "installments", 2000.0, 5, fee=100.0),)
        curve = _dip_curve()
        offered = _decide(_profile(("installments",), cap=6), _request(), options, curve=curve)
        self.assertEqual(offered.chosen.method, "installments")
        refused = _decide(_profile(("partial_payment",)), _request(), options, curve=curve)
        self.assertNotEqual(refused.chosen.method, "installments")


class TestInstallmentCap(unittest.TestCase):
    def test_an_option_past_the_cap_is_rejected(self):
        options = (
            _option("payment_option_02", "installments", 2000.0, 5),
            _option("payment_option_03", "installments", 800.0, 15),
        )
        eligible = planner.eligible_installment_options(
            _request(), _profile(("installments",), cap=6), options
        )
        self.assertEqual([o.payment_option_id for o in eligible], ["payment_option_02"])

    def test_a_blank_cap_rejects_every_installment_option_wholesale(self):
        options = (
            _option("payment_option_02", "installments", 2000.0, 2),
            _option("payment_option_03", "installments", 800.0, 3),
        )
        profile = _profile(("full_payment", "installments"), cap=None)
        self.assertEqual(planner.eligible_installment_options(_request(), profile, options), ())
        decision = _decide(profile, _request(), options)
        self.assertIn(("installments", "max_installment_months is blank"), decision.rejected)

    def test_an_option_exactly_at_the_cap_is_eligible(self):
        options = (_option("payment_option_02", "installments", 2000.0, 6),)
        eligible = planner.eligible_installment_options(
            _request(), _profile(("installments",), cap=6), options
        )
        self.assertEqual(len(eligible), 1)


class TestSafetyGating(unittest.TestCase):
    def test_an_unsafe_schedule_is_dropped(self):
        # Floor 45,000 against a 50,000 balance: any real payment breaches it.
        profile = _profile(("installments",), cap=12, minimum=45000.0)
        options = (_option("payment_option_02", "installments", 2000.0, 5),)
        decision = _decide(profile, _request(), options)
        self.assertEqual(decision.chosen.method, "not_recommended")
        self.assertIn(
            ("payment_option_02", "schedule breaches the minimum balance"), decision.rejected
        )

    def test_nothing_eligible_and_safe_yields_not_recommended_with_no_plan_or_date(self):
        profile = _profile(("installments",), cap=None, minimum=49000.0)
        decision = _decide(profile, _request())
        self.assertEqual(
            (decision.chosen.status, decision.chosen.method), ("not_affordable", "not_recommended")
        )
        self.assertEqual(decision.chosen.payments, ())
        self.assertIsNone(decision.emitted_earliest)

    def test_not_recommended_emits_no_date_even_when_capacity_found_one(self):
        """The one place the contract overrides the computed figure (PLAN.md 2)."""
        profile = _profile(("partial_payment",), minimum=1000.0)
        decision = _decide(profile, _request(allows_partial=False))
        self.assertEqual(decision.chosen.method, "not_recommended")
        self.assertEqual(decision.earliest_full_payment, START)  # capacity says today
        self.assertIsNone(decision.emitted_earliest)  # the row says nothing


class TestWaitBranch(unittest.TestCase):
    def test_wait_emits_one_payment_of_the_full_amount_never_none(self):
        curve = _curve([12000.0] * 40 + [30000.0] * 50)
        profile = _profile(("full_payment",), minimum=5000.0, balance=12000.0)
        decision = _decide(profile, _request(requested=20000.0, completion_days=80), curve=curve)
        self.assertEqual((decision.chosen.status, decision.chosen.method), ("affordable_later", "wait"))
        self.assertEqual(decision.chosen.payments, ((START + dt.timedelta(days=40), 20000.0),))

    def test_wait_is_not_offered_when_the_user_will_not_pay_in_full(self):
        curve = _curve([12000.0] * 40 + [30000.0] * 50)
        profile = _profile(("installments",), cap=None, minimum=5000.0, balance=12000.0)
        decision = _decide(profile, _request(requested=20000.0, completion_days=80), curve=curve)
        self.assertEqual(decision.chosen.method, "not_recommended")


class TestRanking(unittest.TestCase):
    """Each of the six criteria must be the deciding factor in at least one case."""

    def _rank(self, candidates, request=None):
        request = request or _request()
        return min(candidates, key=lambda c: c.rank_key(request))

    def _candidate(self, **kw):
        base = dict(
            method="installments",
            status="affordable_with_plan",
            payments=((START, 100.0),),
            total_payable=100.0,
        )
        base.update(kw)
        return planner.Candidate(**base)

    def test_criterion_1_completing_by_the_deadline_wins(self):
        late = self._candidate(total_payable=50.0, completes_by_deadline=False)
        on_time = self._candidate(total_payable=500.0, completes_by_deadline=True)
        self.assertIs(self._rank([late, on_time]), on_time)

    def test_criterion_2_needing_no_spending_changes_wins(self):
        with_changes = self._candidate(total_payable=50.0, spending_changes=("stop:event_1",))
        without = self._candidate(total_payable=500.0)
        self.assertIs(self._rank([with_changes, without]), without)

    def test_criterion_3_the_lowest_total_payable_wins(self):
        cheap = self._candidate(total_payable=100.0, payments=((START + dt.timedelta(days=5), 100.0),))
        dear = self._candidate(total_payable=150.0, payments=((START, 150.0),))
        self.assertIs(self._rank([dear, cheap]), cheap)

    def test_criterion_4_the_earliest_start_wins(self):
        early = self._candidate(payments=((START, 100.0),))
        late = self._candidate(payments=((START + dt.timedelta(days=10), 100.0),))
        self.assertIs(self._rank([late, early]), early)

    def test_criterion_5_the_fewest_payments_wins(self):
        many = self._candidate(payments=((START, 50.0), (START + dt.timedelta(days=30), 50.0)))
        few = self._candidate(payments=((START, 100.0),))
        self.assertIs(self._rank([many, few]), few)

    def test_criterion_6_the_lowest_payment_option_id_breaks_a_full_tie(self):
        first = self._candidate(option=_option("payment_option_02", "installments", 100.0, 1))
        second = self._candidate(option=_option("payment_option_09", "installments", 100.0, 1))
        self.assertIs(self._rank([second, first]), first)

    def test_full_payment_beats_installments_on_cost_when_both_are_safe(self):
        """Every installment option in the dataset carries a nonzero financing fee."""
        options = (
            _option("payment_option_01", "full_payment", 10000.0, 1),
            _option("payment_option_02", "installments", 2100.0, 5, fee=500.0),
        )
        profile = _profile(("full_payment", "installments"), cap=12)
        decision = _decide(profile, _request(), options)
        self.assertEqual(decision.chosen.method, "full_payment")

    def test_partial_payment_beats_installments_on_cost(self):
        curve = _dip_curve()
        options = (_option("payment_option_02", "installments", 2100.0, 5, fee=500.0),)
        profile = _profile(("partial_payment", "installments"), cap=12)
        decision = _decide(profile, _request(), options, curve=curve)
        self.assertEqual(decision.chosen.method, "partial_payment")
        self.assertAlmostEqual(sum(a for _, a in decision.chosen.payments), 10000.0)


class TestPartialPaymentShape(unittest.TestCase):
    def test_it_is_exactly_two_payments_summing_to_the_requested_amount(self):
        curve = _dip_curve()
        decision = _decide(_profile(("partial_payment",)), _request(), curve=curve)
        payments = decision.chosen.payments
        self.assertEqual(len(payments), 2)
        self.assertEqual(payments[0][0], START)
        self.assertAlmostEqual(payments[0][1], decision.amount_safe_to_pay)
        self.assertAlmostEqual(sum(a for _, a in payments), 10000.0)

    def test_it_is_refused_when_nothing_is_safe_today(self):
        curve = _curve([1000.0] * 90)
        decision = _decide(_profile(("partial_payment",), minimum=1000.0, balance=1000.0), _request(), curve=curve)
        self.assertNotEqual(decision.chosen.method, "partial_payment")

    def test_it_is_refused_when_the_second_payment_would_fall_past_the_deadline(self):
        # 5,000 is safe today, but the headroom for the remainder only appears after day 70
        # while the deadline is day 20.
        curve = _curve([6000.0] * 70 + [80000.0] * 20)
        request = _request(requested=10000.0, completion_days=20)
        profile = _profile(("partial_payment",), minimum=1000.0, balance=6000.0)
        decision = _decide(profile, request, curve=curve)
        self.assertNotEqual(decision.chosen.method, "partial_payment")
        self.assertIn(
            ("partial_payment", "no second payment date on or before the deadline"), decision.rejected
        )


class TestTheIndependenceCase(unittest.TestCase):
    """earliest_date_for_full_payment can equal request_date while the plan is installments."""

    def test_capacity_is_computed_without_reference_to_method_preferences(self):
        options = (
            _option("payment_option_01", "full_payment", 10000.0, 1),
            _option("payment_option_02", "installments", 2100.0, 5, fee=500.0),
        )
        # Full payment today is affordable, but the user will not consider it.
        profile = _profile(("installments",), cap=12)
        decision = _decide(profile, _request(), options)
        self.assertEqual(decision.chosen.method, "installments")
        self.assertEqual(decision.chosen.status, "affordable_with_plan")
        self.assertEqual(decision.emitted_earliest, START)
        self.assertAlmostEqual(decision.amount_safe_to_pay, 10000.0)

    def test_request_12_reproduces_that_shape_on_the_real_dataset(self):
        data = loaders.load_dataset(DATASET)
        rates = RateTable(data.rates)
        request = data.request("request_12")
        profile = data.profiles[request.user_id]
        state = build_state(request, profile, data.events(request.user_id), rates)
        curve = forecast.build(
            request=request,
            profile=profile,
            events=state.events,
            rates=rates,
            events_by_id=state.events_by_id,
            amounts=state.amounts,
        )
        decision = planner.decide(
            request=request, profile=profile, curve=curve, options=data.options("request_12")
        )
        self.assertEqual(decision.chosen.method, "installments")
        self.assertEqual(decision.emitted_earliest, request.request_date)


class TestAgainstTheRealDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from buyorwait.pipeline import Engine

        cls.data = loaders.load_dataset(DATASET)
        cls.engine = Engine.build(cls.data, use_llm=False)

    def test_every_request_reaches_a_decision(self):
        for request in self.data.requests + self.data.samples:
            with self.subTest(request=request.request_id):
                from buyorwait.contract import PAYMENT_METHODS

                decision = self.engine.decision_for(request)
                self.assertIn(decision.chosen.method, PAYMENT_METHODS)

    def test_the_status_method_coupling_holds_on_all_250_rows(self):
        from buyorwait.contract import STATUS_METHODS

        for request in self.data.requests:
            with self.subTest(request=request.request_id):
                chosen = self.engine.decision_for(request).chosen
                self.assertIn(chosen.method, STATUS_METHODS[chosen.status])

    def test_a_recommended_method_is_always_one_the_user_considers(self):
        for request in self.data.requests + self.data.samples:
            decision = self.engine.decision_for(request)
            method = decision.chosen.method
            if method in ("wait", "not_recommended"):
                continue
            with self.subTest(request=request.request_id):
                self.assertTrue(
                    self.data.profiles[request.user_id].accepts(method),
                    f"{request.request_id}: recommended {method}",
                )

    def test_no_installment_plan_ever_exceeds_the_users_month_cap(self):
        for request in self.data.requests + self.data.samples:
            decision = self.engine.decision_for(request)
            if decision.chosen.method != "installments":
                continue
            cap = self.data.profiles[request.user_id].max_installment_months
            with self.subTest(request=request.request_id):
                self.assertIsNotNone(cap)
                self.assertLessEqual(decision.chosen.option.number_of_payments, cap)

    def test_every_installment_plan_matches_a_supplied_option_exactly(self):
        for request in self.data.requests + self.data.samples:
            decision = self.engine.decision_for(request)
            if decision.chosen.method != "installments":
                continue
            option = decision.chosen.option
            with self.subTest(request=request.request_id):
                self.assertIn(option, self.data.options(request.request_id))
                self.assertEqual(decision.chosen.payments, option.schedule())

    def test_every_partial_plan_has_two_payments_summing_to_the_request(self):
        for request in self.data.requests + self.data.samples:
            decision = self.engine.decision_for(request)
            if decision.chosen.method != "partial_payment":
                continue
            with self.subTest(request=request.request_id):
                payments = decision.chosen.payments
                self.assertEqual(len(payments), 2)
                self.assertAlmostEqual(sum(a for _, a in payments), request.requested_amount, places=4)
                self.assertLessEqual(payments[1][0], request.desired_completion_date)

    def test_candidate_records_are_immutable(self):
        decision = self.engine.decision_for(self.data.request("request_01"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            decision.chosen.method = "wait"


if __name__ == "__main__":
    unittest.main()
