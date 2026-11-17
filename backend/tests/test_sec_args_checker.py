"""Arguments are checked against the schema a tool was declared with."""

from __future__ import annotations

import pytest

from app.agent import argcheck, tools

S = {
    "type": "object",
    "properties": {
        "n": {"type": "integer", "minimum": 1, "maximum": 5},
        "f": {"type": "number"},
        "s": {"type": "string", "minLength": 1, "maxLength": 3},
        "b": {"type": "boolean"},
        "e": {"type": "string", "enum": ["a", "b"]},
        "l": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
        "m": {"type": ["string", "null"]},
        "o": {"type": "object", "properties": {"k": {"type": "integer"}}, "required": ["k"]},
    },
    "required": ["s"],
}


@pytest.mark.parametrize("args", [
    {"s": "x"}, {"s": "abc", "n": 5, "f": 1, "b": True, "e": "a", "l": ["x", "y"], "m": None, "o": {"k": 1}},
    {"s": "x", "f": 1.5}, {"s": "x", "m": "text"},
])
def test_valid_arguments_pass(args):
    assert argcheck.problem(S, args) is None


@pytest.mark.parametrize("args,word", [
    ({}, "'s'"),                                   # required
    ({"s": "x", "n": "3"}, "'n'"),                 # text is not an integer
    ({"s": "x", "n": 3.0}, "'n'"),                 # 3.0 is not an integer
    ({"s": "x", "n": True}, "'n'"),                # a boolean is never an integer
    ({"s": "x", "f": True}, "'f'"),                # nor a number
    ({"s": "x", "f": "1"}, "'f'"),
    ({"s": "x", "f": float("nan")}, "'f'"),
    ({"s": "x", "n": 0}, "'n'"), ({"s": "x", "n": 6}, "'n'"),
    ({"s": ""}, "'s'"), ({"s": "abcd"}, "'s'"), ({"s": ["x"]}, "'s'"),
    ({"s": "x", "b": 1}, "'b'"), ({"s": "x", "e": "c"}, "'e'"),
    ({"s": "x", "l": "ab"}, "'l'"), ({"s": "x", "l": ["a", 1]}, "'l[1]'"), ({"s": "x", "l": ["a", "b", "c"]}, "'l'"),
    ({"s": "x", "m": 5}, "'m'"),
    ({"s": "x", "o": {}}, "'o.k'"), ({"s": "x", "o": {"k": "1"}}, "'o.k'"),
    ({"s": "x", "extra": 1}, "'extra'"),           # an undeclared key
    ("text", "arguments"), (["s"], "arguments"),
])
def test_wrong_arguments_are_named(args, word):
    msg = argcheck.problem(S, args)
    assert msg and word in msg


def test_additional_properties_true_allows_other_keys():
    assert argcheck.problem({"type": "object", "properties": {}, "additionalProperties": True}, {"any": 1}) is None
    assert argcheck.problem({"type": "object", "properties": {}}, {"any": 1}) is not None


def test_no_schema_is_not_checked():
    assert argcheck.problem(None, {"anything": object()}) is None
    assert argcheck.schema_for("no_such_tool") is None


def test_a_tool_without_parameters_takes_none():
    schema = argcheck.schema_for("system_info")
    assert argcheck.problem(schema, {}) is None and argcheck.problem(schema, None) is None
    assert argcheck.problem(schema, {"x": 1})


def test_every_registered_tool_declares_what_its_handler_reads():
    """No handler reads an argument its schema leaves out (the checker refuses undeclared keys)."""
    import inspect
    import re

    import app.agent.device_tools  # noqa: F401  (registers the device tools)

    for name, entry in tools.REGISTRY.items():
        code = inspect.getsource(entry.handler)
        read = set(re.findall(r"\bargs(?:\.get\(|\[)\s*[\"'](\w+)[\"']", code))
        declared = set(entry.spec.parameters.get("properties", {}))
        assert read <= declared, (name, read - declared)


def test_the_task_loops_own_tools_are_checked_against_what_the_model_was_shown():
    from app.agent import taskloop

    for name in taskloop.BOOKKEEPING_NAMES:
        assert argcheck.schema_for(name) is not None, name
    check = argcheck.schema_for("aura_check")
    assert argcheck.problem(check, {"passed": True, "method": "ran the tests"}) is None
    assert "'passed'" in argcheck.problem(check, {"passed": "yes", "method": "x"})
    assert "'command'" in argcheck.problem(check, {"passed": True, "method": "x", "command": "rm -rf ~"})
    progress = argcheck.schema_for("aura_progress")
    assert "'index'" in argcheck.problem(progress, {"index": "0", "status": "done"})
    assert "'status'" in argcheck.problem(progress, {"index": 0, "status": "finished"})
    note = argcheck.schema_for("aura_note_preference")
    assert "'text'" in argcheck.problem(note, {"text": "x" * 301, "task_type": "general"})


def test_a_handler_never_reads_an_argument_its_tool_does_not_declare_and_the_runner_refuses_one():
    """The web fetch reads pages only: it takes no method, and a call that passes one never reaches it."""
    assert "method" not in argcheck.schema_for("fetch_url").get("properties", {})
    assert "'method'" in argcheck.problem(argcheck.schema_for("fetch_url"),
                                          {"url": "https://example.com", "method": "POST"})
