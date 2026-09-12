#!/usr/bin/env python3
"""Buy or Wait? — command-line entry point.

    python code/main.py --no-llm

Runs from the repository root, from inside ``code/``, or from an extracted ``code.zip``
sitting beside a ``dataset/`` directory; see ``buyorwait.paths``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):  # invoked as a script: make `buyorwait` importable
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from buyorwait import paths  # noqa: E402
from buyorwait.loaders import load_dataset  # noqa: E402
from buyorwait.records import DatasetError  # noqa: E402
from buyorwait.pipeline import Engine, run  # noqa: E402
from buyorwait.writer import write_output  # noqa: E402
from evaluation import drawdown, full_output, usage  # noqa: E402


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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        dataset = paths.find_dataset(args.dataset)
    except (FileNotFoundError, DatasetError) as error:
        # A bad --dataset is a usage error, not a crash: report it in one line and stop.
        print(f"error: {error}", file=sys.stderr)
        return 2
    output = Path(args.output) if args.output else paths.default_output(dataset)

    try:
        data = load_dataset(dataset)
    except DatasetError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    requests = data.samples if args.samples else data.requests
    if args.requests:
        wanted = [rid.strip() for rid in args.requests.split(",") if rid.strip()]
        known = {r.request_id for r in requests}
        missing = [rid for rid in wanted if rid not in known]
        if missing:
            # Silently emitting fewer rows would look like a successful short run.
            print(f"error: unknown request id(s): {', '.join(missing)}", file=sys.stderr)
            return 2
        requests = tuple(r for r in requests if r.request_id in set(wanted))

    engine = Engine.build(data, use_llm=not args.no_llm)
    report = run(engine, requests)

    if args.samples:
        # --samples prints scores; it never writes predictions, so a scoring run can never
        # leave a 25-row output.csv behind where a 250-row one is expected.
        return score_samples(engine, report, data, detail=bool(args.requests))

    written = write_output(output, report.rows)
    report.write_summary(output)
    write_usage_report(engine, len(requests), offline=args.no_llm)
    print(f"wrote {written} rows to {output}")
    if report.fallback_rows:
        print(f"WARNING: {report.fallback_rows} row(s) came from the failure-isolation fallback")
        for failure in report.failures[:5]:
            print(f"  {failure['request_id']}: {failure['error']}")
    return 0


def write_usage_report(engine, request_count: int, *, offline: bool) -> None:
    """Regenerate evaluation/usage_report.md from this run's model calls."""
    records = list(engine.client.records) if engine.client is not None else []
    if engine.client is not None:
        engine.client.flush_run_log()
    reason = None
    if offline:
        reason = "the run used --no-llm"
    elif engine.client is None or not engine.client.available:
        reason = "OPENROUTER_API_KEY is not set, so the model layer degraded to the offline path"
    summary = usage.summarise(records, requests=request_count)
    path = usage.write_report(summary, offline_reason=reason)
    print(f"wrote the usage report to {path}")


def score_samples(engine, report, data, detail: bool = False) -> int:
    """Print both scorers. Recorded thresholds decide the exit code.

    Two scorers, never one number: the forecast curve can be exact while the emitted row is
    wrong, and vice versa.
    """
    curve_report = drawdown.grade(engine.predicted_drawdown, data)
    print(curve_report.render(detail=detail))
    curve_failures = [f"drawdown {failure}" for failure in drawdown.check_thresholds(curve_report)]

    predicted = {row.request_id: row.as_csv_dict() for row in report.rows}
    full = full_output.score(predicted, data.sample_truth)
    print(full.render())
    full_failures = [f"full-output {failure}" for failure in full_output.check_thresholds(full)]

    for failure in curve_failures + full_failures:
        print(f"FAIL: {failure}")
    return 1 if curve_failures + full_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
