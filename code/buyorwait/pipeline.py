"""Pipeline orchestration: one row per request, with a per-request error boundary.

The contract demands exactly one row per request, so a single malformed record must never
cost the run. Each request is decided inside its own boundary; on an unhandled error the
pipeline emits the conservative fallback row, records the failure with its traceback, and
carries on (PLAN.md 5.8). The run then reports how many rows came from that path, and a
non-zero count is a defect to fix rather than an acceptable steady state.
"""

from __future__ import annotations

import datetime as dt
import json
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import forecast
from .fx import RateTable
from .loaders import Dataset
from .records import Request
from .state import UserState, build_state
from .writer import OutputRow, fallback_row, placeholder_row


@dataclass
class RunReport:
    rows: list[OutputRow] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)

    @property
    def fallback_rows(self) -> int:
        return sum(1 for row in self.rows if row.from_fallback)

    @property
    def fallback_request_ids(self) -> list[str]:
        return [row.request_id for row in self.rows if row.from_fallback]

    def summary(self) -> dict:
        return {
            "rows": len(self.rows),
            "fallback_rows": self.fallback_rows,
            "fallback_request_ids": self.fallback_request_ids,
            "failures": self.failures,
        }

    def write_summary(self, output_path: str | Path) -> Path:
        """Write the run summary beside the output, for CI to assert against."""
        path = Path(output_path)
        path = path.with_suffix(path.suffix + ".run.json")
        path.write_text(json.dumps(self.summary(), indent=2) + "\n", encoding="utf-8")
        return path


@dataclass
class Engine:
    """Everything a single decision needs, assembled once per run."""

    data: Dataset
    rates: RateTable
    use_llm: bool = False

    @classmethod
    def build(cls, data: Dataset, *, use_llm: bool = False) -> "Engine":
        return cls(data=data, rates=RateTable(data.rates), use_llm=use_llm)

    def state_for(self, request: Request) -> UserState:
        return build_state(
            request,
            self.data.profiles[request.user_id],
            self.data.events(request.user_id),
            self.rates,
        )

    def curve_for(self, request: Request):
        state = self.state_for(request)
        return forecast.build(
            request=request,
            profile=state.profile,
            events=state.events,
            rates=self.rates,
            events_by_id=state.events_by_id,
            amounts=state.amounts,
        )

    def decide(self, request: Request) -> OutputRow:
        """Decide one request. Wired to the real engine at M12."""
        return placeholder_row(request.request_id)

    def predicted_drawdown(self, request: Request) -> float:
        """Depth of the forecast curve's trough below the opening balance.

        The one figure the drawdown harness grades, measured through the request's own
        deadline — the safety horizon, not the full 90-day curve (see forecast.py).
        """
        return self.curve_for(request).drawdown(through=request.desired_completion_date)


def run(engine: Engine, requests) -> RunReport:
    report = RunReport()
    for request in requests:
        try:
            row = engine.decide(request)
        except Exception as error:  # noqa: BLE001 - the boundary is the point
            report.failures.append(
                {
                    "request_id": request.request_id,
                    "error": f"{type(error).__name__}: {error}",
                    "traceback": traceback.format_exc(),
                    "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
            )
            row = fallback_row(request.request_id)
        report.rows.append(row)
    return report
