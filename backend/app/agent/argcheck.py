"""Check a tool call's arguments against the schema the model was shown for that tool.

Supports the subset of JSON Schema the tool definitions use: object (properties, required,
additionalProperties), array (items, minItems, maxItems), string (minLength, maxLength), integer, number,
boolean, null, enum, minimum, maximum, and a list of types. A boolean is never an integer or a number; an integer
is an `int` (not 500.0, not "500"). An object key the schema does not declare is refused unless the schema says
`additionalProperties: true`.

`problem(schema, args)` returns None when the arguments fit, else one plain sentence that names the argument.
`schema_for(tool)` is the one place the schema is looked up (the registry the model's tool list is built from,
and the task loop's own bookkeeping tools).
"""

from __future__ import annotations

from typing import Any

_TYPE_NAMES = {
    "object": "an object", "array": "a list", "string": "text", "integer": "a whole number",
    "number": "a number", "boolean": "true or false", "null": "empty",
}


def schema_for(tool: str) -> dict | None:
    """The parameters schema the model was shown for `tool`; None when the tool has none (not checked).

    The registered tools, and the task loop's own bookkeeping tools (which never enter the registry)."""
    from app.agent.tools import REGISTRY

    entry = REGISTRY.get(tool)
    if entry is None:
        from app.agent import taskloop

        if tool in taskloop.BOOKKEEPING_NAMES:
            for spec in taskloop.bookkeeping_specs(preferences=True):
                if spec.name == tool:
                    return spec.parameters if isinstance(spec.parameters, dict) else None
        return None
    params = getattr(getattr(entry, "spec", None), "parameters", None)
    return params if isinstance(params, dict) else None


def _is_type(value: Any, name: str) -> bool:
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value \
            and value not in (float("inf"), float("-inf"))
    if name == "null":
        return value is None
    return False


def _label(path: str) -> str:
    return f"'{path}'" if path else "The arguments"


def _check(schema: Any, value: Any, path: str) -> str | None:
    if not isinstance(schema, dict):
        return None
    types = schema.get("type")
    if types is not None:
        names = [types] if isinstance(types, str) else list(types)
        if not any(_is_type(value, n) for n in names):
            want = " or ".join(_TYPE_NAMES.get(n, n) for n in names)
            return f"{_label(path)} must be {want}."
    enum = schema.get("enum")
    if isinstance(enum, list) and not any(type(value) is type(e) and value == e for e in enum):
        return f"{_label(path)} must be one of: {', '.join(str(e) for e in enum)}."
    if isinstance(value, str):
        lo, hi = schema.get("minLength"), schema.get("maxLength")
        if isinstance(lo, int) and len(value) < lo:
            return f"{_label(path)} must have at least {lo} character(s)."
        if isinstance(hi, int) and len(value) > hi:
            return f"{_label(path)} must have at most {hi} character(s)."
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        lo, hi = schema.get("minimum"), schema.get("maximum")
        if isinstance(lo, (int, float)) and value < lo:
            return f"{_label(path)} must be at least {lo}."
        if isinstance(hi, (int, float)) and value > hi:
            return f"{_label(path)} must be at most {hi}."
    if isinstance(value, list):
        lo, hi = schema.get("minItems"), schema.get("maxItems")
        if isinstance(lo, int) and len(value) < lo:
            return f"{_label(path)} must have at least {lo} item(s)."
        if isinstance(hi, int) and len(value) > hi:
            return f"{_label(path)} must have at most {hi} item(s)."
        item = schema.get("items")
        if isinstance(item, dict):
            for i, v in enumerate(value):
                bad = _check(item, v, f"{path}[{i}]")
                if bad:
                    return bad
    if isinstance(value, dict):
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for key in schema.get("required") or ():
            if key not in value:
                return f"'{path + '.' if path else ''}{key}' is required."
        if schema.get("additionalProperties") is not True:
            for key in value:
                if key not in props:
                    return f"'{path + '.' if path else ''}{key}' is not an argument of this tool."
        for key, sub in props.items():
            if key in value:
                bad = _check(sub, value[key], f"{path + '.' if path else ''}{key}")
                if bad:
                    return bad
    return None


def problem(schema: dict | None, args: Any) -> str | None:
    """None when `args` fit `schema` (or there is no schema); else a plain sentence naming the argument."""
    if schema is None:
        return None
    return _check(schema, {} if args is None else args, "")
