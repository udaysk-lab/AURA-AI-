"""The loop engine with a scripted model (spec 20 sections 3, 5, 14)."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import models, permissions  # noqa: E402
from app.agent import coordinator, taskloop  # noqa: E402
from app.config import settings  # noqa: E402
from app.permissions import rules  # noqa: E402
from app.services import usage as usage_service  # noqa: E402
from tests.perm_helpers import make_session, make_user  # noqa: E402
from tests.scripted import RecordingMock, ScriptedProvider, call, reply  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine, db = make_session(tmp_path)
    user = make_user(db, mode="ask", migrated=True)
    convo = models.Conversation(user_id=user.id, title="t")
    db.add(convo)
    db.commit()
    yield db, user, convo
    db.close()
    engine.dispose()


def use(monkeypatch, provider):
    monkeypatch.setattr(coordinator, "get_provider", lambda: provider)
    return provider


def run(db, user, convo, text="please do it", **kw):
    events = []
    gen = coordinator.iter_agent(db, user, convo, text, **kw)
    while True:
        try:
            events.append(next(gen))
        except StopIteration as done:
            return events, done.value


def kinds(events):
    return [e["type"] for e in events]


def item_of(db, res):
    return db.get(models.WorkItem, res.work_item_id)


def steps(db, item):
    return list(db.scalars(select(models.WorkStep).where(models.WorkStep.work_item_id == item.id)
                           .order_by(models.WorkStep.seq)).all())


def test_quick_answer_creates_no_work_item(env, monkeypatch):
    db, user, convo = env
    use(monkeypatch, RecordingMock())
    events, res = run(db, user, convo, "hello there")
    assert db.scalars(select(models.WorkItem)).all() == []
    assert res.work_item_id is None and res.work_status == "" and res.text
    assert "report" not in kinds(events) and "task" not in kinds(events)


def test_lookup_is_recorded_but_not_recallable(env, monkeypatch):
    db, user, convo = env
    use(monkeypatch, RecordingMock())
    events, res = run(db, user, convo, "what are my tasks")
    item = item_of(db, res)
    assert item.status == "done" and item.recallable is False
    assert item.tools_used == ["list_tasks"] and "report" in kinds(events)


def test_write_creates_item_with_steps_and_links(env, monkeypatch):
    db, user, convo = env
    use(monkeypatch, ScriptedProvider([
        reply("", call("create_task", title="Call Priya")),
        reply("Added the task 'Call Priya'."),
    ]))
    events, res = run(db, user, convo, "add a task to call Priya")
    item = item_of(db, res)
    assert item.status == "done" and item.recallable and item.task_type == "todo"
    assert item.systems_used == ["core-tasks"] and item.tools_used == ["create_task"]
    tool_steps = [s for s in steps(db, item) if s.kind == "tool"]
    assert [(s.tool_name, s.status) for s in tool_steps] == [("create_task", "ok")]
    assert item.verification["status"] == "passed"
    for model in (models.SkillRun, models.ActivityLog, models.UsageRecord):
        rows = db.scalars(select(model).where(model.work_item_id == item.id)).all()
        assert rows, model.__name__
    ks = kinds(events)
    assert ks.index("task") < ks.index("step_started") < ks.index("verification") < ks.index("step_finished") < ks.index("report")
    assert item.steps_total == 2 and item.input_tokens == 200
    assert "Checked:" in item.report  # the footer adds what the model left out


def test_bookkeeping_plan_progress_check_events(env, monkeypatch):
    db, user, convo = env
    seen = []
    real = permissions.decide

    def spy(db_, user_, tool, args, ctx):
        seen.append(tool)
        return real(db_, user_, tool, args, ctx)

    monkeypatch.setattr(permissions, "decide", spy)
    use(monkeypatch, ScriptedProvider([
        reply("", call("aura_plan", title="Tidy tasks", task_type="todo", steps=["Add task", "Check it"])),
        reply("", call("aura_progress", index=0, status="doing"), call("create_task", title="Water plants"),
              call("aura_progress", index=0, status="done"),
              call("aura_check", passed=True, method="listed tasks", evidence="it is there"),
              call("aura_outcome", outcome="done", how="Added one task")),
        reply("Done. Added it."),
    ]))
    events, res = run(db, user, convo, "tidy up my tasks")
    item = item_of(db, res)
    assert item.title == "Tidy tasks" and item.plan[0]["status"] == "done" and item.plan[1]["status"] == "todo"
    assert item.summary == "Added one task"
    assert kinds(events).count("plan") == 3 and "verification" in kinds(events)
    assert set(seen) == {"create_task"}  # bookkeeping never reached the gate


def test_bookkeeping_without_work_is_refused_not_crashing(env, monkeypatch):
    db, user, convo = env
    prov = use(monkeypatch, ScriptedProvider([
        reply("", call("aura_check", passed=True, method="x")), reply("Nothing to do."),
    ]))
    _, res = run(db, user, convo)
    assert res.work_item_id is None
    assert '"ok": false' in prov.seen[1][-1]["content"]


def test_deny_returns_reason_to_model(env, monkeypatch):
    db, user, convo = env
    rules.add(db, user, effect="block", system="core-tasks", tool="create_task")
    prov = use(monkeypatch, ScriptedProvider([
        reply("", call("create_task", title="x")), reply("I could not add it; it is blocked."),
    ]))
    _, res = run(db, user, convo, "add a task x")
    msg = prov.tool_messages(1)[0]["content"]
    assert '"status": "blocked"' in msg and "Not run" in msg
    item = item_of(db, res)
    assert item.status == "failed"
    assert [s.status for s in steps(db, item) if s.kind == "tool"] == ["denied"]


def _reviewer_deny(tool, args):
    return SimpleNamespace(kind="deny", source="reviewer", reason="Not what was asked.", reason_code="REVIEWER_BLOCK",
                           tool=tool)


def test_reviewer_block_counts_persist_across_resume(env, monkeypatch):
    db, user, convo = env
    ctxs = []

    def fake(db_, user_, tool, args, ctx):
        ctxs.append((ctx.reviewer_streak, ctx.reviewer_total))
        return _reviewer_deny(tool, args)

    monkeypatch.setattr(permissions, "decide", fake)
    monkeypatch.setattr(settings, "max_agent_steps", 2)
    use(monkeypatch, ScriptedProvider([
        reply("", call("send_email", to="a@example.com", subject="s", body="b")),
        reply("", call("send_email", to="a@example.com", subject="s", body="b")),
        reply("", call("send_email", to="a@example.com", subject="s", body="b")),
        reply("Blocked, stopping."),
    ]))
    events, res = run(db, user, convo, "email a")
    assert res.work_status == "ready" and "continue" in kinds(events)
    assert [e["streak"] for e in events if e["type"] == "reviewer_block"] == [1, 2]
    item = item_of(db, res)
    assert item.loop_state["reviewer"] == {"streak": 2, "total": 2}
    gen = coordinator.iter_resume(db, user, item.id)
    out = []
    while True:
        try:
            out.append(next(gen))
        except StopIteration as done:
            res2 = done.value
            break
    assert ctxs == [(0, 0), (1, 1), (2, 2)]
    assert [e["streak"] for e in out if e["type"] == "reviewer_block"] == [3]
    db.refresh(item)
    assert item.status == "failed" and item.segments == 2
    assert any(s.kind == "reviewer_block" for s in steps(db, item))


def test_verifier_failure_makes_partly_done(env, monkeypatch):
    db, user, convo = env
    row = db.scalars(select(models.UserSettings)).first()
    row.permission_mode = "skip"
    db.commit()
    use(monkeypatch, ScriptedProvider([
        reply("", call("create_task", title="One"), call("send_email", to="a@example.com", subject="s", body="b")),
        reply("Task added; the email did not go."),
    ]))
    _, res = run(db, user, convo, "add a task and email a")
    item = item_of(db, res)
    assert item.verification["status"] == "partial"
    assert item.status == "partly_done"


@pytest.mark.parametrize(
    "kw,expected",
    [
        (dict(hint="stopped"), "stopped"),
        (dict(wrote=False, r_all=2, r_err=2), "failed"),
        (dict(wrote=False, r_all=2, r_err=1), "done"),
        (dict(wrote=False, hint="model_error", has_text=False), "failed"),
        (dict(wrote=False, hint="spend_cap", has_text=False), "failed"),
        (dict(wrote=False, hint="spend_cap", has_text=True), "done"),
        (dict(wrote=False), "done"),
        (dict(wrote=True, w_ok=2), "done"),
        (dict(wrote=True, w_ok=2, w_bad=1), "partly_done"),
        (dict(wrote=True, w_ok=1, v_fail=1), "partly_done"),
        (dict(wrote=True, w_ok=1, hint="step_cap"), "partly_done"),
        (dict(wrote=True, w_bad=1), "failed"),
        (dict(wrote=True), "failed"),
        (dict(wrote=True, w_ok=1, model="failed"), "failed"),
        (dict(wrote=True, w_ok=1, model="partly_done"), "partly_done"),
        (dict(wrote=True, w_bad=1, model="done"), "failed"),
        (dict(wrote=True, w_ok=1, hint="stopped", model="done"), "stopped"),
    ],
)
def test_outcome_table(kw, expected):
    base = dict(wrote=False, w_ok=0, w_bad=0, r_err=0, r_all=0, v_fail=0, hint=None, has_text=True, model=None)
    assert taskloop.classify_outcome(**{**base, **kw}) == expected


def test_model_outcome_cannot_upgrade(env, monkeypatch):
    db, user, convo = env
    rules.add(db, user, effect="block", system="core-tasks", tool="create_task")
    use(monkeypatch, ScriptedProvider([
        reply("", call("create_task", title="x")),
        reply("", call("aura_outcome", outcome="done", how="all good")),
        reply("Everything is done."),
    ]))
    _, res = run(db, user, convo, "add a task x")
    assert item_of(db, res).status == "failed"


def test_step_cap_finishes_item(env, monkeypatch):
    db, user, convo = env
    monkeypatch.setattr(settings, "max_task_steps", 2)
    use(monkeypatch, ScriptedProvider([reply("", call("create_task", id=f"c{i}", title=f"t{i}")) for i in range(5)]))
    _, res = run(db, user, convo, "add tasks")
    item = item_of(db, res)
    assert item.status == "partly_done" and item.steps_total == 2
    assert "step limit" in item.report


def test_spend_cap_finishes_with_message(env, monkeypatch):
    db, user, convo = env
    use(monkeypatch, ScriptedProvider([
        reply("", call("create_task", title="x")), reply("never reached"),
    ]))
    real = usage_service.check
    n = {"calls": 0}

    def check(db_, uid, trigger="chat", **kw):
        n["calls"] += 1
        if n["calls"] == 2:
            raise usage_service.SpendCapReached(usage_service.SpendState(5.0, 5.0, 3, 100), trigger)
        return real(db_, uid, trigger, **kw)

    monkeypatch.setattr(usage_service, "check", check)
    events, res = run(db, user, convo, "add a task x")
    assert res.capped and "spend cap" in res.text and "capped" in kinds(events)
    assert item_of(db, res).status == "partly_done"


def test_report_footer_adds_missing_sections_only(env, monkeypatch):
    db, user, convo = env
    use(monkeypatch, ScriptedProvider([
        reply("", call("create_task", title="x")),
        reply("Added.\nChecked: I looked the task up."),
    ]))
    _, res = run(db, user, convo, "add a task x")
    assert res.text.count("Checked:") == 1 and "looked the task up" in res.text


def test_model_error_finishes_item_with_error(env, monkeypatch):
    db, user, convo = env

    def boom(messages, tools):
        raise RuntimeError("provider down sk-abcdefghijklmnopqrstuvwx")

    use(monkeypatch, ScriptedProvider([reply("", call("create_task", title="x")), boom]))
    _, res = run(db, user, convo, "add a task x")
    item = item_of(db, res)
    assert "couldn't reach the model" in res.text and "sk-abcdefghijkl" not in item.error
    assert item.status == "partly_done"
