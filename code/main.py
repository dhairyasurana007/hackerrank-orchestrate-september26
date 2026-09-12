#!/usr/bin/env python3
"""Buy or Wait? — command-line entry point.

    python code/main.py --no-llm

Runs from the repository root, from inside ``code/``, or from an extracted ``code.zip``
sitting beside a ``dataset/`` directory; see ``buyorwait.paths``.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

if __package__ in (None, ""):  # invoked as a script: make `buyorwait` importable
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from buyorwait import paths  # noqa: E402
from buyorwait.writer import OutputRow, write_output  # noqa: E402


def placeholder_row(request_id: str) -> OutputRow:
    """M0 scaffolding: a contract-valid row with no engine behind it yet.

    Deliberately *not* ``writer.fallback_row`` — that one marks the failure-isolation path
    (M13), which CI later asserts is never taken. Keeping the two distinct is what makes
    that assertion meaningful.
    """
    return OutputRow(
        request_id=request_id,
        decision_explanation="No recommendation computed yet (scaffolding).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="buy-or-wait", description=__doc__)
    parser.add_argument("--dataset", default=None, help="path to the dataset/ directory")
    parser.add_argument("--output", default=None, help="path of the output.csv to write")
    parser.add_argument(
        "--no-llm", action="store_true", help="skip every model call; evidence degrades to no amendment"
    )
    parser.add_argument(
        "--requests", default=None, help="comma-separated request_ids to restrict the run to"
    )
    parser.add_argument(
        "--samples", action="store_true", help="score against sample_requests.csv instead of predicting"
    )
    parser.add_argument("--no-cache", action="store_true", help="bypass the model response cache")
    return parser


def read_request_ids(dataset: Path, samples: bool) -> list[str]:
    name = "sample_requests.csv" if samples else "requests.csv"
    with (dataset / name).open(newline="", encoding="utf-8") as handle:
        return [row["request_id"] for row in csv.DictReader(handle)]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dataset = paths.find_dataset(args.dataset)
    output = Path(args.output) if args.output else paths.default_output(dataset)

    request_ids = read_request_ids(dataset, args.samples)
    if args.requests:
        wanted = {rid.strip() for rid in args.requests.split(",") if rid.strip()}
        request_ids = [rid for rid in request_ids if rid in wanted]

    rows = [placeholder_row(rid) for rid in request_ids]

    if args.samples:
        # --samples prints scores; it never writes predictions, so a scoring run can never
        # leave a 25-row output.csv behind where a 250-row one is expected.
        print(f"scored {len(rows)} samples (no scorer wired yet)")
        return 0

    written = write_output(output, rows)
    print(f"wrote {written} rows to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
