"""The drawdown harness (TASKS.md M5b)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from buyorwait import loaders, paths
from evaluation import drawdown
from evaluation.drawdown_targets import targets_from

DATASET = paths.find_dataset()


class TestGradingAStubForecaster(unittest.TestCase):
    """The gate: a stub that predicts zero must produce a complete report, not a crash."""

    @classmethod
    def setUpClass(cls):
        cls.data = loaders.load_dataset(DATASET)
        cls.report = drawdown.grade(drawdown.zero_forecaster, cls.data)

    def test_every_sample_is_graded_and_the_sets_stay_separate(self):
        self.assertEqual(len(self.report.results), 25)
        self.assertEqual(len(self.report.exact), 21)
        self.assertEqual(len(self.report.capped), 4)

    def test_predicting_no_drawdown_is_a_hundred_percent_error_on_every_exact_sample(self):
        self.assertAlmostEqual(self.report.median_error, 1.0)
        self.assertAlmostEqual(self.report.mean_error, 1.0)
        self.assertEqual(self.report.within(0.25), 0)

    def test_a_zero_prediction_violates_no_upper_bound(self):
        self.assertEqual(self.report.bound_violations, [])

    def test_the_report_renders_with_and_without_per_sample_detail(self):
        summary = self.report.render()
        self.assertIn("median relative error", summary)
        self.assertNotIn("request_01", summary)
        detail = self.report.render(detail=True)
        for target in targets_from(self.data):
            self.assertIn(target.request_id, detail)

    def test_capped_samples_carry_no_relative_error(self):
        for result in self.report.capped:
            self.assertIsNone(result.error)
        for result in self.report.exact:
            self.assertIsNotNone(result.error)


class TestGradingAPerfectForecaster(unittest.TestCase):
    def setUp(self):
        self.data = loaders.load_dataset(DATASET)
        self.targets = {t.request_id: t for t in targets_from(self.data)}

    def test_predicting_the_target_exactly_scores_zero_error(self):
        report = drawdown.grade(lambda r: self.targets[r.request_id].target_drawdown, self.data)
        self.assertAlmostEqual(report.median_error, 0.0)
        self.assertEqual(report.within(0.10), 21)
        self.assertEqual(report.bound_violations, [])

    def test_over_predicting_a_capped_sample_is_a_bound_violation(self):
        def forecaster(request):
            target = self.targets[request.request_id]
            return target.target_drawdown * (2.0 if not target.is_exact else 1.0)

        report = drawdown.grade(forecaster, self.data)
        self.assertEqual(
            sorted(r.request_id for r in report.bound_violations),
            ["request_01", "request_09", "request_12", "request_16"],
        )

    def test_a_deeper_prediction_on_an_exact_sample_shows_as_positive_error(self):
        report = drawdown.grade(lambda r: self.targets[r.request_id].target_drawdown * 1.5, self.data)
        self.assertAlmostEqual(report.median_error, 0.5)
        self.assertTrue(all(result.error > 0 for result in report.exact))


class TestThresholds(unittest.TestCase):
    def test_the_recorded_threshold_file_round_trips(self):
        original = drawdown.load_thresholds()
        self.assertIn("max_median_relative_error", original)
        self.assertIn("max_bound_violations", original)
        self.assertIn("history", original)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "drawdown.json"
            saved = drawdown.THRESHOLDS_PATH
            try:
                drawdown.THRESHOLDS_PATH = path
                drawdown.save_thresholds(original)
                self.assertEqual(drawdown.load_thresholds(), original)
            finally:
                drawdown.THRESHOLDS_PATH = saved

    def test_a_worsened_median_fails(self):
        report = drawdown.DrawdownReport()
        data = loaders.load_dataset(DATASET)
        report = drawdown.grade(drawdown.zero_forecaster, data)
        self.assertEqual(drawdown.check_thresholds(report, {"max_median_relative_error": 1.0}), [])
        failures = drawdown.check_thresholds(report, {"max_median_relative_error": 0.5})
        self.assertEqual(len(failures), 1)
        self.assertIn("median relative error", failures[0])

    def test_a_bound_violation_above_the_allowance_fails(self):
        data = loaders.load_dataset(DATASET)
        targets = {t.request_id: t for t in targets_from(data)}
        report = drawdown.grade(lambda r: targets[r.request_id].target_drawdown * 3.0, data)
        failures = drawdown.check_thresholds(report, {"max_bound_violations": 0})
        self.assertTrue(any("bound violation" in f for f in failures))

    def test_the_current_forecaster_meets_the_committed_threshold(self):
        """This is the assertion CI's scorers job enforces on every push."""
        from buyorwait.pipeline import Engine

        data = loaders.load_dataset(DATASET)
        engine = Engine.build(data, use_llm=False)
        report = drawdown.grade(engine.predicted_drawdown, data)
        self.assertEqual(drawdown.check_thresholds(report), [])


if __name__ == "__main__":
    unittest.main()
