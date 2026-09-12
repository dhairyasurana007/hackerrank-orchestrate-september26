"""Explanation polish with a hard grounding guard (TASKS.md F3)."""

from __future__ import annotations

from .. import explain
from .client import ModelClient
from .config import TEXT_MODEL

POLISH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["explanation"],
    "properties": {
        "explanation": {"type": "string"},
    },
}

SYSTEM_PROMPT = """\
Rewrite the financial recommendation in clearer, more natural language.

Hard constraint: keep exactly the same figures and dates as the original text. Do not add a
number, remove a number, change an amount, change a date, or add a new financial fact. If a
detail is awkward, preserve it anyway.
"""


def polish_explanation(*, template: str, client: ModelClient, purpose: str) -> str:
    """Return polished text only when it carries exactly the template's figures."""
    result = client.complete_json(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Original explanation:\n" + template,
            },
        ],
        response_schema=POLISH_SCHEMA,
        model=TEXT_MODEL,
        purpose=purpose,
        schema_name="polished_explanation",
    )
    if not result.ok:
        return template
    candidate = str((result.payload or {}).get("explanation") or "").strip()
    if not candidate:
        return template
    if explain.figures_in(candidate) != explain.figures_in(template):
        client.note_dropped(
            purpose,
            "polished explanation changed the figure/date set",
            {"template_figures": sorted(explain.figures_in(template))},
        )
        return template
    return candidate
