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


if __name__ == "__main__":
    unittest.main()
