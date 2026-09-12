"""The usage report (TASKS.md M17).

Two things must hold: the figures reconcile with the run log, and no credential or message
text can reach the file.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from buyorwait.model.config import TEXT_MODEL, VISION_MODEL
from evaluation import usage


def _call(model=TEXT_MODEL, *, input_tokens=700, output_tokens=120, cache_hit=False,
          reported=0.00048, schema_valid=True, error=None):
    return {
        "record": "call",
        "purpose": "message-extraction:request_01",
        "request_hash": "a" * 64,
        "model": model.model_id,
        "cache_hit": cache_hit,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reported_cost_usd": reported,
        "estimated_cost_usd": model.cost(input_tokens, output_tokens),
        "latency_ms": 420.0,
        "schema_valid": schema_valid,
        "schema_problems": [],
        "error": error,
    }


class TestSummarisation(unittest.TestCase):
    def test_totals_reconcile_with_the_records(self):
        records = [_call(), _call(), _call(model=VISION_MODEL, input_tokens=2000, output_tokens=300)]
        summary = usage.summarise(records, requests=250)
        self.assertEqual(summary.calls, 3)
        self.assertEqual(summary.input_tokens, 700 + 700 + 2000)
        self.assertEqual(summary.output_tokens, 120 + 120 + 300)
        self.assertEqual(summary.total_tokens, summary.input_tokens + summary.output_tokens)
        self.assertAlmostEqual(
            summary.estimated_cost_usd,
            TEXT_MODEL.cost(700, 120) * 2 + VISION_MODEL.cost(2000, 300),
        )

    def test_per_model_totals_are_kept_separate(self):
        summary = usage.summarise([_call(), _call(model=VISION_MODEL)], requests=10)
        self.assertEqual(set(summary.by_model), {TEXT_MODEL.model_id, VISION_MODEL.model_id})
        self.assertEqual(summary.by_model[TEXT_MODEL.model_id].calls, 1)

    def test_a_cache_hit_counts_as_a_call_but_not_as_a_billed_one(self):
        summary = usage.summarise([_call(), _call(cache_hit=True)], requests=2)
        self.assertEqual(summary.calls, 2)
        self.assertEqual(summary.billed_calls, 1)
        self.assertEqual(summary.cache_hits, 1)
        self.assertAlmostEqual(summary.estimated_cost_usd, TEXT_MODEL.cost(700, 120))

    def test_averages_are_per_request_not_per_call(self):
        summary = usage.summarise([_call(), _call()], requests=250)
        self.assertAlmostEqual(summary.per_request(summary.total_tokens), (820 * 2) / 250)

    def test_schema_failures_and_errors_are_counted(self):
        summary = usage.summarise(
            [_call(schema_valid=False), _call(error="HTTP 500", schema_valid=False)], requests=2
        )
        totals = summary.by_model[TEXT_MODEL.model_id]
        self.assertEqual(totals.schema_failures, 2)
        self.assertEqual(totals.errors, 1)

    def test_dropped_amendments_are_tallied_by_reason(self):
        records = [
            {"record": "dropped", "reason": "confidence below the floor"},
            {"record": "dropped", "reason": "confidence below the floor"},
            {"record": "dropped", "reason": "unknown currency"},
        ]
        summary = usage.summarise(records, requests=1)
        self.assertEqual(summary.dropped["confidence below the floor"], 2)
        self.assertEqual(summary.dropped["unknown currency"], 1)

    def test_an_empty_run_summarises_to_zero_without_dividing_by_zero(self):
        summary = usage.summarise([], requests=0)
        self.assertEqual(summary.calls, 0)
        self.assertEqual(summary.per_request(summary.total_tokens), 0.0)


class TestReadingTheLog(unittest.TestCase):
    def test_a_jsonl_log_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            path.write_text("\n".join(json.dumps(_call()) for _ in range(3)), encoding="utf-8")
            self.assertEqual(len(usage.read_log(path)), 3)

    def test_a_truncated_final_line_is_skipped_rather_than_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            path.write_text(json.dumps(_call()) + "\n{partial", encoding="utf-8")
            self.assertEqual(len(usage.read_log(path)), 1)

    def test_a_missing_log_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(usage.read_log(Path(tmp) / "nope.jsonl"), [])


class TestTheRenderedReport(unittest.TestCase):
    def _render(self, records=None, requests=250, offline_reason=None):
        summary = usage.summarise(records if records is not None else [_call()] * 3, requests=requests)
        return usage.render(summary, offline_reason=offline_reason)

    def test_every_required_field_is_present(self):
        """AGENTS.md 6.5 lists these explicitly."""
        text = self._render()
        for required in (
            "OpenRouter",
            "openai/gpt-4.1-mini",
            "message extraction and explanation polish",
            "Model calls",
            "Input tokens",
            "Output tokens",
            "Total tokens",
            "Average tokens per request",
            "Estimated total cost",
            "Estimated cost per request",
        ):
            with self.subTest(field=required):
                self.assertIn(required, text)

    def test_the_per_model_table_carries_each_model_that_was_called(self):
        text = self._render([_call(), _call(model=VISION_MODEL)])
        self.assertIn(f"| `{TEXT_MODEL.model_id}` |", text)
        self.assertIn(f"| `{VISION_MODEL.model_id}` |", text)

    def test_the_offline_case_says_so_plainly_rather_than_reporting_a_gap(self):
        text = self._render([], offline_reason="the run used --no-llm")
        self.assertIn("No model calls were made", text)
        self.assertIn("--no-llm", text)
        self.assertIn("Model calls: **0**", text)

    def test_dropped_amendments_appear_with_their_reasons(self):
        text = self._render([_call(), {"record": "dropped", "reason": "unknown currency"}])
        self.assertIn("Amendments dropped", text)
        self.assertIn("unknown currency", text)

    def test_the_figures_in_the_report_match_the_summary(self):
        records = [_call(), _call(), _call(cache_hit=True)]
        summary = usage.summarise(records, requests=250)
        text = usage.render(summary)
        self.assertIn(f"Input tokens: **{summary.input_tokens:,}**", text)
        self.assertIn(f"Total tokens: **{summary.total_tokens:,}**", text)
        self.assertIn("Model calls: **3** (2 billed, 1 served", text)


class TestNoCredentialsReachTheReport(unittest.TestCase):
    def test_writing_refuses_a_report_containing_a_credential_marker(self):
        summary = usage.summarise([], requests=1)
        original = usage.render

        def leaky(*args, **kwargs):
            return original(*args, **kwargs) + "\nAuthorization: Bearer sk-or-v1-secret\n"

        with tempfile.TemporaryDirectory() as tmp:
            usage.render = leaky
            try:
                with self.assertRaises(AssertionError):
                    usage.write_report(summary, path=Path(tmp) / "report.md")
            finally:
                usage.render = original

    def test_the_generated_report_contains_no_credential_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = usage.write_report(
                usage.summarise([_call()], requests=250), path=Path(tmp) / "report.md"
            )
            text = path.read_text(encoding="utf-8")
        for forbidden in usage.FORBIDDEN_SUBSTRINGS:
            self.assertNotIn(forbidden, text)

    def test_the_committed_report_contains_no_credential_marker(self):
        text = usage.REPORT_PATH.read_text(encoding="utf-8")
        for forbidden in usage.FORBIDDEN_SUBSTRINGS:
            self.assertNotIn(forbidden, text)

    def test_the_report_lives_where_the_archive_needs_it(self):
        """code.zip is the code/ directory at its own root, so this must be evaluation/."""
        self.assertEqual(usage.REPORT_PATH.name, "usage_report.md")
        self.assertEqual(usage.REPORT_PATH.parent.name, "evaluation")
        self.assertEqual(usage.REPORT_PATH.parent.parent.name, "code")


if __name__ == "__main__":
    unittest.main()
