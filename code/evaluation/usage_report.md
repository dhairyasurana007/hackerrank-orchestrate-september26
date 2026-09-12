# Token usage and cost

Generated 2026-09-12 21:03 UTC from `evaluation/model_run_log.jsonl`, one record per model
call. Every figure below is read out of that log rather than written by hand, so the
totals reconcile with the run by construction.

## Provider and models

All calls go through **OpenRouter** (`https://openrouter.ai/api/v1`) using OpenAI
models, with the key read from the `OPENROUTER_API_KEY` environment variable. Model
ids are pinned in `buyorwait/model/config.py` and re-confirmed against OpenRouter's
live model list by `scripts/confirm_models.py`.

| Model | Role | Input $/M tokens | Output $/M tokens |
|---|---|---|---|
| `openai/gpt-4.1-mini` | message extraction | 0.40 | 1.60 |
| `openai/gpt-4.1` | document extraction | 2.00 | 8.00 |

## This run

**No model calls were made: the run used --no-llm**

The engine is deterministic and produces a complete, contract-valid `output.csv`
with the model layer disabled; every extraction degrades to "no amendment"
(PLAN.md 5.6). The figures below are therefore all zero, which is an accurate
record of this run and not a missing measurement.

- Requests processed: **2**
- Model calls: **0** (0 billed, 0 served from the response cache)
- Input tokens: **0**
- Output tokens: **0**
- Total tokens: **0**
- Average tokens per request: **0.0**
- Average tokens per call: **0.0**
- Estimated total cost: **$0**
- Estimated cost per request: **$0**
- Provider-reported total cost: **$0**

## Accounting notes

- Estimated cost is computed from the pinned per-million prices above; provider-reported cost is OpenRouter's own figure from each response's `usage` block.
- Cache hits are counted as calls but not as billed calls, since a warm-cache re-run issues no request and costs nothing.
- The run log stores a SHA-256 hash of each prompt and never the prompt text, so no message content and no credential can reach this report.
