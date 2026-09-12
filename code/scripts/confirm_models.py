#!/usr/bin/env python3
"""Re-confirm the pinned model ids against OpenRouter's live list (PLAN.md assumption 13).

    python code/scripts/confirm_models.py

Needs no API key - the model list is public. Exits non-zero if a pinned id has disappeared,
lost a capability the code depends on, or changed price by more than a small margin, so a
silent deprecation surfaces here rather than as a run-time 404.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buyorwait.model.config import MODELS, MODELS_ENDPOINT  # noqa: E402

PRICE_TOLERANCE = 0.05  # 5% drift is a repricing worth noticing, not a failure to ignore


def main() -> int:
    with urllib.request.urlopen(MODELS_ENDPOINT, timeout=30) as handle:
        live = {m["id"]: m for m in json.loads(handle.read().decode("utf-8"))["data"]}

    failures = []
    for spec in MODELS:
        model = live.get(spec.model_id)
        if model is None:
            failures.append(f"{spec.model_id}: no longer listed by OpenRouter")
            continue
        supported = set(model.get("supported_parameters") or ())
        if "structured_outputs" not in supported:
            failures.append(f"{spec.model_id}: no longer advertises structured_outputs")
        if spec.supports_temperature and "temperature" not in supported:
            failures.append(
                f"{spec.model_id}: no longer accepts temperature, which determinism depends on"
            )
        pricing = model.get("pricing") or {}
        for label, pinned, key in (
            ("input", spec.input_usd_per_million, "prompt"),
            ("output", spec.output_usd_per_million, "completion"),
        ):
            try:
                actual = float(pricing[key]) * 1_000_000
            except (KeyError, TypeError, ValueError):
                failures.append(f"{spec.model_id}: no {label} price listed")
                continue
            if pinned <= 0 or abs(actual - pinned) / pinned > PRICE_TOLERANCE:
                failures.append(
                    f"{spec.model_id}: {label} price is {actual:.4f}/M, pinned {pinned:.4f}/M"
                )
        print(f"OK  {spec.model_id:<22} {spec.role:<22} confirmed {spec.confirmed_on}")

    for failure in failures:
        print(f"FAIL {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
