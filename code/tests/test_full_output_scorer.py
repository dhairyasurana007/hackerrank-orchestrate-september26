"""The full-output scorer: per-field, never aggregated (TASKS.md M4)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from buyorwait import loaders, paths
from buyorwait.writer import OutputRow, placeholder_row, write_output
from evaluation import full_output as fo

DATASET = paths.find_dataset()


def _stub_prediction(request_id: str) -> dict:
    return placeholder_row(request_id).as_csv_dict()


class TestScoringAgainstTheSamples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = loaders.load_dataset(DATASET)
        cls.truth = cls.data.sample_truth

    def test_the_m0_stub_scores_near_zero_which_is_the_right_starting_point(self):
        predicted = {rid: _stub_prediction(rid) for rid in self.truth}
        report = fo.score(predicted, self.truth)
        self.assertEqual(report.scored, 25)
        self.assertEqual(report.exact_rows, 0)
        self.assertEqual(report.accuracy("amount_safe_to_pay"), 0.0)

    def test_perfect_predictions_score_every_field(self):
        predicted = {
            rid: {
                "request_id": rid,
                "amount_safe_to_pay": str(truth.amount_safe_to_pay),
                "affordability_status": truth.affordability_status,
                "recommended_payment_method": truth.recommended_payment_method,
                "payment_plan": truth.payment_plan,
                "earliest_date_for_full_payment": truth.earliest_date_for_full_payment,
                "spending_changes_needed": truth.spending_changes_needed,
                "decision_explanation": truth.decision_explanation,
            }
            for rid, truth in self.truth.items()
        }
        report = fo.score(predicted, self.truth)
        self.assertEqual(report.exact_rows, 25)
        for name in fo.SCORED_FIELDS:
            self.assertEqual(report.accuracy(name), 1.0, name)
        self.assertEqual(report.coupling_violations, [])

    def test_fields_are_scored_independently(self):
        """A gain in one field must not be able to hide a regression in another."""
        predicted = {}
        for rid, truth in self.truth.items():
            row = {
                "request_id": rid,
                "amount_safe_to_pay": str(truth.amount_safe_to_pay),
                "affordability_status": truth.affordability_status,
                "recommended_payment_method": truth.recommended_payment_method,
                "payment_plan": "none",  # deliberately wrong everywhere
                "earliest_date_for_full_payment": truth.earliest_date_for_full_payment,
                "spending_changes_needed": truth.spending_changes_needed,
            }
            predicted[rid] = row
        report = fo.score(predicted, self.truth)
        self.assertEqual(report.accuracy("amount_safe_to_pay"), 1.0)
        self.assertLess(report.accuracy("payment_plan"), 1.0)
        self.assertLess(report.exact_rows, 25)

    def test_a_missing_prediction_is_reported_not_silently_skipped(self):
        predicted = {rid: _stub_prediction(rid) for rid in list(self.truth)[:-1]}
        report = fo.score(predicted, self.truth)
        self.assertEqual(report.scored, 24)
        self.assertEqual(len(report.missing), 1)


class TestAmountComparison(unittest.TestCase):
    def test_amounts_compare_numerically_within_a_tight_tolerance(self):
        self.assertTrue(fo._matches("amount_safe_to_pay", "17229139.2", "17229139.2"))
        self.assertTrue(fo._matches("amount_safe_to_pay", "433.4", "433.400001"))
        self.assertFalse(fo._matches("amount_safe_to_pay", "433.4", "433.5"))

    def test_a_non_numeric_amount_is_a_mismatch_not_a_crash(self):
        self.assertFalse(fo._matches("amount_safe_to_pay", "", "433.4"))

    def test_blank_and_none_are_the_same_for_plan_and_changes(self):
        self.assertTrue(fo._matches("payment_plan", "", "none"))
        self.assertTrue(fo._matches("spending_changes_needed", "", "none"))
        self.assertFalse(fo._matches("earliest_date_for_full_payment", "", "2024-03-03"))


class TestCouplingCheck(unittest.TestCase):
    """The coupling must hold on the full 250-row output, where no ground truth exists."""

    def test_each_illegal_combination_is_caught(self):
        illegal = [
            ("affordable_now", "wait"),
            ("affordable_now", "installments"),
            ("affordable_later", "full_payment"),
            ("not_affordable", "partial_payment"),
            ("affordable_with_plan", "wait"),
            ("affordable_with_plan", "not_recommended"),
        ]
        for status, method in illegal:
            with self.subTest(status=status, method=method):
                violations = fo.check_coupling(
                    {"request_01": {"affordability_status": status, "recommended_payment_method": method}}
                )
                self.assertEqual(len(violations), 1)

    def test_each_legal_combination_passes(self):
        legal = [
            ("affordable_now", "full_payment"),
            ("affordable_later", "wait"),
            ("not_affordable", "not_recommended"),
            ("affordable_with_plan", "installments"),
            ("affordable_with_plan", "partial_payment"),
            ("affordable_with_plan", "full_payment"),
        ]
        for status, method in legal:
            with self.subTest(status=status, method=method):
                self.assertEqual(
                    fo.check_coupling(
                        {"r": {"affordability_status": status, "recommended_payment_method": method}}
                    ),
                    [],
                )

    def test_an_unknown_status_is_a_violation(self):
        self.assertEqual(
            len(fo.check_coupling({"r": {"affordability_status": "maybe", "recommended_payment_method": "wait"}})),
            1,
        )

    def test_the_25_solved_samples_satisfy_the_coupling(self):
        data = loaders.load_dataset(DATASET)
        rows = {
            rid: {
                "affordability_status": truth.affordability_status,
                "recommended_payment_method": truth.recommended_payment_method,
            }
            for rid, truth in data.sample_truth.items()
        }
        self.assertEqual(fo.check_coupling(rows), [])


class TestThresholds(unittest.TestCase):
    def test_the_threshold_file_round_trips(self):
        thresholds = fo.load_thresholds()
        self.assertIn("min_accuracy", thresholds)
        for name in fo.SCORED_FIELDS:
            self.assertIn(name, thresholds["min_accuracy"])

    def test_a_report_below_a_recorded_floor_fails(self):
        report = fo.FullOutputReport(scored=25, correct={"affordability_status": 10})
        failures = fo.check_thresholds(report, {"min_accuracy": {"affordability_status": 0.8}})
        self.assertEqual(len(failures), 1)
        self.assertIn("affordability_status", failures[0])

    def test_a_report_at_its_floor_passes(self):
        report = fo.FullOutputReport(scored=25, correct={"affordability_status": 20})
        self.assertEqual(fo.check_thresholds(report, {"min_accuracy": {"affordability_status": 0.8}}), [])

    def test_coupling_violations_always_fail_regardless_of_thresholds(self):
        report = fo.FullOutputReport(scored=1, coupling_violations=["r: bad"])
        self.assertTrue(fo.check_thresholds(report, {"min_accuracy": {}}))


class TestReadingAnEmittedFile(unittest.TestCase):
    def test_an_emitted_output_csv_reads_back_for_scoring(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.csv"
            write_output(path, [OutputRow(request_id="request_01", amount_safe_to_pay=25256)])
            rows = fo.read_output_csv(path)
        self.assertEqual(rows["request_01"]["amount_safe_to_pay"], "25256")

    def test_a_file_with_the_wrong_header_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wrong.csv"
            path.write_text("request_id,amount\nrequest_01,1\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                fo.read_output_csv(path)


if __name__ == "__main__":
    unittest.main()
