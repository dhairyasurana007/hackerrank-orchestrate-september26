"""A small JSON-schema validator, stdlib only (PLAN.md 5.8).

Only the subset the extraction schemas use: object, array, string, number, integer, boolean,
``enum``, ``required``, ``additionalProperties: false``, ``minimum``/``maximum``, and
``nullable`` via a type union. A dependency is not worth taking on for this, and a hand-rolled
checker that covers exactly what is used is easier to trust than one that covers everything.

**Off-schema output is a dropped extraction, never a partial parse.** The validator returns
the list of problems; the caller discards the whole response when it is non-empty.
"""

from __future__ import annotations

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _type_matches(value, expected: str) -> bool:
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    python_type = _TYPES.get(expected)
    if python_type is None:
        return False
    if expected == "boolean":
        return isinstance(value, bool)
    return isinstance(value, python_type)


def validate(value, schema: dict, path: str = "$") -> list[str]:
    """Problems with ``value`` against ``schema``; empty means valid."""
    problems: list[str] = []

    expected = schema.get("type")
    if expected is not None:
        options = expected if isinstance(expected, list) else [expected]
        if not any(_type_matches(value, option) for option in options):
            return [f"{path}: expected {'/'.join(options)}, got {type(value).__name__}"]

    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: {value!r} is not one of {schema['enum']}")

    if isinstance(value, dict) and (expected == "object" or "properties" in schema):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                problems.append(f"{path}: missing required property {name!r}")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    problems.append(f"{path}: unexpected property {name!r}")
        for name, child in properties.items():
            if name in value:
                problems.extend(validate(value[name], child, f"{path}.{name}"))

    if isinstance(value, list) and (expected == "array" or "items" in schema):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            problems.append(f"{path}: {len(value)} items exceeds maxItems {schema['maxItems']}")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                problems.extend(validate(item, item_schema, f"{path}[{index}]"))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{path}: {value} below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{path}: {value} above maximum {schema['maximum']}")

    return problems
