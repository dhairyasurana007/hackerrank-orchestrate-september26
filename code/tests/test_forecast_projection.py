"""Recurring projection on the curve (TASKS.md M7b)."""

from __future__ import annotations

import datetime as dt
import unittest

from buyorwait import forecast, loaders, paths, recurrence
from buyorwait.fx import RateTable
from buyorwait.state import build_state
from code.tests.test_forecast_explicit import _event, _profile, _request  # noqa: F401

DATASET = paths.find_dataset()
START = dt.date(2025, 6, 1)
RATES = RateTable({})


def _curve(events, *, balance=10000.0, when=START, days=90):
    # The pipeline always supplies home-currency amounts keyed by event_id; without them a
    # series has no figure to project and is rejected, so the fixtures supply them too.
    amounts = {event.event_id: event.amount for event in events if event.amount is not None}
    return forecast.build(
        request=_request(when=when),
        profile=_profile(balance=balance),
        events=events,
        rates=RATES,
        amounts=amounts,
        days=days,
    )


def _settled_monthly(count, day, amount, *, category="rent", direction="debit", description="Rent",
                     first_year=2025, first_month=1):
    events = []
    for index in range(count):
        month = first_month + index
        year = first_year + (month - 1) // 12
        events.append(
            _event(
                f"{category}_{index}",
                dt.date(year, (month - 1) % 12 + 1, day),
                amount,
                status="settled",
                direction=direction,
                category=category,
                description=description,
            )
        )
    return events


class TestPhaseCorrectness(unittest.TestCase):
    """Phase beats amount: a monthly series must land on its day-of-month."""

    def test_a_monthly_series_lands_on_its_modal_day_not_last_plus_median_gap(self):
        # Five rents on the 3rd; the last is 2025-05-03 and gaps average 30.25 days, so
        # last-occurrence-plus-median-gap would put the next one on 2025-06-02, a day early
        # and then drifting further every month.
        events = _settled_monthly(5, 3, 1000.0, first_month=1)
        curve = _curve(events, when=dt.date(2025, 5, 20))
        rent_days = [m.when for m in curve.projected if m.category == "rent"]
        self.assertEqual(rent_days[:3], [dt.date(2025, 6, 3), dt.date(2025, 7, 3), dt.date(2025, 8, 3)])

    def test_a_monthly_series_does_not_drift_across_a_short_month(self):
        events = [
            _event("rent_0", dt.date(2024, 12, 31), 500.0, status="settled"),
            _event("rent_1", dt.date(2025, 1, 31), 500.0, status="settled"),
            _event("rent_2", dt.date(2025, 2, 28), 500.0, status="settled"),
            _event("rent_3", dt.date(2025, 3, 31), 500.0, status="settled"),
        ]
        curve = _curve(events, when=dt.date(2025, 4, 2))
        days = [m.when for m in curve.projected]
        self.assertEqual(days[:3], [dt.date(2025, 4, 30), dt.date(2025, 5, 31), dt.date(2025, 6, 30)])

    def test_a_sub_monthly_series_steps_by_its_own_interval(self):
        events = [
            _event(f"g_{i}", dt.date(2025, 5, 1) + dt.timedelta(days=10 * i), 50.0,
                   status="settled", category="groceries")
            for i in range(5)
        ]
        curve = _curve(events, when=dt.date(2025, 6, 12))
        days = [m.when for m in curve.projected]
        self.assertEqual(days[:3], [dt.date(2025, 6, 20), dt.date(2025, 6, 30), dt.date(2025, 7, 10)])

    def test_a_suppressed_series_contributes_nothing(self):
        stale = _settled_monthly(
            4, 20, 900.0, category="salary", direction="credit", description="Second income",
            first_month=1,
        )
        curve = _curve(stale, when=dt.date(2025, 7, 15))
        self.assertEqual(curve.projected, ())
        self.assertEqual(curve.drawdown(), 0.0)


class TestNoDoubleCounting(unittest.TestCase):
    """An explicit scheduled row *is* that series' next occurrence."""

    def test_a_scheduled_row_is_not_projected_onto_again(self):
        history = _settled_monthly(
            5, 15, 1343.54, category="salary", direction="credit", description="Primary household salary",
            first_year=2024, first_month=10,
        )
        scheduled = _event(
            "sched", dt.date(2025, 3, 15), 1343.54, status="scheduled", direction="credit",
            category="salary", description="Next confirmed salary",
        )
        curve = _curve(history + [scheduled], when=dt.date(2025, 3, 7))
        credits_on_the_15th = [
            m.when for m in curve.projected if m.when == dt.date(2025, 3, 15)
        ]
        self.assertEqual(credits_on_the_15th, [])
        self.assertAlmostEqual(curve.values[8] - curve.values[7], 1343.54)  # counted exactly once

    def test_the_claim_is_keyed_on_category_not_on_description(self):
        """The scheduled row's wording differs from the history it continues."""
        history = _settled_monthly(
            5, 15, 1000.0, category="salary", direction="credit", description="Primary household salary",
            first_year=2024, first_month=10,
        )
        scheduled = _event(
            "sched", dt.date(2025, 3, 15), 1000.0, status="scheduled", direction="credit",
            category="salary", description="Something worded completely differently",
        )
        curve = _curve(history + [scheduled], when=dt.date(2025, 3, 7))
        self.assertAlmostEqual(curve.values[8] - curve.values[7], 1000.0)


class TestConfirmedIncomeSeeding(unittest.TestCase):
    """PLAN.md assumption 4: a scheduled credit recurs monthly from its settlement date."""

    def test_a_scheduled_credit_with_no_history_seeds_a_monthly_series(self):
        scheduled = _event(
            "sched", dt.date(2025, 6, 15), 23320.0, status="scheduled", direction="credit",
            category="salary", description="Next confirmed salary",
        )
        curve = _curve([scheduled])
        seeded = [m.when for m in curve.projected if m.amount > 0]
        self.assertEqual(seeded[:2], [dt.date(2025, 7, 15), dt.date(2025, 8, 15)])
        self.assertTrue(all(m.amount == 23320.0 for m in curve.projected if m.amount > 0))

    def test_seeding_does_not_fire_where_a_series_already_projects(self):
        history = _settled_monthly(
            5, 15, 1000.0, category="salary", direction="credit", description="Primary household salary",
            first_year=2024, first_month=10,
        )
        scheduled = _event(
            "sched", dt.date(2025, 3, 15), 1000.0, status="scheduled", direction="credit",
            category="salary", description="Next confirmed salary",
        )
        curve = _curve(history + [scheduled], when=dt.date(2025, 3, 7))
        by_date = {}
        for movement in curve.projected:
            if movement.amount > 0:
                by_date[movement.when] = by_date.get(movement.when, 0) + 1
        self.assertTrue(all(count == 1 for count in by_date.values()), by_date)

    def test_a_scheduled_debit_seeds_nothing(self):
        scheduled = _event("sched", dt.date(2025, 6, 15), 500.0, status="scheduled")
        curve = _curve([scheduled])
        self.assertEqual(curve.projected, ())


class TestSafetyHorizon(unittest.TestCase):
    def test_the_trough_can_be_asked_for_through_an_earlier_date(self):
        events = [
            _event("e1", START + dt.timedelta(days=10), 1000.0),
            _event("e2", START + dt.timedelta(days=80), 5000.0),
        ]
        curve = forecast.build(
            request=_request(when=START), profile=_profile(), events=events, rates=RATES
        )
        self.assertEqual(curve.drawdown(), 6000.0)
        self.assertEqual(curve.drawdown(through=START + dt.timedelta(days=40)), 1000.0)

    def test_a_horizon_beyond_the_window_is_clamped(self):
        curve = _curve([])
        self.assertEqual(curve.horizon_index(START + dt.timedelta(days=900)), 89)

    def test_suffix_minima_stay_non_decreasing_inside_the_horizon(self):
        events = [
            _event("e1", START + dt.timedelta(days=10), 3000.0),
            _event("e2", START + dt.timedelta(days=20), 5000.0, direction="credit"),
            _event("e3", START + dt.timedelta(days=40), 1000.0),
        ]
        curve = _curve(events)
        horizon = START + dt.timedelta(days=50)
        minima = curve.suffix_minima(through=horizon)[: curve.horizon_index(horizon) + 1]
        self.assertEqual(minima, sorted(minima))


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

    def test_projection_improves_on_the_recorded_explicit_only_baseline(self):
        """The M7a baseline was median 100%, mean 94.2%. This must beat both.

        The exact figures are the calibrated ones from M7c; the threshold file records how
        each step got there.
        """
        from evaluation import drawdown

        from buyorwait.pipeline import Engine

        report = drawdown.grade(Engine.build(self.data, use_llm=False).predicted_drawdown, self.data)
        self.assertLess(report.median_error, 1.0)
        self.assertLess(report.mean_error, 0.9421)
        self.assertAlmostEqual(report.median_error, 0.1125, places=3)
        self.assertAlmostEqual(report.mean_error, 0.2897, places=3)
        self.assertEqual(report.within(0.10), 9)
        self.assertEqual(report.within(0.25), 16)
        self.assertEqual(report.bound_violations, [])

    def test_the_committed_threshold_is_met(self):
        from evaluation import drawdown

        from buyorwait.pipeline import Engine

        report = drawdown.grade(Engine.build(self.data, use_llm=False).predicted_drawdown, self.data)
        self.assertEqual(drawdown.check_thresholds(report), [])

    def test_users_with_no_future_events_now_have_a_projected_drawdown(self):
        """request_11 and request_15 were flat at M7a; projection is all they have."""
        for request_id in ("request_11", "request_15"):
            with self.subTest(request=request_id):
                curve = self._curve_for(self.data.request(request_id))
                self.assertEqual(curve.explicit, ())
                self.assertGreater(len(curve.projected), 0)
                self.assertGreater(curve.drawdown(), 0.0)

    def test_every_request_projects_without_error(self):
        for request in self.data.requests + self.data.samples:
            with self.subTest(request=request.request_id):
                curve = self._curve_for(request)
                self.assertEqual(curve.values[0], self.data.profiles[request.user_id].current_available_balance)

    def test_user_01_income_is_seeded_from_its_scheduled_row(self):
        """One prior prorated salary and one scheduled row: nothing detectable in history."""
        request = self.data.request("request_01")
        curve = self._curve_for(request)
        seeded = [s for s in curve.series if s.is_income and s.projects]
        self.assertEqual(len(seeded), 1)
        self.assertIn("confirmed by a scheduled row", seeded[0].reason)
        self.assertAlmostEqual(seeded[0].amount, 23320.0)


if __name__ == "__main__":
    unittest.main()
