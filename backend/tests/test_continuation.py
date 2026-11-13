"""Background continuation: parked work carries on from the worker and the cron tick (spec 20 section 7)."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import json  # noqa: E402
from datetime import timedelta  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import worker  # noqa: E402
from app import models  # noqa: E402
from app.agent import coordinator, taskloop  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import utcnow  # noqa: E402
from app.permissions import approvals  # noqa: E402
from app.services import schedules  # noqa: E402
from tests.perm_helpers import make_session, make_user  # noqa: E402
from tests.scripted import ScriptedProvider, call, reply  # noqa: E402

SEND = dict(to="priya@example.com", subject="Q3", body="Numbers attached.")


@pytest.fixture
def env(tmp_path, monkeypatch):
    eng, db = make_session(tmp_path)
    user = make_user(db, mode="ask", migrated=True)
    convo = models.Conversation(user_id=user.id, title="t")
    db.add(convo)
    db.commit()
    from app.services import mailer

    monkeypatch.setattr(mailer, "send", lambda db, user, to, subject, body: {"sent": True, "to": to, "route": "test"})
    yield db, user, convo, eng
    db.close()
    eng.dispose()


def use(monkeypatch, provider):
    monkeypatch.setattr(coordinator, "get_provider", lambda: provider)
    return provider


def drain(gen):
    events = []
    while True:
        try:
            events.append(next(gen))
        except StopIteration as done:
            return events, done.value


def schedule_for(db, user, deliver_to="notification", mode="ask"):
    return schedules.create(db, user, "Send Priya the Q3 numbers", name="Q3 numbers", cron="0 8 * * *",
                            deliver_to=deliver_to, approval_mode=mode)


def pend(db, item):
    return list(db.scalars(select(models.PendingAction).where(models.PendingAction.work_item_id == item.id)
                           .order_by(models.PendingAction.created_at, models.PendingAction.id)).all())


def notes(db, user):
    return list(db.scalars(select(models.Notification).where(models.Notification.user_id == user.id)
                           .order_by(models.Notification.created_at, models.Notification.id)).all())


def park_schedule(db, user, monkeypatch, *, script, **kw):
    """Run a schedule whose model asks to send an email: it parks on the approval card."""
    prov = use(monkeypatch, ScriptedProvider(script))
    sched = schedule_for(db, user, **kw)
    out = schedules.run_one(db, user, sched)
    item = db.get(models.WorkItem, out["work_item_id"])
    assert item.status == "waiting" and item.trigger == "schedule" and item.origin_ref == sched.id
    return prov, sched, item


def approve(db, user, item):
    out = approvals.resolve(db, user, pend(db, item)[0].id, "once")
    assert out["work_item"] == {"id": item.id, "status": "ready"}
    db.refresh(item)


def test_segment_budget_parks_ready_and_emits_continue(env, monkeypatch):
    db, user, convo, _ = env
    monkeypatch.setattr(settings, "max_agent_steps", 1)
    use(monkeypatch, ScriptedProvider([reply("", call("list_tasks", id="l1")), reply("All checked.")]))
    events, res = drain(coordinator.iter_agent(db, user, convo, "go through my tasks"))
    item = db.get(models.WorkItem, res.work_item_id)
    assert item.status == "ready" and item.wait_reason == "segment_budget" and item.ready_at is not None
    assert item.lease_until is None and res.work_status == "ready"
    cont = [e for e in events if e["type"] == "continue"]
    assert cont == [{"type": "continue", "work_item_id": item.id, "reason": "segment_budget"}]
    assert "Still working" in res.text


def test_worker_resumes_ready_background_item(env, monkeypatch):
    db, user, convo, _ = env
    prov, sched, item = park_schedule(db, user, monkeypatch, script=[
        reply("", call("send_email", id="s1", **SEND)), reply("Sent the Q3 numbers to Priya."),
    ])
    assert taskloop.resume_due(db)["resumed"] == 0  # still waiting for the owner: nothing to do
    approve(db, user, item)
    out = taskloop.resume_due(db)
    assert out["resumed"] == 1 and out["finished"] == 1
    db.refresh(item)
    assert item.status == "done" and item.segments == 2
    tool = json.loads(prov.tool_messages(1)[0]["content"])
    assert tool["status"] == "approved_and_run"
    # the transcript carries the continuation, and the schedule shows the final report
    convo = db.get(models.Conversation, item.conversation_id)
    texts = [m.content for m in db.scalars(select(models.Message).where(models.Message.conversation_id == convo.id)
                                           .order_by(models.Message.created_at, models.Message.id))]
    assert any("Sent the Q3 numbers" in t for t in texts)
    db.refresh(sched)
    assert "Sent the Q3 numbers" in sched.last_result
    assert taskloop.resume_due(db)["resumed"] == 0  # a finished item is not picked up again


def test_schedule_item_delivers_on_finish(env, monkeypatch):
    db, user, _, _ = env
    sent = []
    from app.services import mailer

    monkeypatch.setattr(mailer, "send", lambda db, user, to, subject, body: sent.append((to, subject, body))
                        or {"sent": True, "to": to, "route": "test"})
    user.email = "owner@example.com"
    db.commit()
    _, sched, item = park_schedule(db, user, monkeypatch, deliver_to="email", script=[
        reply("", call("send_email", id="s1", **SEND)), reply("Sent the Q3 numbers to Priya."),
    ])
    parked_notes = len(notes(db, user))
    sent_before = len(sent)
    approve(db, user, item)
    taskloop.resume_due(db)
    # the report went out by email, once, and the app was told too
    assert [s for s in sent[sent_before:] if s[0] == "owner@example.com" and "Sent the Q3 numbers" in s[2]]
    after = notes(db, user)
    assert len(after) > parked_notes
    last = after[-1]
    assert last.title == "Q3 numbers" and "Sent the Q3 numbers" in last.body and last.level == "info"
    assert last.link == f"/chat?c={item.conversation_id}"


def test_schedule_that_finishes_in_one_segment_is_delivered_once(env, monkeypatch):
    db, user, _, _ = env
    use(monkeypatch, ScriptedProvider([reply("", call("list_tasks", id="l1")), reply("Nothing due today.")]))
    sched = schedule_for(db, user)
    out = schedules.run_one(db, user, sched)
    assert out["work_status"] in ("done", "") and "Nothing due" in out["text"]
    rows = [n for n in notes(db, user) if n.title == "Q3 numbers"]
    assert len(rows) == 1 and rows[0].level == "info"


def test_worker_respects_grace_for_chat_items(env, monkeypatch):
    db, user, convo, _ = env
    prov = use(monkeypatch, ScriptedProvider([reply("", call("send_email", id="s1", **SEND)), reply("Sent.")]))
    events, res = drain(coordinator.iter_agent(db, user, convo, "email priya the numbers"))
    item = db.get(models.WorkItem, res.work_item_id)
    approve(db, user, item)
    assert item.trigger == "chat"
    # the open page gets the first go
    assert taskloop.resume_due(db)["resumed"] == 0
    assert db.get(models.WorkItem, item.id).status == "ready"
    item.ready_at = utcnow() - timedelta(seconds=settings.resume_grace_seconds + 5)
    db.commit()
    out = taskloop.resume_due(db)
    assert out["resumed"] == 1
    db.refresh(item)
    assert item.status == "done"
    # finished by the worker with nobody watching: the owner is told
    note = notes(db, user)[-1]
    assert note.link == f"/chat?c={convo.id}" and note.level == "info"
    assert prov.responses == []


def test_chat_item_finished_in_the_open_page_raises_no_notification(env, monkeypatch):
    db, user, convo, _ = env
    use(monkeypatch, ScriptedProvider([reply("", call("send_email", id="s1", **SEND)), reply("Sent.")]))
    _, res = drain(coordinator.iter_agent(db, user, convo, "email priya the numbers"))
    item = db.get(models.WorkItem, res.work_item_id)
    approve(db, user, item)
    drain(coordinator.iter_resume(db, user, item.id))  # what POST /api/chat/continue does
    assert db.get(models.WorkItem, item.id).status == "done"
    assert [n for n in notes(db, user) if n.title == item.title] == []


def test_inbound_item_notifies_when_it_finishes_later(env, monkeypatch):
    db, user, convo, _ = env
    use(monkeypatch, ScriptedProvider([reply("", call("send_email", id="s1", **SEND)),
                                       reply("Sent the Q3 numbers to Priya.")]))
    _, res = drain(coordinator.iter_agent(db, user, convo, "email priya the numbers", trigger="inbound",
                                          origin_id="telegram"))
    item = db.get(models.WorkItem, res.work_item_id)
    assert item.trigger == "inbound" and item.status == "waiting"
    approve(db, user, item)
    assert taskloop.resume_due(db)["finished"] == 1  # no grace for a run nobody is watching
    last = notes(db, user)[-1]
    assert last.title == item.title and "Sent the Q3 numbers" in last.body and last.level == "info"


def test_cron_tick_stops_within_budget(env, monkeypatch):
    db, user, _, eng = env
    items = []
    for i in range(3):
        _, _, item = park_schedule(db, user, monkeypatch, script=[reply("", call("send_email", id=f"s{i}", **SEND))])
        items.append(item)
    for item in items:
        approve(db, user, item)

    # should_stop says no after the first item: the other two stay ready for the next tick
    use(monkeypatch, ScriptedProvider([reply("Done one."), reply("Done two."), reply("Done three.")]))
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 1

    out = taskloop.resume_due(db, should_stop=should_stop)
    assert out["resumed"] == 1
    assert sorted(db.get(models.WorkItem, i.id).status for i in items) == ["done", "ready", "ready"]

    # a segment never runs past the tick: it is handed the time left, and nothing starts without room
    seen = []
    real = coordinator.iter_resume

    def spy(*a, **kw):
        seen.append(kw.get("segment_seconds"))
        return real(*a, **kw)

    monkeypatch.setattr(coordinator, "iter_resume", spy)
    assert taskloop.resume_due(db, time_left=lambda: 5)["resumed"] == 0
    assert taskloop.resume_due(db, time_left=lambda: 60)["resumed"] == 2
    assert seen == [50, 50]

    # the whole tick: a clock that is already past the budget starts nothing
    ticks = iter([0.0] + [10_000.0] * 50)
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=eng, expire_on_commit=False))
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(ticks))
    summary = worker.tick(budget_seconds=240)
    assert summary["stopped_early"] is True and summary["resumed"] == 0 and summary["ok"] is True


def test_tick_resumes_before_anything_else(env, monkeypatch):
    db, user, _, eng = env
    _, _, item = park_schedule(db, user, monkeypatch, script=[
        reply("", call("send_email", id="s1", **SEND)), reply("Sent."),
    ])
    approve(db, user, item)
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=eng, expire_on_commit=False))
    summary = worker.tick()
    assert summary["resumed"] == 1 and summary["ok"] is True and summary["tasks"]["finished"] == 1
    check = sessionmaker(bind=eng)()
    try:
        assert check.get(models.WorkItem, item.id).status == "done"
    finally:
        check.close()


def test_stale_lease_recovered_and_continued(env, monkeypatch):
    db, user, convo, _ = env
    monkeypatch.setattr(settings, "max_agent_steps", 1)
    prov = use(monkeypatch, ScriptedProvider([reply("", call("list_tasks", id="l1")), reply("Checked the list.")]))
    _, res = drain(coordinator.iter_agent(db, user, convo, "go through my tasks", trigger="schedule"))
    item = db.get(models.WorkItem, res.work_item_id)
    # the process that held the lease died: running, lease in the past
    item.status, item.wait_reason = "running", ""
    item.lease_owner, item.lease_until = "dead", utcnow() - timedelta(minutes=5)
    db.commit()
    out = taskloop.resume_due(db)
    assert out["recovered"] == 1 and out["resumed"] == 1
    db.refresh(item)
    assert item.status == "done" and prov.responses == []


def test_expired_approval_resumes_on_the_tick(env, monkeypatch):
    db, user, _, _ = env
    prov, _, item = park_schedule(db, user, monkeypatch, script=[
        reply("", call("send_email", id="s1", **SEND)), reply("Nobody answered, so I did not send it."),
    ])
    pa = pend(db, item)[0]
    pa.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    out = taskloop.resume_due(db)
    assert out["expired"] >= 1 and out["resumed"] == 1
    db.refresh(pa)
    db.refresh(item)
    assert pa.status == "expired" and item.status in ("failed", "partly_done", "done")
    assert json.loads(prov.tool_messages(1)[0]["content"])["status"] == "expired"
    assert "Needs you:" in item.report


def test_a_stuck_item_is_closed_not_retried_forever(env, monkeypatch):
    db, user, convo, _ = env
    monkeypatch.setattr(settings, "max_agent_steps", 1)
    use(monkeypatch, ScriptedProvider([reply("", call("list_tasks", id="l1"))]))
    _, res = drain(coordinator.iter_agent(db, user, convo, "go through my tasks", trigger="schedule"))
    item = db.get(models.WorkItem, res.work_item_id)
    item.segments = settings.max_task_steps + 4
    db.commit()
    out = taskloop.resume_due(db)
    assert out["failed"] == 1 and out["resumed"] == 0
    db.refresh(item)
    assert item.status == "failed" and item.loop_state == {} and item.error


def test_inactive_users_and_held_leases_are_left_alone(env, monkeypatch):
    db, user, convo, _ = env
    monkeypatch.setattr(settings, "max_agent_steps", 1)
    use(monkeypatch, ScriptedProvider([reply("", call("list_tasks", id="l1"))]))
    _, res = drain(coordinator.iter_agent(db, user, convo, "go through my tasks", trigger="schedule"))
    item = db.get(models.WorkItem, res.work_item_id)
    item.lease_until = utcnow() + timedelta(minutes=2)  # another process is on it
    db.commit()
    assert taskloop.resume_due(db)["resumed"] == 0
    item.lease_until = None
    user.is_active = False
    db.commit()
    assert taskloop.resume_due(db)["resumed"] == 0
    assert db.get(models.WorkItem, item.id).status == "ready"
