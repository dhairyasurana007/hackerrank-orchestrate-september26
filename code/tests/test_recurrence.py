"""Recurrence detection and income gating (TASKS.md M6).

The fixture set mirrors all six diagnosed failures of PLAN.md 5.5, each asserting whether
the series projects and at what amount, plus the guard case that must keep projecting.
"""

from __future__ import annotations

import datetime as dt
import unittest

from buyorwait import loaders, paths, recurrence
from buyorwait.fx import RateTable
from buyorwait.records import Event

DATASET = paths.find_dataset()


def _event(event_id, when, amount, *, direction="debit", category="groceries", description="Shop", **kw):
    base = dict(
        event_id=event_id,
        user_id="user_x",
        event_type="expense" if direction == "debit" else "income",
        description=description,
        category=category,
        direction=direction,
        amount=amount,
        currency="EUR",
        event_date=when,
        settlement_date=when,
        status="settled",
        linked_event_id=None,
        flexibility="fixed",
        minimum_allowed_amount=None,
    )
    base.update(kw)
    return Event(**base)


def _monthly(count, day, amount, *, start_year=2025, start_month=1, **kw):
    events = []
    for index in range(count):
        month = start_month + index
        year = start_year + (month - 1) // 12
        events.append(_event(f"event_{index}", dt.date(year, (month - 1) % 12 + 1, day), amount, **kw))
    return events


def _detect(events, as_of):
    amounts = {e.event_id: e.amount for e in events if e.amount is not None}
    return recurrence.detect(events, as_of=as_of, amounts=amounts)


def _one(events, as_of):
    series = _detect(events, as_of)
    assert len(series) == 1, [(s.key, s.reason) for s in series]
    return series[0]


class TestCadenceDetection(unittest.TestCase):
    def test_a_monthly_series_is_anchored_to_its_modal_day_of_month(self):
        series = _one(_monthly(5, 15, 100.0, category="rent"), dt.date(2025, 5, 20))
        self.assertTrue(series.projects)
        self.assertEqual(series.cadence, "monthly")
        self.assertEqual(series.anchor_day, 15)

    def test_any_consistent_interval_inside_the_band_is_accepted(self):
        """A fixed weekly/fortnightly/monthly menu rejected real 10- and 21-day series."""
        for interval in (5, 7, 10, 14, 21):
            with self.subTest(interval=interval):
                events = [
                    _event(f"event_{i}", dt.date(2025, 1, 1) + dt.timedelta(days=interval * i), 50.0)
                    for i in range(6)
                ]
                series = _one(events, dt.date(2025, 1, 1) + dt.timedelta(days=interval * 6))
                self.assertTrue(series.projects, series.reason)
                self.assertEqual(series.interval_days, interval)

    def test_an_interval_slower_than_a_month_is_treated_as_one_off(self):
        events = [
            _event(f"event_{i}", dt.date(2025, 1, 1) + dt.timedelta(days=60 * i), 50.0) for i in range(5)
        ]
        self.assertFalse(_one(events, dt.date(2025, 9, 1)).projects)

    def test_an_irregular_series_does_not_project(self):
        offsets = (0, 3, 40, 47, 90, 91)
        events = [
            _event(f"event_{i}", dt.date(2025, 1, 1) + dt.timedelta(days=offset), 50.0)
            for i, offset in enumerate(offsets)
        ]
        series = _one(events, dt.date(2025, 4, 5))
        self.assertFalse(series.projects)
        self.assertIn("no stable interval", series.reason)

    def test_two_occurrences_are_not_enough(self):
        series = _one(_monthly(2, 10, 100.0), dt.date(2025, 3, 1))
        self.assertFalse(series.projects)
        self.assertIn("needs 3", series.reason)

    def test_a_monthly_anchor_past_the_end_of_a_short_month_is_clamped(self):
        events = [
            _event("event_0", dt.date(2025, 1, 31), 100.0),
            _event("event_1", dt.date(2025, 2, 28), 100.0),
            _event("event_2", dt.date(2025, 3, 31), 100.0),
        ]
        series = _one(events, dt.date(2025, 4, 2))
        self.assertTrue(series.projects)
        dates = recurrence.occurrences_in_window(series, dt.date(2025, 4, 2), dt.date(2025, 7, 1))
        self.assertEqual(dates, (dt.date(2025, 4, 30), dt.date(2025, 5, 31), dt.date(2025, 6, 30)))


class TestSeriesIdentity(unittest.TestCase):
    def test_income_keys_on_category_and_description_so_two_series_stay_separate(self):
        """user_11: base salary on the 15th and commission on the 24th, both `salary`."""
        base = _monthly(5, 15, 23256000.0, direction="credit", category="salary", description="Base salary")
        commission = [
            _event(f"comm_{i}", dt.date(2025, 1 + i, 24), 10000000.0 + i, direction="credit",
                   category="salary", description="Performance commission")
            for i in range(5)
        ]
        series = _detect(base + commission, dt.date(2025, 5, 20))
        self.assertEqual(len(series), 2)
        by_description = {s.description_key: s for s in series}
        self.assertEqual(set(by_description), {"base salary", "performance commission"})
        self.assertTrue(all(s.projects for s in series))

    def test_expenses_key_on_category_alone_so_varying_descriptions_stay_one_series(self):
        """Description-keying fragments expense series and measurably worsened accuracy."""
        descriptions = ["Weekly produce market", "Grocery delivery", "Local market purchase", "Bulk pantry shop"]
        events = [
            _event(f"event_{i}", dt.date(2025, 1, 6) + dt.timedelta(days=7 * i), 100.0,
                   description=descriptions[i % len(descriptions)])
            for i in range(8)
        ]
        series = _detect(events, dt.date(2025, 2, 27))
        self.assertEqual(len(series), 1)
        self.assertTrue(series[0].projects)
        self.assertEqual(series[0].occurrences, 8)

    def test_refunds_and_investment_rows_never_form_a_series(self):
        events = _monthly(5, 10, 100.0, direction="credit", event_type="refund", category="shopping")
        self.assertEqual(_detect(events, dt.date(2025, 6, 1)), ())


class TestTheSixDiagnosedFailures(unittest.TestCase):
    """One fixture per diagnosed case, asserting the projection verdict and the amount."""

    def test_user_05_terminated_by_description(self):
        events = _monthly(4, 15, 14740.0, direction="credit", category="salary", description="Payroll credit")
        events.append(
            _event("final", dt.date(2025, 5, 15), 14740.0, direction="credit", category="salary",
                   description="Final employer payroll")
        )
        series = {s.description_key: s for s in _detect(events, dt.date(2025, 6, 6))}
        self.assertFalse(series["payroll credit"].projects)
        self.assertIn("stale", series["payroll credit"].reason)
        self.assertFalse(series["final employer payroll"].projects)

    def test_termination_wording_suppresses_an_otherwise_fresh_income_series(self):
        """The wording gate in isolation, with staleness deliberately not in play."""
        events = _monthly(5, 15, 14740.0, direction="credit", category="salary",
                          description="Final employer payroll")
        series = _one(events, dt.date(2025, 5, 20))
        self.assertFalse(series.projects)
        self.assertIn("marks the series as ended", series.reason)

    def test_user_10_variable_platform_payouts_split_into_separate_irregular_series(self):
        """Real rows, because the shape is the point: one stream relabelled week to week.

        Description-keying splits user_10's gig income into four series, and the three whose
        occurrences are genuinely irregular are rejected outright. The fourth
        ("Driver platform payout") does clear the interval test on four occurrences, so
        structure alone does not suppress it; the message saying the next payout is pending
        and not withdrawable is what does, at M15. That residual is measured rather than
        assumed - see the drawdown harness.
        """
        data = loaders.load_dataset(DATASET)
        request = next(r for r in data.samples if r.user_id == "user_10")
        events = data.events("user_10")
        amounts = {e.event_id: e.amount for e in events if e.amount is not None}
        income = {
            s.description_key: s
            for s in recurrence.detect(events, as_of=request.request_date, amounts=amounts)
            if s.is_income
        }
        self.assertEqual(len(income), 4)
        for key in ("delivery platform payout", "task marketplace payout", "weekly app earnings"):
            with self.subTest(series=key):
                self.assertFalse(income[key].projects)
                self.assertIn("no stable interval", income[key].reason)

    def test_user_11_two_income_series_in_one_category_are_gated_separately(self):
        base = _monthly(5, 15, 23256000.0, direction="credit", category="salary", description="Base salary")
        commission = [
            _event(f"comm_{i}", dt.date(2024, 12 + i if i == 0 else i, 24), 15000000.0, direction="credit",
                   category="salary", description="Performance commission")
            for i in range(1, 4)
        ]
        series = {s.description_key: s for s in _detect(base + commission, dt.date(2025, 5, 3))}
        self.assertTrue(series["base salary"].projects)
        self.assertAlmostEqual(series["base salary"].amount, 23256000.0)
        self.assertFalse(series["performance commission"].projects)

    def test_user_13_a_silently_stopping_series_is_caught_by_its_own_phase(self):
        primary = _monthly(5, 15, 1343.54, direction="credit", category="salary",
                           description="Primary household salary", start_year=2023, start_month=10)
        second = [
            _event("second_0", dt.date(2023, 10, 20), 993.88, direction="credit", category="salary",
                   description="Second household income"),
            _event("second_1", dt.date(2023, 11, 20), 771.17, direction="credit", category="salary",
                   description="Second household income"),
            _event("second_2", dt.date(2023, 12, 20), 948.46, direction="credit", category="salary",
                   description="Second household income"),
            _event("second_3", dt.date(2024, 1, 20), 881.45, direction="credit", category="salary",
                   description="Second household income"),
        ]
        series = {s.description_key: s for s in _detect(primary + second, dt.date(2024, 3, 7))}
        self.assertTrue(series["primary household salary"].projects)
        self.assertAlmostEqual(series["primary household salary"].amount, 1343.54)
        self.assertFalse(series["second household income"].projects)
        self.assertEqual(series["second household income"].next_expected, dt.date(2024, 2, 20))

    def test_user_14_a_series_resuming_at_a_new_amount_has_no_history_to_project(self):
        """Two occurrences before leave and one after is not a projectable series.

        The resumption is a message fact, not a historical one; the structural layer must
        decline rather than invent, and M15's amendment is what supplies it.
        """
        events = [
            _event("event_0", dt.date(2025, 3, 15), 2717.0, direction="credit", category="salary",
                   description="Payroll before leave"),
            _event("event_1", dt.date(2025, 4, 15), 2717.0, direction="credit", category="salary",
                   description="Payroll before leave"),
            _event("event_2", dt.date(2025, 7, 15), 2717.0, direction="credit", category="salary",
                   description="Payroll after returning from leave"),
        ]
        series = _detect(events, dt.date(2025, 8, 4))
        self.assertTrue(all(not s.projects for s in series))

    def test_user_15_a_series_existing_only_in_evidence_has_nothing_to_detect(self):
        events = [
            _event("event_0", dt.date(2025, 11, 15), 1661.0, direction="credit", category="salary",
                   description="First-job payroll"),
            _event("event_1", dt.date(2025, 12, 15), 1661.0, direction="credit", category="salary",
                   description="First-job payroll"),
        ]
        series = _one(events, dt.date(2026, 1, 6))
        self.assertFalse(series.projects)
        self.assertIn("needs 3", series.reason)


class TestTheGuardCase(unittest.TestCase):
    """A variance-based gate must never be reintroduced."""

    def test_legitimately_variable_but_ongoing_salary_still_projects(self):
        """user_08: EUR 1422.85 four times then 782.57, and the series continues."""
        events = _monthly(4, 15, 1422.85, direction="credit", category="salary",
                          description="Payroll credit", start_year=2024, start_month=9)
        events.append(
            _event("event_4", dt.date(2025, 1, 15), 782.57, direction="credit", category="salary",
                   description="Payroll credit")
        )
        series = _one(events, dt.date(2025, 2, 7))
        self.assertTrue(series.projects, series.reason)
        self.assertAlmostEqual(series.amount, (1422.85 * 4 + 782.57) / 5, places=2)

    def test_a_halved_then_continuing_series_projects_at_the_mean_not_the_max(self):
        """Blanket max-selection was measured and made accuracy worse."""
        amounts = [1000.0, 1000.0, 1000.0, 500.0, 500.0, 500.0]
        events = [
            _event(f"event_{i}", dt.date(2025, 1 + i, 15), amount,
                   direction="credit", category="salary", description="Payroll credit")
            for i, amount in enumerate(amounts)
        ]
        series = _one(events, dt.date(2025, 6, 25))
        self.assertTrue(series.projects)
        self.assertAlmostEqual(series.amount, 750.0)
        self.assertLess(series.amount, max(amounts))


class TestAgainstTheRealDataset(unittest.TestCase):
    """The same seven verdicts, on the real rows rather than on fixtures."""

    @classmethod
    def setUpClass(cls):
        cls.data = loaders.load_dataset(DATASET)
        cls.rates = RateTable(cls.data.rates)

    def _series_for(self, user_id):
        request = next(r for r in self.data.samples if r.user_id == user_id)
        profile = self.data.profiles[user_id]
        events = self.data.events(user_id)
        amounts = {
            event.event_id: self.rates.convert_event_amount(event, profile.home_currency)
            for event in events
            if event.amount is not None
        }
        return {
            (s.direction, s.category, s.description_key): s
            for s in recurrence.detect(events, as_of=request.request_date, amounts=amounts)
        }

    def test_user_05_projects_no_income_at_all(self):
        series = self._series_for("user_05")
        income = [s for s in series.values() if s.is_income]
        self.assertTrue(income)
        self.assertTrue(all(not s.projects for s in income))

    def test_user_08_still_projects_its_variable_salary(self):
        series = self._series_for("user_08")
        salary = series[("credit", "salary", "payroll credit")]
        self.assertTrue(salary.projects)
        self.assertAlmostEqual(salary.amount, 1294.79, places=2)

    def test_user_11_projects_base_pay_and_neither_commission(self):
        series = self._series_for("user_11")
        self.assertTrue(series[("credit", "salary", "base salary")].projects)
        self.assertAlmostEqual(series[("credit", "salary", "base salary")].amount, 23256000.0)
        self.assertFalse(series[("credit", "salary", "performance commission")].projects)
        self.assertFalse(series[("credit", "salary", "monthly sales commission")].projects)

    def test_user_13_projects_the_primary_salary_and_not_the_stopped_one(self):
        series = self._series_for("user_13")
        self.assertTrue(series[("credit", "salary", "primary household salary")].projects)
        self.assertFalse(series[("credit", "salary", "second household income")].projects)

    def test_user_14_and_user_15_project_no_income_from_history_alone(self):
        for user_id in ("user_14", "user_15"):
            with self.subTest(user=user_id):
                income = [s for s in self._series_for(user_id).values() if s.is_income]
                self.assertTrue(all(not s.projects for s in income))

    def test_expense_recurrence_is_detected_for_every_sample_user(self):
        for request in self.data.samples:
            with self.subTest(request=request.request_id):
                projecting = [s for s in self._series_for(request.user_id).values() if s.projects]
                self.assertTrue(projecting, request.request_id)

    def test_detection_never_reads_an_event_after_the_request_date(self):
        """A scheduled row must reach the curve as an explicit event, not as history."""
        for request in self.data.samples:
            for series in self._series_for(request.user_id).values():
                self.assertLessEqual(series.last_date, request.request_date, series.key)


if __name__ == "__main__":
    unittest.main()
