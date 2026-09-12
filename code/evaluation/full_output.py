"""Full-output scorer: predicted rows against the 25 solved samples (TASKS.md M4).

Scored **per field, not as one aggregate**. The forecast can be exact while the emitted row
is wrong and vice versa, and within a row a regression in one field must not be able to hide
behind a gain in another.

This scorer owns ``thresholds/full_output.json``, which is a *different* file from the
drawdown threshold owned by M5b: the two measure different things — emitted-row correctness
versus forecast-curve accuracy — and must never be merged into one number.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from buyorwait.contract import OUTPUT_COLUMNS, STATUS_METHODS

#: The six predicted fields with ground truth. ``decision_explanation`` is free text and is
#: graded on usefulness rather than on string equality, so it is not scored here.
SCORED_FIELDS = (
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
)

#: Absolute tolerance on ``amount_safe_to_pay``. Rendering is asserted separately (M12);
#: this scorer is asking whether the engine computed the right number.
AMOUNT_TOLERANCE = 0.005

THRESHOLDS_PATH = Path(__file__).resolve().parent / "thresholds" / "full_output.json"


@dataclass
class Mismatch:
    request_id: str
    field_name: str
    predicted: str
    expected: str


@dataclass
class FullOutputReport:
    scored: int = 0
    correct: dict[str, int] = field(default_factory=dict)
    mismatches: list[Mismatch] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    coupling_violations: list[str] = field(default_factory=list)

    def accuracy(self, field_name: str) -> float:
        if not self.scored:
            return 0.0
        return self.correct.get(field_name, 0) / self.scored

    @property
    def exact_rows(self) -> int:
        """Rows where all six scored fields match — the strictest single figure."""
        wrong = {m.request_id for m in self.mismatches}
        return self.scored - len(wrong)

    def as_dict(self) -> dict:
        return {
            "scored": self.scored,
            "exact_rows": self.exact_rows,
            "accuracy": {name: round(self.accuracy(name), 4) for name in SCORED_FIELDS},
        }

    def render(self) -> str:
        lines = [f"full-output scorer: {self.scored} samples scored"]
        if self.missing:
            lines.append(f"  MISSING predictions for {len(self.missing)}: {', '.join(self.missing[:8])}")
        for name in SCORED_FIELDS:
            hits = self.correct.get(name, 0)
            lines.append(f"  {name:<34} {hits:>3}/{self.scored}  {self.accuracy(name):6.1%}")
        lines.append(f"  {'all six fields exact':<34} {self.exact_rows:>3}/{self.scored}")
        if self.coupling_violations:
            lines.append(f"  COUPLING violations: {len(self.coupling_violations)}")
            for violation in self.coupling_violations[:8]:
                lines.append(f"    {violation}")
        return "\n".join(lines)


def _normalise(field_name: str, value: str) -> str:
    value = (value or "").strip()
    if field_name == "payment_plan" and value == "":
        return "none"
    if field_name == "spending_changes_needed" and value == "":
        return "none"
    return value


def _matches(field_name: str, predicted: str, expected: str) -> bool:
    predicted = _normalise(field_name, predicted)
    expected = _normalise(field_name, expected)
    if field_name == "amount_safe_to_pay":
        try:
            return abs(float(predicted) - float(expected)) <= AMOUNT_TOLERANCE
        except ValueError:
            return False
    return predicted == expected


def check_coupling(rows: dict[str, dict]) -> list[str]:
    """The status/method coupling of PLAN.md 2, asserted wherever rows exist.

    It holds on the full 250-row output too, where there is no ground truth to compare
    against but the coupling must still be true.
    """
    violations = []
    for request_id, row in sorted(rows.items()):
        status = (row.get("affordability_status") or "").strip()
        method = (row.get("recommended_payment_method") or "").strip()
        permitted = STATUS_METHODS.get(status)
        if permitted is None:
            violations.append(f"{request_id}: unknown affordability_status {status!r}")
        elif method not in permitted:
            violations.append(f"{request_id}: {status} may not recommend {method!r}")
    return violations


def score(predicted: dict[str, dict], truth: dict) -> FullOutputReport:
    """Score predicted rows (``request_id`` -> field dict) against sample ground truth."""
    report = FullOutputReport()
    report.coupling_violations = check_coupling(predicted)
    for request_id in sorted(truth):
        expected = truth[request_id]
        row = predicted.get(request_id)
        if row is None:
            report.missing.append(request_id)
            continue
        report.scored += 1
        for name in SCORED_FIELDS:
            got = str(row.get(name, ""))
            want = str(getattr(expected, name))
            if _matches(name, got, want):
                report.correct[name] = report.correct.get(name, 0) + 1
            else:
                report.mismatches.append(Mismatch(request_id, name, got, want))
    return report


def read_output_csv(path: str | Path) -> dict[str, dict]:
    """Read an emitted ``output.csv`` into ``request_id`` -> field dict."""
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != OUTPUT_COLUMNS:
            raise ValueError(f"{path}: header is not the output contract")
        return {row["request_id"]: row for row in reader}


def load_thresholds() -> dict:
    if not THRESHOLDS_PATH.is_file():
        return {"min_accuracy": {}, "min_exact_rows": 0}
    return json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))


def save_thresholds(thresholds: dict) -> None:
    THRESHOLDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    THRESHOLDS_PATH.write_text(json.dumps(thresholds, indent=2) + "\n", encoding="utf-8")


def check_thresholds(report: FullOutputReport, thresholds: dict | None = None) -> list[str]:
    """Recorded thresholds ratchet down as accuracy improves, never up for a regression."""
    thresholds = load_thresholds() if thresholds is None else thresholds
    failures = []
    for name, floor in (thresholds.get("min_accuracy") or {}).items():
        actual = report.accuracy(name)
        if actual + 1e-9 < floor:
            failures.append(f"{name}: {actual:.1%} below the recorded {floor:.1%}")
    floor_rows = thresholds.get("min_exact_rows") or 0
    if report.exact_rows < floor_rows:
        failures.append(f"exact rows: {report.exact_rows} below the recorded {floor_rows}")
    if report.coupling_violations:
        failures.append(f"{len(report.coupling_violations)} status/method coupling violation(s)")
    return failures
