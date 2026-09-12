"""Pipeline orchestration: one row per request, with a per-request error boundary.

The contract demands exactly one row per request, so a single malformed record must never
cost the run. Each request is decided inside its own boundary; on an unhandled error the
pipeline emits the conservative fallback row, records the failure with its traceback, and
carries on (PLAN.md 5.8). The run then reports how many rows came from that path, and a
non-zero count is a defect to fix rather than an acceptable steady state.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import evidence, explain, forecast, planner, recurrence, spending
from .fx import RateTable
from .loaders import Dataset
from .model.client import ModelClient
from .model.extract import apply_to_series, extract
from .model.polish import polish_explanation
from .model.vision import extract_amount
from .records import Request
from .state import UserState, build_state
from .validator import validate_row
from .writer import OutputRow, fallback_row


@dataclass
class RunReport:
    rows: list[OutputRow] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    validation_fallbacks: list[dict] = field(default_factory=list)

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
            "validation_fallbacks": self.validation_fallbacks,
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
    client: ModelClient | None = None
    amendment_log: list = field(default_factory=list)
    vision_amounts: dict[str, dict[str, float]] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        data: Dataset,
        *,
        use_llm: bool = False,
        client: ModelClient | None = None,
        use_cache: bool = True,
    ) -> "Engine":
        if use_llm and client is None:
            client = ModelClient(use_cache=use_cache)
        return cls(data=data, rates=RateTable(data.rates), use_llm=use_llm, client=client)

    def state_for(self, request: Request, *, recover_images: bool = True) -> UserState:
        return build_state(
            request,
            self.data.profiles[request.user_id],
            self.data.events(request.user_id),
            self.rates,
            recovered=self.recovered_amounts_for(request) if recover_images else {},
        )

    def recovered_amounts_for(self, request: Request) -> dict[str, float]:
        """Exact document amounts recovered from linked images for this request.

        Empty on the offline path, preserving the MVP's blank-amount fallback. Results are
        memoized because state reconstruction is used by both extraction and forecasting.
        """
        if request.request_id in self.vision_amounts:
            return dict(self.vision_amounts[request.request_id])
        recovered: dict[str, float] = {}
        if self.use_llm and self.client is not None and self.client.available:
            events = self.data.events(request.user_id)
            for event in events:
                if event.amount is not None:
                    continue
                image_ref = self.data.images_by_event.get(event.event_id)
                if image_ref is None or image_ref.request_id != request.request_id:
                    continue
                amount = extract_amount(
                    request=request,
                    event=event,
                    image_ref=image_ref,
                    dataset_root=self.data.root,
                    client=self.client,
                )
                if amount is not None:
                    recovered[event.event_id] = amount.amount
        self.vision_amounts[request.request_id] = dict(recovered)
        return recovered

    def amendments_for(self, request: Request):
        """Validated amendments for one request, or () on the offline path."""
        if not (self.use_llm and self.client is not None and self.client.available):
            return ()
        state = self.state_for(request, recover_images=False)
        purpose = f"message-extraction:{request.request_id}"

        def note(reason, amendment):
            self.client.note_dropped(
                purpose, reason, {"kind": amendment.kind, "message_id": amendment.message_id}
            )

        raw = extract(
            request=request,
            profile=state.profile,
            messages=self.data.messages(request.user_id),
            events=state.events,
            client=self.client,
        )
        kept = []
        for amendment in raw:
            if evidence.validate(amendment, request=request, events=state.events, note=note) is None:
                kept.append(amendment)
        if kept:
            self.amendment_log.append((request.request_id, tuple(kept)))
        return tuple(kept)

    def curve_for(self, request: Request):
        state = self.state_for(request)
        series = recurrence.detect(
            state.events, as_of=request.request_date, amounts=state.amounts
        )
        explicit = forecast.build_explicit(
            request=request,
            profile=state.profile,
            events=state.events,
            rates=self.rates,
            events_by_id=state.events_by_id,
            amounts=state.amounts,
        )
        series = series + recurrence.confirmed_income_series(
            explicit.explicit, series, as_of=request.request_date
        )
        amendments = self.amendments_for(request)
        if amendments:
            purpose = f"message-extraction:{request.request_id}"

            def note(reason, amendment):
                self.client.note_dropped(
                    purpose, reason, {"kind": amendment.kind, "message_id": amendment.message_id}
                )

            series = apply_to_series(
                series,
                amendments,
                request=request,
                profile=state.profile,
                rates=self.rates,
                note=note,
            )
        return forecast.build(
            request=request,
            profile=state.profile,
            events=state.events,
            rates=self.rates,
            events_by_id=state.events_by_id,
            amounts=state.amounts,
            series=series,
        )

    def decide(self, request: Request) -> OutputRow:
        """Decide one request end to end."""
        decision = self.decision_for(request)
        profile = self.data.profiles[request.user_id]
        explanation = explain.render(decision, request, profile)
        if self.use_llm and self.client is not None and self.client.available:
            explanation = polish_explanation(
                template=explanation,
                client=self.client,
                purpose=f"explanation-polish:{request.request_id}",
            )
        return OutputRow(
            request_id=request.request_id,
            amount_safe_to_pay=decision.amount_safe_to_pay,
            affordability_status=decision.chosen.status,
            recommended_payment_method=decision.chosen.method,
            payment_plan=decision.chosen.payments,
            earliest_date_for_full_payment=decision.emitted_earliest,
            spending_changes_needed=decision.chosen.spending_changes,
            decision_explanation=explanation,
        )

    def decision_for(self, request: Request):
        profile = self.data.profiles[request.user_id]

        def change_search(**kwargs):
            return spending.search(rates=self.rates, **kwargs)

        decision = planner.decide(
            request=request,
            profile=profile,
            curve=self.curve_for(request),
            options=self.data.options(request.request_id),
            spending_change_search=change_search,
        )
        return dataclasses.replace(
            decision,
            changed_events=tuple(
                self.data.events_by_id[change.split(":")[1]]
                for change in decision.chosen.spending_changes
            ),
        )

    def predicted_drawdown(self, request: Request) -> float:
        """Depth of the forecast curve's trough below the opening balance.

        The one figure the drawdown harness grades, measured through the request's own
        deadline — the safety horizon, not the full 90-day curve (see forecast.py).
        """
        return self.curve_for(request).drawdown(through=request.desired_completion_date)


def run(engine: Engine, requests) -> RunReport:
    report = RunReport()
    mvp_engine = Engine.build(engine.data, use_llm=False) if engine.use_llm else None
    for request in requests:
        try:
            row = engine.decide(request)
            violations = _row_violations(engine, request, row)
            if violations and mvp_engine is not None:
                mvp_row = mvp_engine.decide(request)
                mvp_violations = _row_violations(mvp_engine, request, mvp_row)
                if not mvp_violations:
                    report.validation_fallbacks.append(
                        {
                            "request_id": request.request_id,
                            "violations": [str(v) for v in violations],
                        }
                    )
                    row = mvp_row
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


def _row_violations(engine: Engine, request: Request, row: OutputRow):
    profile = engine.data.profiles[request.user_id]
    return validate_row(row.as_csv_dict(), request, profile, engine.data.options(request.request_id))
