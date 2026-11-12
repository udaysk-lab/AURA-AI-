"""The work-item service: create, plan, steps, verification, finalise, the owner's controls."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402
from sqlalchemy import event, select  # noqa: E402

from app import models  # noqa: E402
from app.models import ActivityLog, PendingAction, SkillRun, UsageRecord, WorkItem, WorkStep  # noqa: E402
from app.schemas import WorkItemDetail, WorkItemOut, WorkStepOut  # noqa: E402
from app.services import preferences as prefs  # noqa: E402
from app.services import vault  # noqa: E402
from app.services import workmemory as wm  # noqa: E402
from tests.work_helpers import conversation, finished_item, make_session, make_user  # noqa: E402


@pytest.fixture
def env(tmp_path):
    engine, db = make_session(tmp_path)

    @event.listens_for(engine, "connect")
    def _foreign_keys(conn, _record):  # as the app's own engine does
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    user = make_user(db)
    yield db, user
    db.close()
    engine.dispose()


def test_create_item_scrubs_types_and_titles(env):
    db, user = env
    vault.put(db, user.id, "pw", "correct-horse-battery")
    convo = conversation(db, user)
    text = "Reply to Jane's email\nuse password: correct-horse-battery " + "x" * 5000
    item = wm.create_item(db, user, text, trigger="schedule", origin_ref="sch-1", conversation=convo)
    assert item.status == "running" and item.trigger == "schedule" and item.origin_ref == "sch-1"
    assert item.conversation_id == convo.id and item.task_type == "email" and item.started_at is not None
    assert item.title == "Reply to Jane's email" and len(item.instruction) == 4000
    assert "correct-horse" not in item.instruction
    assert item.plan == [] and item.verification == {"status": "none", "checks": []}
    assert wm.title_from("\n\n  hello  \nworld") == "hello" and wm.title_from("") == "Untitled task"
    assert wm.create_item(db, user, "x", task_type="Calendar").task_type == "scheduling"


def test_plan_and_progress(env):
    db, user = env
    item = wm.create_item(db, user, "do things")
    plan = wm.set_plan(db, item, "  Tidy up  ", "To-Do", ["one", "  ", "two", *[f"s{i}" for i in range(20)]])
    assert item.title == "Tidy up" and item.task_type == "todo" and len(plan) == 12
    assert plan[1] == {"index": 1, "text": "two", "status": "todo", "note": ""}
    assert wm.update_plan_step(db, item, 1, "doing", "working on it") == {
        "index": 1, "text": "two", "status": "doing", "note": "working on it"}
    db.refresh(item)
    assert item.plan[1]["status"] == "doing" and item.plan[0]["status"] == "todo"
    for bad in ((12, "done"), (-1, "done"), (0, "todo"), (0, "bogus"), ("0", "done"), (True, "done")):
        assert wm.update_plan_step(db, item, *bad) is None
    long = wm.update_plan_step(db, item, 0, "failed", "n" * 500)
    assert len(long["note"]) == 200


def test_steps_are_ordered_and_redacted(env):
    db, user = env
    item = wm.create_item(db, user, "send")
    a = wm.add_step(db, item, kind="plan", summary="planned")
    b = wm.add_step(db, item, kind="tool", status="started", step_no=1, tool_name="send_email", tool_call_id="c1",
                    system="core-inbox", arguments={"to": "a@b.test", "password": "x", "body": "token=abc123 hi"})
    c = wm.add_step(db, item, kind="approval", status="waiting", detail={"pending_action_id": "pa-1", "authorization": "Bearer abcdefghijklmnop1234"})
    assert [s.seq for s in (a, b, c)] == [0, 1, 2]
    assert b.arguments == {"to": "a@b.test", "password": "[redacted]", "body": "token=[redacted] hi"}
    assert c.detail["authorization"] == "[redacted]"
    wm.finish_step(db, b, "ok", "Sent to a@b.test", {"duration_ms": 12})
    wm.finish_step(db, c, "approved", detail={"decision_source": "mode"})
    assert (b.status, b.summary, b.detail) == ("ok", "Sent to a@b.test", {"duration_ms": 12})
    assert c.detail == {"pending_action_id": "pa-1", "authorization": "[redacted]", "decision_source": "mode"}
    with pytest.raises(ValueError):
        wm.add_step(db, item, kind="nonsense")
    other = wm.create_item(db, user, "other")
    assert wm.add_step(db, other, kind="note").seq == 0, "seq counts per item"
    assert [s.seq for s in wm.steps_of(db, item.id)] == [0, 1, 2]
    assert len(wm.add_step(db, item, kind="note", summary="s" * 900).summary) == 500


def test_note_tool_lists_once_in_first_use_order(env):
    db, user = env
    item = wm.create_item(db, user, "x")
    for tool, system in (("get_email", "core-inbox"), ("aura_plan", "*"), ("send_email", "core-inbox"), ("get_email", "core-inbox"),
                         ("list_events", "core-calendar"), ("", "x")):
        wm.note_tool(db, item, tool, system)
    assert item.tools_used == ["get_email", "send_email", "list_events"]
    assert item.systems_used == ["core-inbox", "core-calendar"]


def test_checks_and_verification_status(env):
    db, user = env
    item = wm.create_item(db, user, "x")
    assert wm.verification_status([]) == "none"
    step = wm.add_check(db, item, True, "Read the event back", "Found at 10:00")
    assert step.kind == "verification" and step.status == "passed" and "Found at 10:00" in step.summary
    assert item.verification["status"] == "passed"
    wm.add_check(db, item, False, "Looked in Sent", "Not there")
    assert item.verification["status"] == "partial" and len(item.verification["checks"]) == 2
    other = wm.create_item(db, user, "y")
    wm.add_check(db, other, False, "m", "e")
    assert other.verification["status"] == "failed" and wm.steps_of(db, other.id)[0].status == "failed"


def test_is_lookup(env):
    db, user = env
    read = wm.create_item(db, user, "what's on my calendar")
    for _ in range(3):
        wm.add_step(db, read, kind="tool", tool_name="list_events", status="ok")
    assert wm.is_lookup(db, read)
    wm.add_step(db, read, kind="tool", tool_name="list_tasks", status="ok")
    assert not wm.is_lookup(db, read), "more than three tool calls"
    write = wm.create_item(db, user, "add")
    wm.add_step(db, write, kind="tool", tool_name="create_task", status="ok")
    assert not wm.is_lookup(db, write)
    planned = wm.create_item(db, user, "plan")
    wm.set_plan(db, planned, "t", None, ["a"])
    assert not wm.is_lookup(db, planned)
    asked = wm.create_item(db, user, "ask")
    wm.add_step(db, asked, kind="approval", status="waiting")
    assert not wm.is_lookup(db, asked)
    assert wm.is_lookup(db, wm.create_item(db, user, "just talk")), "no tools at all is also not task memory"


def test_finalise_a_write(env):
    db, user = env
    item = wm.create_item(db, user, "hello there")
    item.loop_state = {"messages": [1]}
    item.lease_owner = "tok"
    db.commit()
    wm.set_plan(db, item, "Make a note", None, ["Create it"])
    wm.add_step(db, item, kind="tool", tool_name="create_task", status="ok", summary="Created 'Water plants'")
    wm.add_step(db, item, kind="tool", tool_name="create_task", status="error", summary="Duplicate")
    wm.note_tool(db, item, "create_task", "core-tasks")
    assert item.task_type == "other"
    done = wm.finalise(db, item, status="done", report="\nAdded 'Water plants' to your to-dos.\n\nDid: created it", cost_usd=0.01,
                       input_tokens=10, output_tokens=5, steps_total=2)
    assert done.status == "done" and done.finished_at is not None and done.wait_reason == ""
    assert done.outcome == "Added 'Water plants' to your to-dos."
    assert done.task_type == "todo", "typed from the tools at the end"
    assert done.summary == "Create it (todo); create_task -> Created 'Water plants'; create_task -> Duplicate"
    assert done.recallable is True and done.loop_state == {} and done.lease_owner == "" and done.lease_until is None
    assert (done.cost_usd, done.input_tokens, done.output_tokens, done.steps_total) == (0.01, 10, 5, 2)
    assert done.embedding == [], "the mock provider cannot embed"
    with pytest.raises(ValueError):
        wm.finalise(db, item, status="running", report="")


def test_finalise_caps_and_scrubs(env):
    db, user = env
    item = wm.create_item(db, user, "x")
    wm.add_step(db, item, kind="tool", tool_name="create_task", status="ok")
    wm.finalise(db, item, status="partly_done", report="password: hunter2\n" + "r" * 5000, summary="s" * 3000,
                outcome="o" * 500, error="boom sk-abcdefghijklmnop1234")
    assert "hunter2" not in item.report and len(item.report) == 4000
    assert len(item.summary) == 1200 and len(item.outcome) == 300
    assert "sk-abc" not in item.error and "[redacted]" in item.error


@pytest.mark.parametrize("paused, memory_off, tools, plan, expected", [
    (False, False, ["create_task"], True, True),
    (False, False, ["list_events"], False, False),  # a lookup
    (False, False, ["list_events"], True, True),  # a plan makes it a task
    (True, False, ["create_task"], False, False),
    (False, True, ["create_task"], False, False),
])
def test_finalise_recallable_rules(env, paused, memory_off, tools, plan, expected):
    db, user = env
    convo = conversation(db, user, memory_off=memory_off)
    settings = db.scalars(select(models.UserSettings).where(models.UserSettings.user_id == user.id)).one()
    settings.memory_paused = paused
    db.commit()
    item = wm.create_item(db, user, "do it", conversation=convo)
    if plan:
        wm.set_plan(db, item, "t", None, ["a"])
    for t in tools:
        wm.add_step(db, item, kind="tool", tool_name=t, status="ok")
    assert wm.finalise(db, item, status="done", report="ok").recallable is expected


def test_finalise_embeds_when_the_provider_can(env, monkeypatch):
    from app.services import memory as memory_service
    from tests.work_helpers import FakeProvider

    db, user = env
    monkeypatch.setattr(memory_service, "get_provider", lambda: FakeProvider(vectors=lambda t: [0.5, 0.5]))
    item = wm.create_item(db, user, "add a task")
    wm.add_step(db, item, kind="tool", tool_name="create_task", status="ok")
    assert wm.finalise(db, item, status="done", report="ok").embedding == [0.5, 0.5]
    lookup = wm.create_item(db, user, "list")
    assert wm.finalise(db, lookup, status="done", report="ok").embedding == [], "not recallable: not indexed"


def test_edit_reembeds_and_validates(env, monkeypatch):
    from app.services import memory as memory_service
    from tests.work_helpers import FakeProvider

    db, user = env
    item = finished_item(db, user, title="Old title", task_type="email")
    monkeypatch.setattr(memory_service, "get_provider", lambda: FakeProvider(vectors=lambda t: [float(len(t)), 1.0]))
    wm.edit(db, item, title="New title", task_type="Calendar", summary="Did it")
    assert (item.title, item.task_type, item.summary) == ("New title", "scheduling", "Did it")
    assert item.embedding and item.embedding[0] == float(len(wm.index_text(item)))
    wm.edit(db, item, recallable=False)
    assert item.recallable is False and item.embedding == []
    with pytest.raises(ValueError):
        wm.edit(db, item, title="  ")


def test_feedback(env):
    db, user = env
    item = finished_item(db, user, title="Email Priya", task_type="email")
    wm.set_feedback(db, user, item, "up", "great")
    assert item.user_feedback["rating"] == "up" and item.user_feedback["note"] == "great"
    assert prefs.listing(db, user.id) == [], "an up-vote teaches nothing"
    wm.set_feedback(db, user, item, "down", "password: hunter2 and far too long " + "z" * 600)
    assert item.user_feedback["rating"] == "down" and len(item.user_feedback["note"]) == 500
    assert "hunter2" not in item.user_feedback["note"]
    (p,) = prefs.listing(db, user.id)
    assert (p.origin, p.source, p.task_type, p.origin_work_item_id) == ("feedback", "correction", "email", item.id)
    with pytest.raises(ValueError):
        wm.set_feedback(db, user, item, "meh")


def test_delete_links_become_null_and_steps_go(env):
    db, user = env
    item = wm.create_item(db, user, "x")
    step_id = wm.add_step(db, item, kind="note").id
    pa = PendingAction(user_id=user.id, tool_name="send_email", work_item_id=item.id)
    run = SkillRun(user_id=user.id, code="EM03", work_item_id=item.id)
    log = ActivityLog(user_id=user.id, action="a", work_item_id=item.id)
    use = UsageRecord(user_id=user.id, work_item_id=item.id)
    pref = models.Preference(user_id=user.id, task_type="email", text="t", source="correction", origin="chat",
                             origin_work_item_id=item.id)
    db.add_all([pa, run, log, use, pref])
    db.commit()
    with pytest.raises(wm.ItemBusy):
        wm.delete_item(db, item)
    item.status = "waiting"
    db.commit()
    wm.delete_item(db, item)
    for row in (pa, run, log, use, pref):
        db.refresh(row)
    assert pa.work_item_id is None and run.work_item_id is None and log.work_item_id is None and use.work_item_id is None
    assert pref.origin_work_item_id is None
    db.expire_all()
    assert db.get(WorkStep, step_id) is None and db.get(PendingAction, pa.id) is not None


def test_reset_keeps_open_items_and_other_users(env):
    db, user = env
    done = finished_item(db, user, title="a")
    failed = finished_item(db, user, title="b", status="failed")
    running = wm.create_item(db, user, "running")
    waiting = finished_item(db, user, title="c", status="waiting")
    other = finished_item(db, make_user(db, email="o@example.com"), title="theirs")
    assert wm.reset(db, user.id) == 2
    assert db.get(WorkItem, done.id) is None and db.get(WorkItem, failed.id) is None
    assert db.get(WorkItem, running.id) and db.get(WorkItem, waiting.id) and db.get(WorkItem, other.id)


def test_api_shapes(env):
    db, user = env
    item = wm.create_item(db, user, "send it")
    wm.add_step(db, item, kind="approval", status="waiting", tool_name="send_email", summary="needs ok",
                detail={"pending_action_id": "pa-9"})
    wm.add_check(db, item, True, "m", "e")
    wm.finalise(db, item, status="done", report="Sent.")
    db.refresh(item)
    out = WorkItemOut.model_validate(item).model_dump(mode="json")
    assert set(out) == {"id", "title", "task_type", "status", "trigger", "conversation_id", "outcome", "verification",
                        "steps_total", "cost_usd", "recallable", "created_at", "finished_at"}
    assert out["verification"]["status"] == "passed"
    detail = WorkItemDetail.model_validate(item).model_dump(mode="json")
    assert detail["recall_used"] == {"past_work": [], "preferences": []}
    assert detail["pending_action_ids"] == [] and detail["user_feedback"] == {}
    approval, check = detail["steps"]
    assert approval["pending_action_id"] == "pa-9" and approval["result_summary"] == "needs ok"
    assert approval["started_at"] and approval["summary"] == "needs ok" and approval["detail"]["pending_action_id"] == "pa-9"
    assert check["kind"] == "verification" and check["pending_action_id"] is None
    # a row with no verification yet reads as an object, never a bare string or null
    item.verification = {}
    assert WorkItemOut.model_validate(item).verification == {"status": "none", "checks": []}
    item.verification = {"status": "weird"}
    assert WorkItemOut.model_validate(item).verification["status"] == "none"
    # recall_used holds ids in the table; the API wants the resolved lists
    other = finished_item(db, user, title="Earlier job")
    p = prefs.add(db, user, "Be brief", "general", "stated", "settings").preference
    item.recall_used = {"work_item_ids": [other.id], "preference_ids": [p.id], "memory_ids": []}
    db.commit()
    assert WorkItemDetail.model_validate(item).recall_used.past_work == []
    resolved = WorkItemDetail.model_validate(item).model_copy(update={"recall_used": wm.resolve_recall(db, item)})
    assert resolved.recall_used["past_work"][0]["title"] == "Earlier job"
    assert WorkStepOut.model_validate(wm.steps_of(db, item.id)[0]).pending_action_id == "pa-9"
