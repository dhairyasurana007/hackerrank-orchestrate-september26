# Token usage and cost

Generated 2026-09-12 22:10 UTC from `evaluation/model_run_log.jsonl`, one record per model
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

- Requests processed: **250**
- Model calls: **198** (198 billed, 0 served from the response cache)
- Input tokens: **185,991**
- Output tokens: **9,278**
- Total tokens: **195,269**
- Average tokens per request: **781.1**
- Average tokens per call: **986.2**
- Estimated total cost: **$0.089241**
- Estimated cost per request: **$0.000357**
- Provider-reported total cost: **$0.089241**

## Per model

| Model | Calls | Billed | Cache hits | Input tokens | Output tokens | Total tokens | Est. cost | Reported cost |
|---|---|---|---|---|---|---|---|---|
| `openai/gpt-4.1-mini` | 198 | 198 | 0 | 185,991 | 9,278 | 195,269 | $0.089241 | $0.089241 |

Schema-validation failures: **0**. Provider errors: **0**. Both degrade to "no amendment" rather than failing the run.

## Amendments dropped

| Reason | Count |
|---|---|
| income_suppressed matched no projecting income series | 13 |
| income_stopped matched no projecting income series | 12 |

## Accounting notes

- Estimated cost is computed from the pinned per-million prices above; provider-reported cost is OpenRouter's own figure from each response's `usage` block.
- Cache hits are counted as calls but not as billed calls, since a warm-cache re-run issues no request and costs nothing.
- The run log stores a SHA-256 hash of each prompt and never the prompt text, so no message content and no credential can reach this report.
