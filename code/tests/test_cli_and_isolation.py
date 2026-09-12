"""CLI surface and per-request failure isolation (TASKS.md M13, PLAN.md 5.8).

The contract demands exactly one row per request, so one malformed record must never cost
the run. Without the boundary, a bad request at minute 40 of a full run yields no output.csv
at all — which is why the fallback path is tested by *rigging* a failure rather than by
hoping one never happens.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from buyorwait import loaders, paths
from buyorwait.contract import OUTPUT_COLUMNS
from buyorwait.pipeline import Engine, run
from buyorwait.validator import validate_rows

DATASET = paths.find_dataset()
MAIN = paths.code_dir() / "main.py"


class _RiggedEngine(Engine):
    """An engine that raises on one nominated request and behaves normally otherwise."""

    def __init__(self, *args, doomed=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.doomed = set(doomed)

    def decide(self, request):
        if request.request_id in self.doomed:
            raise ZeroDivisionError("rigged failure")
        return super().decide(request)


class TestFailureIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = loaders.load_dataset(DATASET)

    def _rigged(self, doomed):
        base = Engine.build(self.data, use_llm=False)
        return _RiggedEngine(
            data=base.data, rates=base.rates, use_llm=False, doomed=doomed
        )

    def test_a_rigged_request_still_yields_a_contract_valid_fallback_row(self):
        engine = self._rigged({"request_26"})
        report = run(engine, self.data.requests)
        self.assertEqual(len(report.rows), len(self.data.requests))
        row = next(r for r in report.rows if r.request_id == "request_26")
        self.assertTrue(row.from_fallback)
        self.assertEqual(
            (row.affordability_status, row.recommended_payment_method),
            ("not_affordable", "not_recommended"),
        )
        self.assertEqual(row.amount_safe_to_pay, 0.0)
        self.assertEqual(row.payment_plan, ())
        self.assertIsNone(row.earliest_date_for_full_payment)
        self.assertEqual(row.spending_changes_needed, ())

    def test_the_fallback_row_passes_the_validator(self):
        engine = self._rigged({"request_26"})
        report = run(engine, self.data.requests)
        rows = {r.request_id: r.as_csv_dict() for r in report.rows}
        self.assertEqual([str(v) for v in validate_rows(rows, self.data)], [])

    def test_the_run_completes_with_every_row_present(self):
        engine = self._rigged({r.request_id for r in self.data.requests[:5]})
        report = run(engine, self.data.requests)
        self.assertEqual(len(report.rows), 250)
        self.assertEqual(report.fallback_rows, 5)

    def test_the_failure_is_recorded_with_its_traceback(self):
        engine = self._rigged({"request_27"})
        report = run(engine, self.data.requests)
        self.assertEqual(len(report.failures), 1)
        failure = report.failures[0]
        self.assertEqual(failure["request_id"], "request_27")
        self.assertIn("ZeroDivisionError", failure["error"])
        self.assertIn("rigged failure", failure["traceback"])
        self.assertIn("Traceback", failure["traceback"])

    def test_a_failure_does_not_stop_later_requests(self):
        engine = self._rigged({self.data.requests[0].request_id})
        report = run(engine, self.data.requests)
        later = [r for r in report.rows[1:] if not r.from_fallback]
        self.assertEqual(len(later), 249)

    def test_the_run_summary_records_the_count_and_the_ids(self):
        engine = self._rigged({"request_26"})
        report = run(engine, self.data.requests)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.csv"
            summary_path = report.write_summary(path)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(summary["rows"], 250)
        self.assertEqual(summary["fallback_rows"], 1)
        self.assertEqual(summary["fallback_request_ids"], ["request_26"])

    def test_a_clean_run_records_zero_fallback_rows(self):
        report = run(Engine.build(self.data, use_llm=False), self.data.requests)
        self.assertEqual(report.fallback_rows, 0)
        self.assertEqual(report.failures, [])


class TestTheCliSurface(unittest.TestCase):
    """Every flag PLAN.md 5.8 lists, exercised in-process so failures are readable."""

    def _run(self, argv):
        sys.path.insert(0, str(paths.code_dir()))
        import main as cli  # noqa: PLC0415 - the module under test

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_no_llm_writes_every_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.csv"
            code, out, _ = self._run(["--no-llm", "--output", str(path)])
            self.assertEqual(code, 0)
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.reader(handle)
                self.assertEqual(tuple(next(reader)), OUTPUT_COLUMNS)
                self.assertEqual(len([row for row in reader if row]), 250)
        self.assertIn("wrote 250 rows", out)

    def test_requests_restricts_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.csv"
            code, _, _ = self._run(
                ["--no-llm", "--requests", "request_26,request_27", "--output", str(path)]
            )
            self.assertEqual(code, 0)
            with path.open(newline="", encoding="utf-8") as handle:
                ids = [row["request_id"] for row in csv.DictReader(handle)]
        self.assertEqual(ids, ["request_26", "request_27"])

    def test_an_unknown_request_id_is_an_error_not_a_short_run(self):
        code, _, err = self._run(["--no-llm", "--requests", "request_26,request_9999"])
        self.assertEqual(code, 2)
        self.assertIn("request_9999", err)

    def test_a_bad_dataset_path_reports_cleanly_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = self._run(["--no-llm", "--dataset", tmp])
        self.assertEqual(code, 2)
        self.assertIn("not a dataset directory", err)

    def test_samples_prints_both_scorers_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.csv"
            code, out, _ = self._run(["--no-llm", "--samples", "--output", str(path)])
        self.assertEqual(code, 0)
        self.assertIn("drawdown harness", out)
        self.assertIn("full-output scorer", out)
        self.assertFalse(path.exists())

    def test_a_run_writes_the_summary_beside_its_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.csv"
            self._run(["--no-llm", "--output", str(path)])
            summary = Path(str(path) + ".run.json")
            self.assertTrue(summary.is_file())
            self.assertEqual(json.loads(summary.read_text(encoding="utf-8"))["fallback_rows"], 0)


class TestPathResolution(unittest.TestCase):
    """Asserted from three working directories, because packaging must not break the run."""

    def _subprocess_run(self, cwd, script_path, extra=()):
        with tempfile.TemporaryDirectory() as out:
            target = Path(out) / "output.csv"
            result = subprocess.run(
                [sys.executable, str(script_path), "--no-llm", "--output", str(target), *extra],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                check=False,
            )
            rows = None
            if target.is_file():
                with target.open(newline="", encoding="utf-8") as handle:
                    rows = len(list(csv.DictReader(handle)))
            return result, rows

    def test_it_runs_from_the_repository_root(self):
        result, rows = self._subprocess_run(paths.code_dir().parent, MAIN)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(rows, 250)

    def test_it_runs_from_inside_code(self):
        result, rows = self._subprocess_run(paths.code_dir(), "main.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(rows, 250)

    def test_it_runs_from_an_unrelated_working_directory(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            result, rows = self._subprocess_run(elsewhere, MAIN)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(rows, 250)

    def test_it_runs_from_an_extracted_archive_layout(self):
        """`code.zip` unpacked at its own root beside a `dataset/` copy — how it ships."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(
                paths.code_dir(),
                root / "extracted",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            (root / "dataset").mkdir()
            for name in paths.DATASET_FILES:
                shutil.copyfile(DATASET / name, root / "dataset" / name)
            result, rows = self._subprocess_run(root, root / "extracted" / "main.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(rows, 250)


if __name__ == "__main__":
    unittest.main()
