"""Output formatting (TASKS.md M12).

The two amount fields are formatted by different rules in the same row; every literal
asserted here is lifted from ``dataset/sample_requests.csv``.
"""

from __future__ import annotations

import csv
import datetime as dt
import tempfile
import unittest
from pathlib import Path

from buyorwait.contract import OUTPUT_COLUMNS
from buyorwait.writer import (
    OutputRow,
    fallback_row,
    format_plan_amount,
    format_safe_amount,
    write_output,
)


class TestPlanAmountFormatting(unittest.TestCase):
    def test_fractional_amounts_render_at_exactly_two_places(self):
        self.assertEqual(format_plan_amount(620.4), "620.40")
        self.assertEqual(format_plan_amount(3246.1), "3246.10")
        self.assertEqual(format_plan_amount(996.6), "996.60")
        self.assertEqual(format_plan_amount(15952906.67), "15952906.67")

    def test_whole_amounts_carry_no_decimal_part(self):
        self.assertEqual(format_plan_amount(38016), "38016")
        self.assertEqual(format_plan_amount(13110000), "13110000")
        self.assertEqual(format_plan_amount(25256.0), "25256")


class TestSafeAmountFormatting(unittest.TestCase):
    def test_amount_safe_to_pay_is_not_padded_to_two_places(self):
        self.assertEqual(format_safe_amount(433.4), "433.4")
        self.assertEqual(format_safe_amount(603.3), "603.3")
        self.assertEqual(format_safe_amount(17229139.2), "17229139.2")

    def test_whole_safe_amounts_render_as_integers(self):
        self.assertEqual(format_safe_amount(0), "0")
        self.assertEqual(format_safe_amount(25256.0), "25256")

    def test_float_noise_does_not_leak_into_the_field(self):
        self.assertEqual(format_safe_amount(0.1 + 0.2), "0.3")


class TestRowRendering(unittest.TestCase):
    def test_payment_plan_joins_with_a_pipe_in_order(self):
        row = OutputRow(
            request_id="request_02",
            payment_plan=(
                (dt.date(2025, 8, 8), 15952906.67),
                (dt.date(2025, 9, 7), 15952906.67),
                (dt.date(2025, 10, 7), 15952906.67),
            ),
        )
        self.assertEqual(
            row.payment_plan_text(),
            "2025-08-08:15952906.67|2025-09-07:15952906.67|2025-10-07:15952906.67",
        )

    def test_an_empty_plan_renders_none_and_a_missing_date_renders_empty(self):
        row = OutputRow(request_id="request_99")
        self.assertEqual(row.payment_plan_text(), "none")
        self.assertEqual(row.as_csv_dict()["earliest_date_for_full_payment"], "")

    def test_spending_changes_render_none_when_absent_and_pipe_joined_when_present(self):
        self.assertEqual(OutputRow(request_id="r").spending_changes_text(), "none")
        row = OutputRow(request_id="r", spending_changes_needed=("stop:event_12", "reduce_to:event_13:50"))
        self.assertEqual(row.spending_changes_text(), "stop:event_12|reduce_to:event_13:50")

    def test_fallback_row_is_marked_and_placeholder_rows_are_not(self):
        self.assertTrue(fallback_row("request_01").from_fallback)
        self.assertFalse(OutputRow(request_id="request_01").from_fallback)


class TestCsvEmission(unittest.TestCase):
    def test_column_order_is_exactly_the_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.csv"
            written = write_output(path, [OutputRow(request_id="request_26")])
            self.assertEqual(written, 1)
            with path.open(newline="", encoding="utf-8") as handle:
                header = next(csv.reader(handle))
            self.assertEqual(tuple(header), OUTPUT_COLUMNS)

    def test_a_newline_inside_an_explanation_round_trips(self):
        """Quoted free text must not be able to break a parse of the emitted file."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.csv"
            write_output(path, [OutputRow(request_id="r", decision_explanation="a\nb, and \"c\"")])
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["decision_explanation"], 'a\nb, and "c"')


class TestFormattersReproduceGroundTruthExactly(unittest.TestCase):
    """Every emitted figure in the 25 solved rows must round-trip byte for byte (M12).

    Stronger than the literal-by-literal tests above: it takes every amount that actually
    appears in `sample_requests.csv` and asserts our formatter reproduces its exact text.
    Matching ground truth on exact comparison depends on this, and the two amount fields
    follow different rules in the same row.
    """

    @classmethod
    def setUpClass(cls):
        from buyorwait import loaders, paths

        cls.dataset = paths.find_dataset()
        cls.data = loaders.load_dataset(cls.dataset)

    def test_amount_safe_to_pay_round_trips_for_all_25(self):
        with (self.dataset / "sample_requests.csv").open(newline="", encoding="utf-8") as handle:
            raw = {row["request_id"]: row["amount_safe_to_pay"] for row in csv.DictReader(handle)}
        self.assertEqual(len(raw), 25)
        for request_id, text in raw.items():
            with self.subTest(request=request_id):
                self.assertEqual(format_safe_amount(float(text)), text)

    def test_every_payment_plan_amount_round_trips(self):
        checked = 0
        for request_id, truth in self.data.sample_truth.items():
            if truth.payment_plan == "none":
                continue
            for part in truth.payment_plan.split("|"):
                _, amount = part.split(":")
                with self.subTest(request=request_id, amount=amount):
                    self.assertEqual(format_plan_amount(float(amount)), amount)
                checked += 1
        self.assertGreater(checked, 20)

    def test_every_reduce_to_amount_round_trips(self):
        """reduce_to amounts follow the plan-amount rule: `23.50` but `665950`."""
        amounts = []
        for truth in self.data.sample_truth.values():
            if truth.spending_changes_needed == "none":
                continue
            for action in truth.spending_changes_needed.split("|"):
                if action.startswith("reduce_to:"):
                    amounts.append(action.split(":")[2])
        self.assertEqual(sorted(amounts), ["23.50", "665950"])
        for amount in amounts:
            self.assertEqual(format_plan_amount(float(amount)), amount)

    def test_the_wait_branch_emits_one_full_payment_and_never_none(self):
        for request_id, truth in self.data.sample_truth.items():
            if truth.recommended_payment_method != "wait":
                continue
            with self.subTest(request=request_id):
                self.assertNotEqual(truth.payment_plan, "none")
                entries = truth.payment_plan.split("|")
                self.assertEqual(len(entries), 1)
                when, amount = entries[0].split(":")
                request = self.data.request(request_id)
                self.assertEqual(when, truth.earliest_date_for_full_payment)
                self.assertEqual(format_plan_amount(request.requested_amount), amount)

    def test_our_own_emitted_rows_use_the_two_distinct_amount_formats(self):
        from buyorwait.pipeline import Engine

        engine = Engine.build(self.data, use_llm=False)
        two_dp = 0
        for request in self.data.requests:
            row = engine.decide(request).as_csv_dict()
            for part in row["payment_plan"].split("|"):
                if part == "none":
                    continue
                amount = part.split(":")[1]
                if "." in amount:
                    self.assertEqual(len(amount.split(".")[1]), 2, row["payment_plan"])
                    two_dp += 1
            safe = row["amount_safe_to_pay"]
            if "." in safe:
                # Never padded, so a trailing zero would mean the plan rule leaked across.
                self.assertFalse(safe.endswith("0"), safe)
        self.assertGreater(two_dp, 0)


if __name__ == "__main__":
    unittest.main()
