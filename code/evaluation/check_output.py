#!/usr/bin/env python3
"""CI assertion over a produced ``output.csv`` (TASKS.md 2.3).

Assertions are staged: each is enabled by the task that makes it satisfiable, via a flag,
so the workflow never checks something that cannot exist yet.

    python code/evaluation/check_output.py output.csv --rows 250

The row count is asserted by *parsing* the CSV rather than counting lines:
``decision_explanation`` is quoted free text, and a newline inside a quoted field would
break a line count while leaving the file perfectly valid.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buyorwait.contract import OUTPUT_COLUMNS  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", help="path to the output.csv to check")
    parser.add_argument("--rows", type=int, required=True, help="exact number of data rows expected")
    parser.add_argument(
        "--validate", action="store_true", help="assert zero contract violations (enabled at M11)"
    )
    parser.add_argument(
        "--dataset", default=None, help="dataset directory, required by --validate"
    )
    parser.add_argument(
        "--no-fallback-rows",
        action="store_true",
        help="assert no row came from the failure-isolation path (enabled at M13)",
    )
    args = parser.parse_args(argv)

    path = Path(args.output)
    failures: list[str] = []

    if not path.is_file():
        print(f"FAIL: {path} does not exist")
        return 1

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            print(f"FAIL: {path} is empty")
            return 1
        rows = [row for row in reader if row]

    if tuple(header) != OUTPUT_COLUMNS:
        failures.append(f"header mismatch\n  expected: {OUTPUT_COLUMNS}\n  actual:   {tuple(header)}")
    if len(rows) != args.rows:
        failures.append(f"expected {args.rows} data rows, parsed {len(rows)}")
    widths = {len(row) for row in rows}
    if widths - {len(OUTPUT_COLUMNS)}:
        failures.append(f"rows with the wrong field count: widths seen {sorted(widths)}")

    if args.validate:
        failures.extend(_validate(path, args.dataset))

    if args.no_fallback_rows:
        failures.extend(_check_no_fallback(path))

    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        return 1
    print(f"OK: {path} — {len(rows)} rows, header matches the contract")
    return 0


def _validate(path: Path, dataset: str | None) -> list[str]:
    from buyorwait import paths
    from buyorwait.loaders import load_dataset
    from buyorwait.validator import validate_output_file

    data = load_dataset(paths.find_dataset(dataset))
    violations = validate_output_file(path, data)
    return [f"{v.request_id}: {v.message}" for v in violations]


def _check_no_fallback(path: Path) -> list[str]:
    """Read the run summary the pipeline writes beside its output (M13).

    The count comes from the run rather than from the CSV: a fallback row is defined by the
    code path that produced it, and that is not recoverable by pattern-matching the text.
    """
    import json

    summary_path = path.with_suffix(path.suffix + ".run.json")
    if not summary_path.is_file():
        return [f"no run summary at {summary_path}; cannot assert the fallback count"]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    count = summary.get("fallback_rows")
    if count is None:
        return [f"{summary_path} does not record fallback_rows"]
    if count:
        ids = summary.get("fallback_request_ids", [])[:10]
        return [f"{count} row(s) came from the failure-isolation fallback: {ids}"]
    return []


if __name__ == "__main__":
    raise SystemExit(main())
