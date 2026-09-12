"""The explicit-event curve (TASKS.md M7a)."""

from __future__ import annotations

import datetime as dt
import unittest

from buyorwait import forecast, loaders, paths
from buyorwait.contract import FORECAST_DAYS
from buyorwait.fx import RateTable
from buyorwait.records import Event, Profile, Request
from buyorwait.state import build_state

DATASET = paths.find_dataset()
START = dt.date(2025, 6, 1)
RATES = RateTable({})


def _profile(balance=10000.0, minimum=1000.0) -> Profile:
    return Profile(
        user_id="user_x",
        home_currency="EUR",
        current_available_balance=balance,
        minimum_balance_to_keep=minimum,
        financial_priorities=("savings",),
        expense_categories_to_protect=("rent",),
        categories_willing_to_reduce=("dining",),
        categories_willing_to_stop=("gym",),
        payment_methods_user_will_consider=("full_payment",),
        max_installment_months=None,
    )


def _request(requested=1000.0, when=START) -> Request:
    return Request(
        request_id="request_x",
        user_id="user_x",
        request_date=when,
        request_type="purchase",
        requested_amount=requested,
        desired_completion_date=when + dt.timedelta(days=30),
        allows_partial_payment=True,
        request_text="",
    )


def _event(event_id, when, amount, *, status="scheduled", direction="debit", **kw) -> Event:
    base = dict(
        event_id=event_id,
        user_id="user_x",
        event_type="expense" if direction == "debit" else "income",
        description="Something",
        category="rent",
        direction=direction,
        amount=amount,
        currency="EUR",
        event_date=when,
        settlement_date=when,
        status=status,
        linked_event_id=None,
        flexibility="fixed",
        minimum_allowed_amount=None,
    )
    base.update(kw)
    return Event(**base)


def _curve(events, *, balance=10000.0, when=START):
    return forecast.build_explicit(
        request=_request(when=when),
        profile=_profile(balance=balance),
        events=events,
        rates=RATES,
    )


class TestCurveShape(unittest.TestCase):
    def test_the_window_is_ninety_daily_points_starting_at_the_request_date(self):
        curve = _curve([])
        self.assertEqual(len(curve), FORECAST_DAYS)
        self.assertEqual(curve.date_at(0), START)
        self.assertEqual(curve.end, START + dt.timedelta(days=FORECAST_DAYS - 1))

    def test_day_zero_is_the_opening_balance(self):
        self.assertEqual(_curve([]).values[0], 10000.0)

    def test_a_user_with_no_future_events_has_a_flat_curve_and_zero_drawdown(self):
        curve = _curve([_event("event_1", START - dt.timedelta(days=10), 500.0, status="settled")])
        self.assertEqual(set(curve.values), {10000.0})
        self.assertEqual(curve.drawdown(), 0.0)

    def test_a_scheduled_debit_lowers_the_curve_from_its_settlement_date(self):
        curve = _curve([_event("event_1", START + dt.timedelta(days=10), 2500.0)])
        self.assertEqual(curve.values[9], 10000.0)
        self.assertEqual(curve.values[10], 7500.0)
        self.assertEqual(curve.values[-1], 7500.0)
        self.assertEqual(curve.drawdown(), 2500.0)
        self.assertEqual(curve.trough_date(), START + dt.timedelta(days=10))

    def test_a_scheduled_credit_raises_the_curve_and_leaves_no_drawdown(self):
        curve = _curve([_event("event_1", START + dt.timedelta(days=5), 2000.0, direction="credit")])
        self.assertEqual(curve.values[5], 12000.0)
        self.assertEqual(curve.drawdown(), 0.0)

    def test_a_pending_debit_is_reserved_on_its_settlement_date_not_its_event_date(self):
        event = _event(
            "event_1",
            START + dt.timedelta(days=20),
            300.0,
            status="pending",
            event_date=START + dt.timedelta(days=15),
        )
        curve = _curve([event])
        self.assertEqual(curve.values[19], 10000.0)
        self.assertEqual(curve.values[20], 9700.0)

    def test_movements_on_the_same_day_accumulate(self):
        day = START + dt.timedelta(days=7)
        curve = _curve([_event("event_1", day, 100.0), _event("event_2", day, 250.0)])
        self.assertEqual(curve.values[7], 9650.0)

    def test_an_event_beyond_the_window_is_excluded(self):
        curve = _curve([_event("event_1", START + dt.timedelta(days=200), 5000.0)])
        self.assertEqual(curve.drawdown(), 0.0)
        self.assertEqual(curve.explicit, ())

    def test_the_curve_is_exact_on_a_hand_built_multi_event_fixture(self):
        events = [
            _event("event_1", START + dt.timedelta(days=2), 1000.0),
            _event("event_2", START + dt.timedelta(days=5), 4000.0, direction="credit"),
            _event("event_3", START + dt.timedelta(days=9), 6000.0),
            _event("event_4", START + dt.timedelta(days=12), 500.0, status="pending"),
        ]
        curve = _curve(events)
        expected = {0: 10000.0, 1: 10000.0, 2: 9000.0, 4: 9000.0, 5: 13000.0, 8: 13000.0,
                    9: 7000.0, 11: 7000.0, 12: 6500.0, 89: 6500.0}
        for index, value in expected.items():
            with self.subTest(day=index):
                self.assertEqual(curve.values[index], value)
        self.assertEqual(curve.minimum(), 6500.0)
        self.assertEqual(curve.drawdown(), 3500.0)


class TestSuffixMinimaAndShift(unittest.TestCase):
    def test_suffix_minima_are_non_decreasing(self):
        curve = _curve(
            [
                _event("event_1", START + dt.timedelta(days=10), 3000.0),
                _event("event_2", START + dt.timedelta(days=20), 5000.0, direction="credit"),
                _event("event_3", START + dt.timedelta(days=40), 1000.0),
            ]
        )
        minima = curve.suffix_minima()
        self.assertEqual(minima, sorted(minima))
        self.assertEqual(minima[0], curve.minimum())

    def test_paying_x_today_lowers_every_point_by_exactly_x(self):
        curve = _curve([_event("event_1", START + dt.timedelta(days=10), 3000.0)])
        shifted = curve.shifted([(START, 1500.0)])
        for index in range(len(curve)):
            with self.subTest(day=index):
                self.assertAlmostEqual(shifted.values[index] - curve.values[index], -1500.0)

    def test_a_payment_dated_mid_window_lowers_only_from_that_day(self):
        curve = _curve([])
        shifted = curve.shifted([(START + dt.timedelta(days=30), 400.0)])
        self.assertEqual(shifted.values[29], 10000.0)
        self.assertEqual(shifted.values[30], 9600.0)

    def test_a_payment_outside_the_window_is_ignored_rather_than_clamped(self):
        curve = _curve([])
        shifted = curve.shifted([(START + dt.timedelta(days=500), 400.0)])
        self.assertEqual(shifted.values, curve.values)


class TestAgainstTheRealDataset(unittest.TestCase):
    """The explicit-only baseline, and what it says about the size of the projection job."""

    @classmethod
    def setUpClass(cls):
        cls.data = loaders.load_dataset(DATASET)
        cls.rates = RateTable(cls.data.rates)

    def _curve_for(self, request):
        state = build_state(
            request, self.data.profiles[request.user_id], self.data.events(request.user_id), self.rates
        )
        return forecast.build_explicit(
            request=request,
            profile=state.profile,
            events=state.events,
            rates=self.rates,
            events_by_id=state.events_by_id,
            amounts=state.amounts,
        )

    def test_users_with_no_future_events_produce_a_flat_curve_and_zero_drawdown(self):
        """request_11 and request_15: every event predates request_date.

        Correct at this stage, and the clearest statement of how much of each drawdown has
        to come from projection rather than from anything explicit.
        """
        for request_id in ("request_11", "request_15"):
            with self.subTest(request=request_id):
                curve = self._curve_for(self.data.request(request_id))
                self.assertEqual(curve.explicit, ())
                self.assertEqual(len(set(curve.values)), 1)
                self.assertEqual(curve.drawdown(), 0.0)

    def test_fifteen_of_the_25_samples_have_no_explicit_drawdown_at_all(self):
        flat = [r.request_id for r in self.data.samples if self._curve_for(r).drawdown() == 0.0]
        self.assertEqual(len(flat), 15)

    def test_every_request_builds_a_curve_without_error(self):
        for request in self.data.requests + self.data.samples:
            with self.subTest(request=request.request_id):
                curve = self._curve_for(request)
                self.assertEqual(len(curve), FORECAST_DAYS)
                self.assertEqual(curve.values[0], self.data.profiles[request.user_id].current_available_balance)

    def test_the_recorded_explicit_only_baseline_still_holds(self):
        """M5b's number for *this* forecaster, kept in the suite so later work stays comparable.

        Graded on build_explicit directly rather than through the Engine: the Engine moved on
        to the projected curve at M7b, while the claim this test makes is about the
        explicit-only forecaster, which still exists and must keep reporting the figure the
        threshold file records against it.
        """
        from evaluation import drawdown

        def forecaster(request):
            return self._curve_for(request).drawdown(through=request.desired_completion_date)

        report = drawdown.grade(forecaster, self.data)
        self.assertAlmostEqual(report.median_error, 1.0, places=4)
        self.assertAlmostEqual(report.mean_error, 0.9421, places=3)
        self.assertEqual(report.bound_violations, [])


if __name__ == "__main__":
    unittest.main()
