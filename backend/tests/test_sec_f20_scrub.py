"""F20: secrets in tool arguments are not kept in activity rows, message meta or resolved approvals."""

from __future__ import annotations

import json
import os
import types

import pytest
from sqlalchemy import select

from app import models
from app.agent import coordinator, tools as tool_registry
from app.llm import ToolCall
from app.permissions import approvals, gate
from tests.sec_world import build

KEY = "sk-ant-api03-ABCDEFGHIJKLMNOP1234567890"
TEXT = f"my deploy password: hunter2hunter2 and key {KEY}"


def everything(db):
    rows = []
    for model in (models.ActivityLog, models.Message, models.PendingAction):
        for r in db.scalars(select(model)).all():
            rows.append(json.dumps({c.name: str(getattr(r, c.name)) for c in r.__table__.columns}, default=str))
    return "\n".join(rows)


def park(w, path, content):
    args = {"path": path, "content": content}
    ctx = gate.build_context(w.db, w.user, "chat")
    d = gate.decide(w.db, w.user, "write_file", args, ctx)
    assert d.kind == "ask", d.reason_code
    return approvals.queue_action(w.db, w.user, ctx, ToolCall("c1", "write_file", args), d), args


@pytest.mark.parametrize("answer", ["approve", "reject"])
def test_a_resolved_approval_keeps_no_secret(tmp_path, monkeypatch, answer):
    w = build(tmp_path, monkeypatch, mode="ask")
    action, args = park(w, os.path.join(w.work, "notes.txt"), TEXT)
    assert "hunter2hunter2" in json.dumps(action.arguments), "while it waits, the call is kept whole"
    out = approvals.resolve(w.db, w.user, action.id, "once" if answer == "approve" else "reject", None)
    w.db.refresh(action)
    assert action.status in ("approved", "rejected")
    blob = everything(w.db)
    assert "hunter2hunter2" not in blob and KEY not in blob, blob
    if answer == "approve":
        assert open(os.path.join(w.work, "notes.txt")).read() == TEXT, "the action itself ran with the real text"


def test_an_expired_approval_is_scrubbed(tmp_path, monkeypatch):
    from datetime import timedelta

    from app.models import utcnow

    w = build(tmp_path, monkeypatch, mode="ask")
    action, _ = park(w, os.path.join(w.work, "n.txt"), TEXT)
    action.expires_at = utcnow() - timedelta(minutes=1)
    w.db.commit()
    assert approvals.expire_due(w.db, w.user.id) == 1
    w.db.refresh(action)
    assert action.status == "expired" and "hunter2hunter2" not in everything(w.db)


def test_a_failing_tool_does_not_log_its_arguments(tmp_path, monkeypatch):
    w = build(tmp_path, monkeypatch, mode="skip")
    entry = tool_registry.REGISTRY["write_file"]

    def boom(ctx, args):
        raise RuntimeError(f"failed with {KEY}")

    monkeypatch.setattr(entry, "handler", boom)
    args = {"path": os.path.join(w.work, "a.txt"), "content": TEXT}
    d = gate.decide(w.db, w.user, "write_file", args, gate.build_context(w.db, w.user, "chat"))
    out = coordinator.execute_tool(w.db, w.user, "write_file", args, decision=d)
    assert "error" in out
    rows = w.db.scalars(select(models.ActivityLog).where(models.ActivityLog.status == "error")).all()
    assert rows and "hunter2hunter2" not in json.dumps(rows[0].detail) and KEY not in json.dumps(rows[0].detail)


def test_message_meta_keeps_no_secret(tmp_path, monkeypatch):
    w = build(tmp_path, monkeypatch)
    convo = models.Conversation(user_id=w.user.id, title="t")
    w.db.add(convo)
    w.db.commit()
    result = types.SimpleNamespace(
        text="done", tool_calls=[{"name": "write_file", "arguments": {"path": "p", "content": TEXT, "api_key": KEY}}],
        skill_runs=[], memories_used=[], steps=1, latency_ms=1, input_tokens=1, output_tokens=1, cost_usd=0.0,
        capped=False, pending_actions=[], work_item_id=None, work_status="", preferences_noted=[],
    )
    msg = coordinator.persist_exchange(w.db, convo, "hi", result)
    blob = json.dumps(msg.meta)
    assert "hunter2hunter2" not in blob and KEY not in blob
    assert msg.meta["tool_calls"][0]["arguments"]["path"] == "p"


def test_tool_log_rows_are_scrubbed(tmp_path, monkeypatch):
    w = build(tmp_path, monkeypatch)
    ctx = tool_registry.ToolContext(db=w.db, user=w.user)
    tool_registry._log(ctx, "web_research", f"look up {KEY}", {"note": TEXT})
    row = w.db.scalars(select(models.ActivityLog)).all()[-1]
    assert KEY not in row.target and "hunter2hunter2" not in json.dumps(row.detail)
