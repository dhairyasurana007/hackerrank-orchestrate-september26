"""Generate ``evaluation/usage_report.md`` from the model run log (TASKS.md M17).

The report is a required submission artifact (AGENTS.md 6.5) and must summarise the final
full-dataset run's providers and model names, call counts, input and output tokens, totals
and averages per request, and estimated total and per-request cost.

It is generated from the JSONL run log rather than written by hand, which is what makes the
figures reconcile by construction. The log records a prompt *hash* and never the prompt, so
nothing in this path can put message text or a credential into the report — and the generator
asserts that before writing.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

REPORT_PATH = Path(__file__).resolve().parent / "usage_report.md"

#: Field names that must never reach the report. Checked on the rendered text, not trusted.
FORBIDDEN_SUBSTRINGS = ("OPENROUTER_API_KEY=", "Bearer ", "sk-or-", "api_key")


@dataclass
class ModelTotals:
    model: str = ""
    calls: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reported_cost_usd: float = 0.0
    estimated_cost_usd: float = 0.0
    schema_failures: int = 0
    errors: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def billed_calls(self) -> int:
        """Cache hits cost nothing, so they are not billed calls."""
        return self.calls - self.cache_hits


@dataclass
class UsageSummary:
    requests: int = 0
    by_model: dict = field(default_factory=dict)
    dropped: dict = field(default_factory=dict)

    @property
    def calls(self) -> int:
        return sum(t.calls for t in self.by_model.values())

    @property
    def billed_calls(self) -> int:
        return sum(t.billed_calls for t in self.by_model.values())

    @property
    def cache_hits(self) -> int:
        return sum(t.cache_hits for t in self.by_model.values())

    @property
    def input_tokens(self) -> int:
        return sum(t.input_tokens for t in self.by_model.values())

    @property
    def output_tokens(self) -> int:
        return sum(t.output_tokens for t in self.by_model.values())

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def estimated_cost_usd(self) -> float:
        return sum(t.estimated_cost_usd for t in self.by_model.values())

    @property
    def reported_cost_usd(self) -> float:
        return sum(t.reported_cost_usd for t in self.by_model.values())

    def per_request(self, value: float) -> float:
        return value / self.requests if self.requests else 0.0


def summarise(records, *, requests: int) -> UsageSummary:
    summary = UsageSummary(requests=requests)
    for record in records:
        if record.get("record") == "dropped":
            reason = record.get("reason", "unspecified")
            summary.dropped[reason] = summary.dropped.get(reason, 0) + 1
            continue
        if record.get("record") != "call":
            continue
        model = record.get("model") or "unknown"
        totals = summary.by_model.setdefault(model, ModelTotals(model=model))
        totals.calls += 1
        if record.get("cache_hit"):
            totals.cache_hits += 1
        totals.input_tokens += int(record.get("input_tokens") or 0)
        totals.output_tokens += int(record.get("output_tokens") or 0)
        if not record.get("cache_hit"):
            totals.reported_cost_usd += float(record.get("reported_cost_usd") or 0.0)
            totals.estimated_cost_usd += float(record.get("estimated_cost_usd") or 0.0)
        if record.get("error"):
            totals.errors += 1
        if not record.get("schema_valid"):
            totals.schema_failures += 1
    return summary


def read_log(path: str | Path) -> list[dict]:
    path = Path(path)
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            continue  # a truncated final line is a partial write, not a reason to fail
    return records


def _money(value: float) -> str:
    return f"${value:,.6f}".rstrip("0").rstrip(".") if value else "$0"


def render(summary: UsageSummary, *, offline_reason: str | None = None) -> str:
    from buyorwait.model.config import MODELS, spec_for

    generated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Token usage and cost",
        "",
        f"Generated {generated} from `evaluation/model_run_log.jsonl`, one record per model",
        "call. Every figure below is read out of that log rather than written by hand, so the",
        "totals reconcile with the run by construction.",
        "",
        "## Provider and models",
        "",
        "All calls go through **OpenRouter** (`https://openrouter.ai/api/v1`) using OpenAI",
        "models, with the key read from the `OPENROUTER_API_KEY` environment variable. Model",
        "ids are pinned in `buyorwait/model/config.py` and re-confirmed against OpenRouter's",
        "live model list by `scripts/confirm_models.py`.",
        "",
        "| Model | Role | Input $/M tokens | Output $/M tokens |",
        "|---|---|---|---|",
    ]
    for spec in MODELS:
        lines.append(
            f"| `{spec.model_id}` | {spec.role} | "
            f"{spec.input_usd_per_million:.2f} | {spec.output_usd_per_million:.2f} |"
        )

    lines += ["", "## This run", ""]
    if offline_reason:
        lines += [
            f"**No model calls were made: {offline_reason}**",
            "",
            "The engine is deterministic and produces a complete, contract-valid `output.csv`",
            "with the model layer disabled; every extraction degrades to \"no amendment\"",
            "(PLAN.md 5.6). The figures below are therefore all zero, which is an accurate",
            "record of this run and not a missing measurement.",
            "",
        ]

    lines += [
        f"- Requests processed: **{summary.requests}**",
        f"- Model calls: **{summary.calls}** "
        f"({summary.billed_calls} billed, {summary.cache_hits} served from the response cache)",
        f"- Input tokens: **{summary.input_tokens:,}**",
        f"- Output tokens: **{summary.output_tokens:,}**",
        f"- Total tokens: **{summary.total_tokens:,}**",
        f"- Average tokens per request: **{summary.per_request(summary.total_tokens):,.1f}**",
        f"- Average tokens per call: "
        f"**{(summary.total_tokens / summary.calls if summary.calls else 0):,.1f}**",
        f"- Estimated total cost: **{_money(summary.estimated_cost_usd)}**",
        f"- Estimated cost per request: **{_money(summary.per_request(summary.estimated_cost_usd))}**",
        f"- Provider-reported total cost: **{_money(summary.reported_cost_usd)}**",
        "",
    ]

    if summary.by_model:
        lines += [
            "## Per model",
            "",
            "| Model | Calls | Billed | Cache hits | Input tokens | Output tokens | "
            "Total tokens | Est. cost | Reported cost |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for model in sorted(summary.by_model):
            totals = summary.by_model[model]
            lines.append(
                f"| `{model}` | {totals.calls} | {totals.billed_calls} | {totals.cache_hits} | "
                f"{totals.input_tokens:,} | {totals.output_tokens:,} | {totals.total_tokens:,} | "
                f"{_money(totals.estimated_cost_usd)} | {_money(totals.reported_cost_usd)} |"
            )
        lines.append("")
        failures = sum(t.schema_failures for t in summary.by_model.values())
        errors = sum(t.errors for t in summary.by_model.values())
        lines += [
            f"Schema-validation failures: **{failures}**. Provider errors: **{errors}**. "
            "Both degrade to \"no amendment\" rather than failing the run.",
            "",
        ]

    if summary.dropped:
        lines += ["## Amendments dropped", "", "| Reason | Count |", "|---|---|"]
        for reason in sorted(summary.dropped, key=lambda r: (-summary.dropped[r], r)):
            lines.append(f"| {reason} | {summary.dropped[reason]} |")
        lines.append("")

    lines += [
        "## Accounting notes",
        "",
        "- Estimated cost is computed from the pinned per-million prices above; "
        "provider-reported cost is OpenRouter's own figure from each response's `usage` block.",
        "- Cache hits are counted as calls but not as billed calls, since a warm-cache re-run "
        "issues no request and costs nothing.",
        "- The run log stores a SHA-256 hash of each prompt and never the prompt text, so no "
        "message content and no credential can reach this report.",
        "",
    ]
    return "\n".join(lines)


def write_report(summary: UsageSummary, *, path=None, offline_reason: str | None = None) -> Path:
    text = render(summary, offline_reason=offline_reason)
    for forbidden in FORBIDDEN_SUBSTRINGS:
        if forbidden in text:
            raise AssertionError(f"usage report would contain {forbidden!r}")
    target = Path(path) if path else REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target
