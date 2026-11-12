"""execute_tool refuses to run without the gate's sealed decision, and every caller goes through the gate."""

from __future__ import annotations

import ast
import os
import pathlib

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import models, permissions  # noqa: E402
from app.agent import coordinator, tools  # noqa: E402
from app.config import settings  # noqa: E402
from app.llm import LLMResponse, ToolCall, Usage  # noqa: E402
from app.models import PendingAction  # noqa: E402
from app.permissions import gate, rules  # noqa: E402
from tests.perm_helpers import ctx, decide, make_session, make_user  # noqa: E402

TASK = {"title": "Call Priya"}
SEND = {"to": "bob@example.com", "subject": "Hi", "body": "x"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine, db = make_session(tmp_path)
    monkeypatch.setattr(settings, "device_control", "off", raising=False)
    monkeypatch.setattr(settings, "local_mode", False, raising=False)
    user = make_user(db, mode="ask", migrated=True)
    yield db, user
    db.close()
    engine.dispose()


def test_execute_without_decision_raises(env):
    db, user = env
    with pytest.raises(PermissionError):
        coordinator.execute_tool(db, user, "create_task", TASK, decision=None)
    with pytest.raises(TypeError):  # the keyword is required, not defaulted
        coordinator.execute_tool(db, user, "create_task", TASK)  # type: ignore[call-arg]
    assert db.scalars(select(models.Task)).all() == []


def test_forged_decision_raises(env):
    db, user = env
    real = decide(db, user, "create_task", TASK)
    assert real.kind == "allow"
    forged = gate.Decision(
        kind="allow", source="mode", reason_code="DEFAULT_WORKSPACE", reason="", tool="create_task",
        args_digest=real.args_digest, user_id=user.id,
    )
    with pytest.raises(PermissionError):
        coordinator.execute_tool(db, user, "create_task", TASK, decision=forged)
    guessed = gate.Decision(
        kind="allow", source="mode", reason_code="X", reason="", tool="create_task",
        args_digest=real.args_digest, user_id=user.id, _seal=object(),
    )
    with pytest.raises(PermissionError):
        coordinator.execute_tool(db, user, "create_task", TASK, decision=guessed)
    with pytest.raises(PermissionError):
        coordinator.execute_tool(db, user, "create_task", TASK, decision="allow")  # type: ignore[arg-type]
    assert db.scalars(select(models.Task)).all() == []


def test_a_decision_cannot_be_edited_into_an_allow(env):
    db, user = env
    ask = decide(db, user, "delete_task", {"task_id": "x"})
    assert ask.kind == "ask"
    with pytest.raises(Exception):  # frozen
        ask.kind = "allow"  # type: ignore[misc]
    import dataclasses

    changed = dataclasses.replace(ask, kind="allow")  # copies the seal, changes the answer
    assert not gate.is_sealed(changed) and gate.is_sealed(ask)
    with pytest.raises(PermissionError):
        coordinator.execute_tool(db, user, "delete_task", {"task_id": "x"}, decision=changed)
    with pytest.raises(PermissionError):
        coordinator.execute_tool(db, user, "delete_task", {"task_id": "x"}, decision=ask)


def test_args_swapped_raises(env):
    db, user = env
    d = decide(db, user, "create_task", TASK)
    with pytest.raises(PermissionError):
        coordinator.execute_tool(db, user, "create_task", {"title": "something else"}, decision=d)
    with pytest.raises(PermissionError):  # same arguments, different tool
        coordinator.execute_tool(db, user, "complete_task", TASK, decision=d)
    # key order does not matter, content does
    d2 = decide(db, user, "create_task", {"title": "a", "priority": "high"})
    coordinator.execute_tool(db, user, "create_task", {"priority": "high", "title": "a"}, decision=d2)
    assert db.scalars(select(models.Task)).first().title == "a"


def test_ask_and_deny_decisions_never_execute(env):
    db, user = env
    for tool, args in (("delete_task", {"task_id": "x"}), ("bogus_tool", {})):
        d = decide(db, user, tool, args)
        assert d.kind in ("ask", "deny")
        with pytest.raises(PermissionError):
            coordinator.execute_tool(db, user, tool, args, decision=d)


def test_a_decision_belongs_to_one_user(env):
    db, user = env
    other = make_user(db, "other@example.com", mode="ask", migrated=True)
    d = decide(db, user, "create_task", TASK)
    with pytest.raises(PermissionError):
        coordinator.execute_tool(db, other, "create_task", TASK, decision=d)


def test_allowed_call_runs_and_the_handler_sees_the_decision(env):
    db, user = env
    d = decide(db, user, "create_task", TASK)
    seen = []
    original = tools.REGISTRY["create_task"].handler

    def spy(c, a):
        seen.append(c.decision)
        return original(c, a)

    tools.REGISTRY["create_task"].handler = spy
    try:
        out = coordinator.execute_tool(db, user, "create_task", TASK, decision=d)
    finally:
        tools.REGISTRY["create_task"].handler = original
    assert seen == [d] and "error" not in out


def test_run_gated_stops_what_the_gate_does_not_allow(env):
    db, user = env
    out = coordinator.run_gated(db, user, "send_email", SEND, ctx(db, user, "heartbeat"))
    assert out["blocked"] is True and out["reason_code"] == "TRIGGER_DENIED"
    assert coordinator.run_gated(db, user, "list_tasks", {}, ctx(db, user, "heartbeat")) == []


def test_only_execute_tool_calls_handlers():
    """No code in app/ reaches a handler except execute_tool (spec 4.6)."""
    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn_stack, node in _walk_with_functions(tree):
            where = f"{path.relative_to(root)}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, ast.Attribute) and node.attr == "handler" and isinstance(node.ctx, ast.Load):
                if not (path.name == "coordinator.py" and "execute_tool" in fn_stack):
                    offenders.append(("handler", where))
            if isinstance(node, ast.Subscript) and _is_registry(node.value) and isinstance(node.ctx, ast.Load):
                offenders.append(("REGISTRY[...]", where))
    assert offenders == []


def _is_registry(node) -> bool:
    return (isinstance(node, ast.Name) and node.id == "REGISTRY") or (
        isinstance(node, ast.Attribute) and node.attr == "REGISTRY"
    )


def _walk_with_functions(tree):
    def visit(node, stack):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            stack = stack + (node.name,)
        yield stack, node
        for child in ast.iter_child_nodes(node):
            yield from visit(child, stack)

    yield from visit(tree, ())


def test_no_module_other_than_the_gate_builds_decisions():
    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    for path in root.rglob("*.py"):
        if path.name == "gate.py":
            continue
        assert "_SEAL" not in path.read_text(encoding="utf-8"), path


# --- the loop and the other callers ---------------------------------------------------------


class Scripted:
    name = "scripted"

    def __init__(self, tool, arguments):
        self.tool, self.arguments = tool, arguments

    def complete(self, messages, tools=None, model=None):
        if messages and messages[-1]["role"] == "tool":
            return LLMResponse(text="ok", usage=Usage())
        return LLMResponse(tool_calls=[ToolCall(id="c1", name=self.tool, arguments=self.arguments)], usage=Usage())

    def embed(self, texts):
        return []


def run_turn(db, user, monkeypatch, tool, args, tier="conservative", trigger="chat", **kw):
    monkeypatch.setattr(coordinator, "get_provider", lambda: Scripted(tool, args))
    convo = models.Conversation(user_id=user.id, title="t")
    db.add(convo)
    db.commit()
    return coordinator.run_agent(db, user, convo, "please do it", autonomy_level=tier, trigger=trigger, **kw)


def test_the_loop_parks_an_ask_with_its_card_fields(env, monkeypatch):
    db, user = env
    res = run_turn(db, user, monkeypatch, "send_email", SEND)
    assert len(res.pending_actions) == 1
    a = res.pending_actions[0]
    assert (a.card, a.reason_code, a.always_user, a.origin) == ("tool", "MODE_ASK", False, "chat")
    assert a.options == ["once", "window", "always", "reject"] and a.args_digest == gate.args_digest(SEND)
    assert a.expires_at is not None and "Why I'm asking" in a.preview
    payload = res.tool_calls[0]["result"]
    assert payload["status"] == "awaiting_approval" and payload["approval_id"] == a.id and payload["reason"]
    # nothing ran
    assert db.scalars(select(models.PermissionEvent).where(models.PermissionEvent.kind == "approval_created")).all()


def test_the_loop_reports_a_block_to_the_model(env, monkeypatch):
    db, user = env
    rules.add(db, user, effect="block", system="core-inbox", tool="send_email")
    res = run_turn(db, user, monkeypatch, "send_email", SEND)
    assert not res.pending_actions
    p = res.tool_calls[0]["result"]
    assert p["status"] == "blocked" and p["reason_code"] == "RULE_BLOCK" and "Not run" in p["message"]


def test_the_loop_runs_what_is_allowed(env, monkeypatch):
    db, user = env
    res = run_turn(db, user, monkeypatch, "create_task", TASK)
    assert not res.pending_actions and res.skill_runs and db.scalars(select(models.Task)).first().title == "Call Priya"


def test_resolve_pending_rechecks_the_gate(env, monkeypatch):
    db, user = env
    res = run_turn(db, user, monkeypatch, "delete_task", {"task_id": "nothing"})
    action = res.pending_actions[0]
    rules.add(db, user, effect="block", system="core-tasks", tool="delete_task")  # added after the card
    out = coordinator.resolve_pending(db, user, action, "once")
    assert out["status"] == "refused" and "no longer allowed" in out["message"]
    db.refresh(action)
    assert action.status == "refused"


def test_resolve_pending_runs_an_approved_call_and_honours_expiry(env, monkeypatch):
    db, user = env
    res = run_turn(db, user, monkeypatch, "delete_task", {"task_id": "nothing"})
    action = res.pending_actions[0]
    out = coordinator.resolve_pending(db, user, action, "once")
    assert out.get("error") or out  # ran through execute_tool with the re-check's decision
    assert action.status == "approved"
    res = run_turn(db, user, monkeypatch, "delete_task", {"task_id": "nothing"})
    old = res.pending_actions[0]
    from datetime import timedelta

    old.expires_at = models.utcnow() - timedelta(minutes=1)
    db.commit()
    assert "expired" in coordinator.resolve_pending(db, user, old, "once")["error"]
    assert old.status == "expired"


def test_automation_actions_each_go_through_the_gate(env):
    from app.automation import engine

    db, user = env
    rule = models.AutomationRule(
        user_id=user.id, name="R", natural_language="when x, do y",
        actions=[{"tool": "create_task", "arguments": {"title": "from rule"}},
                 {"tool": "send_email", "arguments": SEND},
                 {"tool": "run_command", "arguments": {"command": "echo hi"}},
                 {"tool": "no_such_tool", "arguments": {}}],
    )
    db.add(rule)
    db.commit()
    out = engine.run_rule(db, user, rule, {"subject": "s"})
    assert [e["tool"] for e in out["executed"]] == ["create_task"]
    assert out["queued_for_approval"] == ["send_email"]
    assert [b["tool"] for b in out["blocked"]] == ["run_command", "no_such_tool"]
    parked = db.scalars(select(PendingAction)).all()
    assert len(parked) == 1 and parked[0].origin == "automation" and parked[0].origin_id == rule.id
    assert parked[0].conversation_id is None and "Automation “R” wants to" in parked[0].preview


def test_automation_keeps_what_the_old_engine_ran_alone(env):
    """Interim rules: safe/external actions of an existing automation still run, irreversible ones queue."""
    from app.automation import engine

    db, user = env
    legacy = make_user(db, "legacy@example.com", level="conservative")
    rule = models.AutomationRule(
        user_id=legacy.id, name="R", actions=[
            {"tool": "create_event", "arguments": {"title": "t", "start": "2030-01-01T10:00:00Z", "end": "2030-01-01T11:00:00Z"}},
            {"tool": "delete_event", "arguments": {"event_id": "e"}},
        ],
    )
    db.add(rule)
    db.commit()
    out = engine.run_rule(db, legacy, rule, {})
    assert [e["tool"] for e in out["executed"]] == ["create_event"]
    assert out["queued_for_approval"] == ["delete_event"]


def test_heartbeat_tools_go_through_the_gate(env, monkeypatch):
    from app.services import heartbeat

    db, user = env
    calls = []
    real = permissions.decide
    monkeypatch.setattr(permissions, "decide", lambda *a, **k: calls.append((a[2], a[4].trigger)) or real(*a, **k))
    heartbeat.run(db, user, "full", force=True)
    assert calls and all(trigger == "heartbeat" for _, trigger in calls)
