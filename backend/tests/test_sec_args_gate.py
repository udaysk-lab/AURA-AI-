"""The gate and the executor both refuse arguments that do not have the declared form."""

from __future__ import annotations

import pytest

from app.agent.coordinator import execute_tool
from app.permissions import gate
from tests.perm_helpers import ctx
from tests.test_perm_screen import env, front, grant, here  # noqa: F401


@pytest.mark.parametrize("tool,args,word", [
    ("type_text", {"text": ["win", "r"]}, "'text'"),
    ("type_text", {"text": "x", "extra": 1}, "'extra'"),
    ("press_keys", {"keys": ["ctrl", "c"]}, "'keys'"),
    ("mouse_click", {"x": "500", "y": "300"}, "'x'"),
    ("mouse_click", {"x": 500.0, "y": 300}, "'x'"),
    ("mouse_click", {"x": True, "y": True}, "'x'"),
    ("scroll_screen", {"amount": "-5"}, "'amount'"),
    ("read_file", {"path": 7}, "'path'"),
    ("fetch_url", {"url": "https://example.com", "headers": {}}, "'headers'"),
])
def test_the_gate_denies_wrongly_formed_arguments_and_names_the_argument(env, monkeypatch, tool, args, word):
    db, user = env
    front(monkeypatch)
    here(user)
    grant(db, user, "notes", "full")
    d = gate.decide(db, user, tool, args, ctx(db, user))
    assert (d.kind, d.reason_code) == ("deny", "BAD_ARGS")
    assert word in d.reason


def test_execute_tool_checks_again(env, monkeypatch):
    """A sealed allow for arguments that do not fit the schema still runs nothing."""
    db, user = env
    front(monkeypatch)
    here(user)
    from app.agent import argcheck

    good = {"path": "~/x.txt"}
    # The second check is what stops a gate that was changed to let such a call through.
    monkeypatch.setattr(argcheck, "problem", lambda schema, args: "'path' must be text.")
    allow = gate._make(kind="allow", source="mode", reason_code="MODE_SKIP", reason="x", tool="read_file",
                       args_digest=gate.args_digest(good), user_id=user.id)
    with pytest.raises(PermissionError):
        execute_tool(db, user, "read_file", good, decision=allow)
