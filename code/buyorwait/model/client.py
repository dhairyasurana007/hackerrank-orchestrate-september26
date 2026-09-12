"""OpenRouter client: cache, schema validation, run log, failure isolation (TASKS.md M14).

Four guarantees sit around one ``POST /chat/completions`` (PLAN.md 5.6):

**Structured output.** Every call requests a JSON schema and the response is validated
against it. Off-schema output is a dropped extraction, never a partial parse.

**Cache.** Keyed by a hash of the model id, the messages, the schema, the schema version and
the call settings. Re-runs are free and byte-identical, which is what makes tuning the engine
against 250 requests practical — and it doubles as the debugging record, since every prompt
and response is on disk, greppable, offline, with no account.

**Failure isolation.** Any call that errors, times out, or fails validation degrades to "no
result". The engine always has a complete, valid answer without the model. A missing API key
is one of these cases and not an error: it is exactly the ``--no-llm`` path.

**Accounting.** Each response's ``usage`` block is recorded per call and per model into a
JSONL run log, from which ``evaluation/usage_report.md`` is generated directly. The cost of
the observability is therefore near zero — it is a by-product of a required artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths
from . import schema as schema_module
from .config import (
    API_KEY_ENV,
    COMPLETIONS_ENDPOINT,
    SCHEMA_VERSION,
    CallSettings,
    ModelSpec,
    spec_for,
)

RUN_LOG_PATH = paths.code_dir() / "evaluation" / "model_run_log.jsonl"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    reported_cost_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ModelResult:
    """The outcome of one call. ``payload`` is ``None`` whenever anything went wrong."""

    payload: dict | None
    model_id: str
    cache_hit: bool = False
    usage: Usage = field(default_factory=Usage)
    latency_ms: float = 0.0
    error: str | None = None
    schema_problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.payload is not None


def _digest(model_id: str, messages, response_schema: dict, settings: CallSettings) -> str:
    material = json.dumps(
        {
            "model": model_id,
            "messages": messages,
            "schema": response_schema,
            "schema_version": SCHEMA_VERSION,
            "temperature": settings.temperature,
            "seed": settings.seed,
            "max_output_tokens": settings.max_output_tokens,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ModelClient:
    """A single OpenAI-compatible client pointed at OpenRouter, for text and vision alike."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        settings: CallSettings | None = None,
        cache_dir: Path | None = None,
        use_cache: bool = True,
        run_log_path: Path | None = None,
        transport=None,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get(API_KEY_ENV)
        self.settings = settings or CallSettings()
        self.cache_dir = Path(cache_dir) if cache_dir else paths.cache_dir() / "model"
        self.use_cache = use_cache
        self.run_log_path = Path(run_log_path) if run_log_path else RUN_LOG_PATH
        # Injected in tests so the suite never makes a live call (TASKS.md M15). Tracked by
        # a flag rather than by comparing against self._post: a bound method is a fresh
        # object on every attribute access, so `is` against it is never true.
        self._injected_transport = transport is not None
        self.transport = transport or self._post
        self.records: list[dict] = []

    # ---------------------------------------------------------------- public

    @property
    def available(self) -> bool:
        """No key means no calls. Not an error — it is the offline path."""
        return bool(self.api_key) or self._injected_transport

    def complete_json(
        self,
        *,
        messages,
        response_schema: dict,
        model: ModelSpec,
        purpose: str,
        schema_name: str = "extraction",
    ) -> ModelResult:
        """One schema-validated call. Never raises; failures come back as ``ok is False``."""
        key = _digest(model.model_id, messages, response_schema, self.settings)

        cached = self._read_cache(key)
        if cached is not None:
            result = ModelResult(
                payload=cached.get("payload"),
                model_id=model.model_id,
                cache_hit=True,
                usage=Usage(**cached.get("usage", {})),
            )
            self._record(purpose, key, result)
            return result

        if not self.available:
            result = ModelResult(
                payload=None,
                model_id=model.model_id,
                error=f"{API_KEY_ENV} is not set; degraded to no result",
            )
            self._record(purpose, key, result)
            return result

        body = {
            "model": model.model_id,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": response_schema},
            },
            "max_tokens": self.settings.max_output_tokens,
            "seed": self.settings.seed,
            # Ask OpenRouter to return its own cost figure, so the report uses the
            # provider's number rather than a reconstruction.
            "usage": {"include": True},
        }
        if model.supports_temperature:
            body["temperature"] = self.settings.temperature

        result = self._call(body, response_schema, model)
        if result.ok:
            self._write_cache(key, result)
        self._record(purpose, key, result)
        return result

    def flush_run_log(self) -> Path | None:
        """Append this run's records to the JSONL log. Returns the path, or None if empty."""
        if not self.records:
            return None
        self.run_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.run_log_path.open("a", encoding="utf-8") as handle:
            for record in self.records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return self.run_log_path

    def note_dropped(self, purpose: str, reason: str, detail: dict | None = None) -> None:
        """Record an amendment the engine refused, and why.

        Dropped amendments are the most useful thing in the log: a wrong answer is usually a
        dropped amendment that should have been kept, or a kept one that should not have been.
        """
        # The discriminator is `record`, not `kind`: a dropped amendment's detail carries
        # its own `kind` and would otherwise overwrite it.
        self.records.append(
            {
                "record": "dropped",
                "purpose": purpose,
                "reason": reason,
                **(detail or {}),
            }
        )

    # --------------------------------------------------------------- internal

    def _call(self, body: dict, response_schema: dict, model: ModelSpec) -> ModelResult:
        last_error = "no attempt made"
        for attempt in range(1, self.settings.max_attempts + 1):
            started = time.monotonic()
            try:
                response = self.transport(body)
            except Exception as error:  # noqa: BLE001 - every failure degrades identically
                last_error = f"{type(error).__name__}: {error}"
                continue
            latency_ms = (time.monotonic() - started) * 1000.0

            usage_block = response.get("usage") or {}
            usage = Usage(
                input_tokens=int(usage_block.get("prompt_tokens") or 0),
                output_tokens=int(usage_block.get("completion_tokens") or 0),
                reported_cost_usd=usage_block.get("cost"),
            )
            try:
                content = response["choices"][0]["message"]["content"]
                payload = json.loads(content)
            except (KeyError, IndexError, TypeError, ValueError) as error:
                last_error = f"unparseable response: {type(error).__name__}: {error}"
                continue

            problems = schema_module.validate(payload, response_schema)
            if problems:
                return ModelResult(
                    payload=None,
                    model_id=model.model_id,
                    usage=usage,
                    latency_ms=latency_ms,
                    error="response failed schema validation",
                    schema_problems=tuple(problems[:8]),
                )
            return ModelResult(
                payload=payload, model_id=model.model_id, usage=usage, latency_ms=latency_ms
            )
        return ModelResult(payload=None, model_id=model.model_id, error=last_error)

    def _post(self, body: dict) -> dict:
        request = urllib.request.Request(
            COMPLETIONS_ENDPOINT,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                # Identifies the caller to OpenRouter; carries no secret.
                "X-Title": "buy-or-wait",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.settings.timeout_seconds) as handle:
            return json.loads(handle.read().decode("utf-8"))

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    def _read_cache(self, key: str) -> dict | None:
        if not self.use_cache:
            return None
        path = self._cache_path(key)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None  # a corrupt entry is a miss, not a crash

    def _write_cache(self, key: str, result: ModelResult) -> None:
        if not self.use_cache:
            return
        path = self._cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "payload": result.payload,
                    "usage": {
                        "input_tokens": result.usage.input_tokens,
                        "output_tokens": result.usage.output_tokens,
                        "reported_cost_usd": result.usage.reported_cost_usd,
                    },
                    "model": result.model_id,
                    "schema_version": SCHEMA_VERSION,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _record(self, purpose: str, key: str, result: ModelResult) -> None:
        spec = spec_for(result.model_id)
        estimated = (
            spec.cost(result.usage.input_tokens, result.usage.output_tokens) if spec else None
        )
        self.records.append(
            {
                "record": "call",
                "purpose": purpose,
                # The prompt hash, never the prompt: the log is safe to ship, and the cache
                # already holds the full text for debugging.
                "request_hash": key,
                "model": result.model_id,
                "cache_hit": result.cache_hit,
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "reported_cost_usd": result.usage.reported_cost_usd,
                "estimated_cost_usd": estimated,
                "latency_ms": round(result.latency_ms, 1),
                "schema_valid": result.payload is not None,
                "schema_problems": list(result.schema_problems),
                "error": result.error,
            }
        )
