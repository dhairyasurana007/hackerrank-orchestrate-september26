"""Model configuration, pinned and confirmed rather than remembered (PLAN.md 5.6, 13).

Every value here was read from OpenRouter's live model list
(``GET https://openrouter.ai/api/v1/models``, which needs no key) on 2026-09-13 rather than
recalled. ``scripts/confirm_models.py`` re-checks them, so a silent deprecation shows up as
a failed check instead of as a run-time 404.

**Why ``gpt-4.1-mini`` for extraction rather than something cheaper.** The gpt-5.x family is
cheaper per token but does not accept ``temperature``, and PLAN.md assumption 12 requires
``temperature=0`` with a fixed seed so that a cold-cache run is reproducible up to model
non-determinism. Determinism is a stated requirement of the submission, so temperature
support is a selection criterion and not a preference. Among the models that accept it,
``gpt-4.1-mini`` is the cheapest that is not a nano-class model; extraction is narrow but it
is the accuracy lever the whole evidence layer rests on, and at roughly 116 calls the
difference between mini and nano is a fraction of a cent.
"""

from __future__ import annotations

from dataclasses import dataclass

BASE_URL = "https://openrouter.ai/api/v1"
MODELS_ENDPOINT = f"{BASE_URL}/models"
COMPLETIONS_ENDPOINT = f"{BASE_URL}/chat/completions"

API_KEY_ENV = "OPENROUTER_API_KEY"

#: Bumped whenever a prompt or schema changes, so a stale cache entry can never be reused
#: against a different contract.
SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class ModelSpec:
    """A pinned model, with the prices the usage report is built from."""

    model_id: str
    role: str
    input_usd_per_million: float
    output_usd_per_million: float
    supports_temperature: bool
    confirmed_on: str = "2026-09-13"

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_usd_per_million / 1_000_000
            + output_tokens * self.output_usd_per_million / 1_000_000
        )


#: Message extraction. Used by the MVP and by FINAL.
TEXT_MODEL = ModelSpec(
    model_id="openai/gpt-4.1-mini",
    role="message extraction",
    input_usd_per_million=0.40,
    output_usd_per_million=1.60,
    supports_temperature=True,
)

#: Document extraction from the 16 blank-amount PNGs. FINAL only; unused in the MVP.
VISION_MODEL = ModelSpec(
    model_id="openai/gpt-4.1",
    role="document extraction",
    input_usd_per_million=2.00,
    output_usd_per_million=8.00,
    supports_temperature=True,
)

MODELS = (TEXT_MODEL, VISION_MODEL)


def spec_for(model_id: str) -> ModelSpec | None:
    return next((spec for spec in MODELS if spec.model_id == model_id), None)


@dataclass(frozen=True)
class CallSettings:
    """Request parameters shared by every call."""

    temperature: float = 0.0
    seed: int = 20260913
    timeout_seconds: float = 45.0
    max_attempts: int = 2
    max_output_tokens: int = 900
    max_workers: int = 6
