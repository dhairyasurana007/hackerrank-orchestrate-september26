"""The drawdown harness: grades any forecaster against the 25 samples (TASKS.md M5b).

The harness takes a **callable**, not a forecaster class, so it can grade a stub, the
explicit-event-only curve of M7a, and the fully projected curve of M7b through the same
path — which is what makes the numbers those tasks report comparable to each other.

Two sets, scored by different rules and never pooled (PLAN.md 5.4):

* the **21 exact-trough** samples, where the target is an equality, are scored by median and
  mean absolute relative error;
* the **4 capped** samples, where the target is only an upper bound, are bound-checked.

It owns ``thresholds/drawdown.json``, which is a *different* file from M4's full-output
threshold: forecast-curve accuracy and emitted-row correctness are different measurements.
Rejected tuning variants are recorded in that file alongside their measured numbers, so a
falsified idea costs a line rather than being retried on intuition later.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from .drawdown_targets import DrawdownTarget, split, targets_from

THRESHOLDS_PATH = Path(__file__).resolve().parent / "thresholds" / "drawdown.json"


@dataclass(frozen=True)
class SampleResult:
    target: DrawdownTarget
    predicted_drawdown: float
    error: float | None  # signed relative error; None for a capped sample
    bound_violated: bool

    @property
    def request_id(self) -> str:
        return self.target.request_id


@dataclass
class DrawdownReport:
    results: list[SampleResult] = field(default_factory=list)

    @property
    def exact(self) -> list[SampleResult]:
        return [r for r in self.results if r.target.is_exact]

    @property
    def capped(self) -> list[SampleResult]:
        return [r for r in self.results if not r.target.is_exact]

    @property
    def absolute_errors(self) -> list[float]:
        return [abs(r.error) for r in self.exact if r.error is not None]

    @property
    def median_error(self) -> float:
        errors = self.absolute_errors
        return statistics.median(errors) if errors else 0.0

    @property
    def mean_error(self) -> float:
        errors = self.absolute_errors
        return statistics.fmean(errors) if errors else 0.0

    @property
    def bound_violations(self) -> list[SampleResult]:
        return [r for r in self.capped if r.bound_violated]

    def within(self, fraction: float) -> int:
        return sum(1 for error in self.absolute_errors if error <= fraction)

    def as_dict(self) -> dict:
        return {
            "exact_samples": len(self.exact),
            "capped_samples": len(self.capped),
            "median_relative_error": round(self.median_error, 4),
            "mean_relative_error": round(self.mean_error, 4),
            "bound_violations": [r.request_id for r in self.bound_violations],
            "within_10pct": self.within(0.10),
            "within_25pct": self.within(0.25),
        }

    def render(self, detail: bool = False) -> str:
        lines = [
            f"drawdown harness: {len(self.exact)} exact-trough samples, {len(self.capped)} capped",
            f"  median relative error   {self.median_error:7.1%}",
            f"  mean relative error     {self.mean_error:7.1%}",
            f"  within 10% / 25%        {self.within(0.10)} / {self.within(0.25)} of {len(self.exact)}",
            f"  bound violations        {len(self.bound_violations)}"
            + (f"  {[r.request_id for r in self.bound_violations]}" if self.bound_violations else ""),
        ]
        if detail:
            lines.append(
                f"  {'request':<12}{'kind':<8}{'target':>16}{'predicted':>16}{'error':>10}"
            )
            for result in sorted(self.results, key=lambda r: r.request_id):
                error = "bound" if result.error is None else f"{result.error:+.1%}"
                flag = "  BOUND!" if result.bound_violated else ""
                lines.append(
                    f"  {result.request_id:<12}{result.target.kind:<8}"
                    f"{result.target.target_drawdown:>16,.2f}{result.predicted_drawdown:>16,.2f}"
                    f"{error:>10}{flag}"
                )
        return "\n".join(lines)


def grade(forecaster, data) -> DrawdownReport:
    """Grade ``forecaster(request) -> predicted drawdown`` against every sample target."""
    report = DrawdownReport()
    for target in targets_from(data):
        predicted = float(forecaster(data.request(target.request_id)))
        if target.is_exact:
            report.results.append(
                SampleResult(
                    target=target,
                    predicted_drawdown=predicted,
                    error=target.relative_error(predicted),
                    bound_violated=False,
                )
            )
        else:
            report.results.append(
                SampleResult(
                    target=target,
                    predicted_drawdown=predicted,
                    error=None,
                    bound_violated=target.violates_bound(predicted),
                )
            )
    return report


def zero_forecaster(_request) -> float:
    """A forecaster that predicts no drawdown at all. The harness's own test subject."""
    return 0.0


def load_thresholds() -> dict:
    if not THRESHOLDS_PATH.is_file():
        return {}
    return json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))


def save_thresholds(thresholds: dict) -> None:
    THRESHOLDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    THRESHOLDS_PATH.write_text(json.dumps(thresholds, indent=2) + "\n", encoding="utf-8")


def check_thresholds(report: DrawdownReport, thresholds: dict | None = None) -> list[str]:
    """A worsened median fails. Thresholds ratchet down on acceptance, never up."""
    thresholds = load_thresholds() if thresholds is None else thresholds
    failures = []
    ceiling = thresholds.get("max_median_relative_error")
    if ceiling is not None and report.median_error > ceiling + 1e-9:
        failures.append(
            f"median relative error {report.median_error:.2%} worse than the recorded {ceiling:.2%}"
        )
    allowed = thresholds.get("max_bound_violations")
    if allowed is not None and len(report.bound_violations) > allowed:
        failures.append(
            f"{len(report.bound_violations)} bound violation(s) "
            f"{[r.request_id for r in report.bound_violations]} above the allowed {allowed}"
        )
    return failures
