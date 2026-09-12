"""The safety predicate and the closed-form solvers (TASKS.md M8).

The randomised cross-check below is the one that licenses using the closed form at all: over
hundreds of random curves, the closed-form earliest-date must agree exactly with a
brute-force day-by-day search.
"""

from __future__ import annotations

import datetime as dt
import random
import unittest

from buyorwait import forecast, loaders, paths, safety
from buyorwait.fx import RateTable
from buyorwait.state import build_state

DATASET = paths.find_dataset()
START = dt.date(2025, 6, 1)


def _curve_from(values, start=START) -> forecast.Curve:
    return forecast.Curve(start=start, opening=values[0], values=list(values))


def _random_curve(rng, length=90) -> forecast.Curve:
    opening = rng.uniform(500, 500000)
    values = [opening]
    for _ in range(length - 1):
        step = 0.0
        if rng.random() < 0.35:
            step = rng.uniform(-opening * 0.12, opening * 0.10)
        values.append(values[-1] + step)
    return forecast.Curve(start=START, opening=opening, values=values)


class TestIsSafe(unittest.TestCase):
    def test_a_curve_above_the_floor_is_safe(self):
        self.assertTrue(safety.is_safe(_curve_from([100.0] * 90), 50.0))

    def test_a_single_dip_below_the_floor_is_unsafe(self):
        values = [100.0] * 90
        values[42] = 49.0
        self.assertFalse(safety.is_safe(_curve_from(values), 50.0))

    def test_touching_the_floor_exactly_is_safe(self):
        self.assertTrue(safety.is_safe(_curve_from([50.0] * 90), 50.0))

    def test_a_payment_that_breaches_the_floor_is_unsafe(self):
        curve = _curve_from([100.0] * 90)
        self.assertTrue(safety.is_safe(curve, 50.0, [(START, 50.0)]))
        self.assertFalse(safety.is_safe(curve, 50.0, [(START, 51.0)]))

    def test_a_dip_outside_the_horizon_does_not_make_a_plan_unsafe(self):
        values = [100.0] * 90
        values[80] = 0.0
        curve = _curve_from(values)
        self.assertFalse(safety.is_safe(curve, 50.0))
        self.assertTrue(safety.is_safe(curve, 50.0, through=START + dt.timedelta(days=40)))

    def test_a_payment_beyond_the_horizon_is_still_checked_on_its_own_day(self):
        curve = _curve_from([100.0] * 90)
        horizon = START + dt.timedelta(days=10)
        self.assertFalse(
            safety.is_safe(curve, 50.0, [(START + dt.timedelta(days=60), 60.0)], through=horizon)
        )

    def test_a_payment_dated_past_the_window_cannot_be_checked_and_is_not_counted_unsafe(self):
        curve = _curve_from([100.0] * 90)
        self.assertTrue(safety.is_safe(curve, 50.0, [(START + dt.timedelta(days=500), 10000.0)]))


class TestSafeAmountToday(unittest.TestCase):
    def test_it_is_the_trough_less_the_floor(self):
        values = [1000.0] * 90
        values[30] = 400.0
        self.assertAlmostEqual(safety.safe_amount_today(_curve_from(values), 100.0, 10000.0), 300.0)

    def test_it_never_exceeds_the_requested_amount(self):
        self.assertAlmostEqual(safety.safe_amount_today(_curve_from([1000.0] * 90), 100.0, 250.0), 250.0)

    def test_it_never_goes_below_zero(self):
        self.assertEqual(safety.safe_amount_today(_curve_from([50.0] * 90), 100.0, 250.0), 0.0)

    def test_it_never_exceeds_the_trough_less_the_floor_over_random_curves(self):
        """The property the whole contract rests on."""
        rng = random.Random(20260913)
        for _ in range(300):
            curve = _random_curve(rng)
            floor = curve.opening * rng.uniform(0.05, 0.6)
            cap = curve.opening * rng.uniform(0.01, 1.5)
            amount = safety.safe_amount_today(curve, floor, cap)
            self.assertLessEqual(amount, max(0.0, curve.minimum() - floor) + 1e-6)
            self.assertLessEqual(amount, cap + 1e-9)
            self.assertGreaterEqual(amount, 0.0)

    def test_paying_the_safe_amount_is_always_safe(self):
        rng = random.Random(4242)
        for _ in range(300):
            curve = _random_curve(rng)
            floor = curve.opening * rng.uniform(0.05, 0.6)
            amount = safety.safe_amount_today(curve, floor, curve.opening * 2)
            if amount > 0:
                self.assertTrue(safety.is_safe(curve, floor, [(curve.start, amount)]))

    def test_paying_more_than_the_safe_amount_is_not_safe(self):
        values = [1000.0] * 90
        values[30] = 400.0
        curve = _curve_from(values)
        amount = safety.safe_amount_today(curve, 100.0, 10000.0)
        self.assertFalse(safety.is_safe(curve, 100.0, [(START, amount + 0.01)]))


class TestShiftIdentity(unittest.TestCase):
    def test_paying_x_today_lowers_every_point_by_exactly_x(self):
        rng = random.Random(7)
        for _ in range(100):
            curve = _random_curve(rng)
            amount = curve.opening * rng.uniform(0.01, 0.5)
            shifted = curve.shifted([(curve.start, amount)])
            for index in range(len(curve)):
                self.assertAlmostEqual(curve.values[index] - shifted.values[index], amount, places=6)

    def test_the_trough_moves_down_by_exactly_the_payment(self):
        rng = random.Random(8)
        for _ in range(100):
            curve = _random_curve(rng)
            amount = curve.opening * rng.uniform(0.01, 0.5)
            shifted = curve.shifted([(curve.start, amount)])
            self.assertAlmostEqual(curve.minimum() - shifted.minimum(), amount, places=6)


class TestSuffixMinima(unittest.TestCase):
    def test_suffix_minima_are_non_decreasing_over_random_curves(self):
        rng = random.Random(99)
        for _ in range(300):
            minima = _random_curve(rng).suffix_minima()
            self.assertEqual(minima, sorted(minima))

    def test_inside_a_horizon_each_entry_is_the_minimum_to_the_horizon(self):
        values = [float(v) for v in range(90, 0, -1)]
        curve = _curve_from(values)
        minima = curve.suffix_minima(through=START + dt.timedelta(days=20))
        self.assertEqual(minima[0], min(values[:21]))
        self.assertEqual(minima[20], values[20])

    def test_beyond_the_horizon_each_entry_is_the_days_own_balance(self):
        values = [float(v) for v in range(90, 0, -1)]
        curve = _curve_from(values)
        minima = curve.suffix_minima(through=START + dt.timedelta(days=20))
        for index in range(21, 90):
            self.assertEqual(minima[index], values[index])


class TestEarliestFullPaymentDate(unittest.TestCase):
    def test_it_is_the_request_date_when_the_full_amount_is_already_safe(self):
        curve = _curve_from([1000.0] * 90)
        self.assertEqual(safety.earliest_full_payment_date(curve, 100.0, 500.0), START)

    def test_it_is_empty_when_no_day_can_carry_the_payment(self):
        curve = _curve_from([1000.0] * 90)
        self.assertIsNone(safety.earliest_full_payment_date(curve, 100.0, 5000.0))

    def test_it_lands_on_the_first_day_after_an_income_step(self):
        curve = _curve_from([500.0] * 30 + [2500.0] * 60)
        self.assertEqual(
            safety.earliest_full_payment_date(curve, 100.0, 1000.0),
            START + dt.timedelta(days=30),
        )

    def test_a_later_dip_pushes_the_date_past_it(self):
        curve = _curve_from([2500.0] * 40 + [300.0] * 5 + [2500.0] * 45)
        horizon = START + dt.timedelta(days=89)
        self.assertEqual(
            safety.earliest_full_payment_date(curve, 100.0, 1000.0, through=horizon),
            START + dt.timedelta(days=45),
        )

    def test_the_closed_form_agrees_with_a_brute_force_search_over_random_curves(self):
        """The cross-check that licenses replacing the search with the closed form."""
        rng = random.Random(20260912)
        for _ in range(400):
            curve = _random_curve(rng)
            floor = curve.opening * rng.uniform(0.05, 0.6)
            amount = curve.opening * rng.uniform(0.01, 1.2)
            horizon = curve.date_at(rng.randrange(len(curve)))
            self.assertEqual(
                safety.earliest_full_payment_date(curve, floor, amount, through=horizon),
                safety.brute_force_earliest(curve, floor, amount, through=horizon),
            )

    def test_the_two_agree_with_no_horizon_given(self):
        rng = random.Random(31337)
        for _ in range(200):
            curve = _random_curve(rng)
            floor = curve.opening * rng.uniform(0.05, 0.6)
            amount = curve.opening * rng.uniform(0.01, 1.2)
            self.assertEqual(
                safety.earliest_full_payment_date(curve, floor, amount),
                safety.brute_force_earliest(curve, floor, amount),
            )


class TestAgainstTheRealDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = loaders.load_dataset(DATASET)
        cls.rates = RateTable(cls.data.rates)

    def _curve_for(self, request):
        state = build_state(
            request, self.data.profiles[request.user_id], self.data.events(request.user_id), self.rates
        )
        return forecast.build(
            request=request,
            profile=state.profile,
            events=state.events,
            rates=self.rates,
            events_by_id=state.events_by_id,
            amounts=state.amounts,
        )

    def test_the_solvers_agree_on_every_real_request(self):
        for request in self.data.requests + self.data.samples:
            with self.subTest(request=request.request_id):
                curve = self._curve_for(request)
                floor = self.data.profiles[request.user_id].minimum_balance_to_keep
                horizon = request.desired_completion_date
                self.assertEqual(
                    safety.earliest_full_payment_date(
                        curve, floor, request.requested_amount, through=horizon
                    ),
                    safety.brute_force_earliest(
                        curve, floor, request.requested_amount, through=horizon
                    ),
                )

    def test_the_safe_amount_is_inside_the_contract_range_on_every_real_request(self):
        for request in self.data.requests + self.data.samples:
            with self.subTest(request=request.request_id):
                profile = self.data.profiles[request.user_id]
                amount = safety.safe_amount_today(
                    self._curve_for(request),
                    profile.minimum_balance_to_keep,
                    request.requested_amount,
                    through=request.desired_completion_date,
                )
                self.assertGreaterEqual(amount, 0.0)
                self.assertLessEqual(amount, request.requested_amount)


if __name__ == "__main__":
    unittest.main()
