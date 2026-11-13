"""Pause on approval, resume, stop, expiry (spec 20 section 6, test list in section 14)."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("ALLOW_DEMO_LOGIN", "true")

import json  # noqa: E402
from datetime import timedelta  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import models  # noqa: E402
from app.agent import coordinator, taskloop  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.permissions import approvals  # noqa: E402
from tests.perm_helpers import make_session, make_user  # noqa: E402
from tests.schema import ensure_test_schema  # noqa: E402
from tests.scripted import ScriptedProvider, call, reply  # noqa: E402

SEND = dict(to="priya@example.com", subject="Q3", body="Numbers attached.")


@pytest.fixture
def env(tmp_path):
    eng, db = make_session(tmp_path)
    user = make_user(db, mode="ask", migrated=True)
    convo = models.Conversation(user_id=user.id, title="t")
    db.add(convo)
    db.commit()
    yield db, user, convo
    db.close()
    eng.dispose()


def use(monkeypatch, provider):
    from app.services import mailer

    monkeypatch.setattr(coordinator, "get_provider", lambda: provider)
    monkeypatch.setattr(mailer, "send", lambda db, user, to, subject, body: {"sent": True, "to": to, "route": "test"})
    return provider


def drain(gen):
    events = []
    while True:
        try:
            events.append(next(gen))
        except StopIteration as done:
            return events, done.value


def start(db, user, convo, text="please do it"):
    events, res = drain(coordinator.iter_agent(db, user, convo, text))
    return events, res, db.get(models.WorkItem, res.work_item_id) if res.work_item_id else None


def pend(db, item):
    return list(db.scalars(select(models.PendingAction).where(models.PendingAction.work_item_id == item.id)
                           .order_by(models.PendingAction.created_at, models.PendingAction.id)).all())


def kinds(events):
    return [e["type"] for e in events]


def test_multi_step_task_pauses_on_approval_and_resumes(env, monkeypatch):
    db, user, convo = env
    prov = use(monkeypatch, ScriptedProvider([
        reply("", call("aura_plan", id="p1", title="Send Priya the numbers", task_type="email",
                       steps=["Check tasks", "Send the email"]), call("list_tasks", id="l1")),
        reply("", call("send_email", id="s1", **SEND)),
        None,  # filled below: step 3 after the approval
    ]))
    seen_step3 = {}

    def step3(messages, tools):
        seen_step3["tool"] = [m for m in messages if m.get("role") == "tool" and m["tool_call_id"] == "s1"]
        return reply("", call("aura_check", id="k1", passed=True, method="looked in Sent", evidence="there"))

    prov.responses[2] = step3
    prov.responses.append(reply("Sent the Q3 numbers to Priya.\nDid: emailed priya@example.com."))

    events, res, item = start(db, user, convo, "email Priya the Q3 numbers")
    ks = kinds(events)
    assert "approval_needed" in ks and ks.index("approval_needed") < ks.index("waiting")
    assert item.status == "waiting" and item.wait_reason == "approval" and res.work_status == "waiting"
    assert "I need your go-ahead" in res.text and len(res.pending_actions) == 1
    pa = pend(db, item)[0]
    assert pa.tool_call_id == "s1" and pa.status == "pending" and pa.expires_at is not None

    out = approvals.resolve(db, user, pa.id, "once")
    assert out["work_item"] == {"id": item.id, "status": "ready"}
    db.refresh(pa)
    assert pa.status == "approved" and pa.result is not None  # the hook wrote it

    events2, res2 = drain(coordinator.iter_resume(db, user, item.id))
    db.refresh(item)
    assert item.status == "done" and res2.work_status == "done"
    assert json.loads(seen_step3["tool"][0]["content"])["status"] == "approved_and_run"
    ks2 = kinds(events2)
    assert ks2[0] == "status" or ks2[0] == "task"
    assert next(e for e in events2 if e["type"] == "task")["resumed"] is True
    assert "report" in ks2 and item.segments == 2
    st = db.scalars(select(models.WorkStep).where(models.WorkStep.work_item_id == item.id)
                    .order_by(models.WorkStep.seq)).all()
    assert [s.seq for s in st] == list(range(len(st)))
    assert ("approval", "approved") in [(s.kind, s.status) for s in st]
    assert ("tool", "ok") in [(s.kind, s.status) for s in st if s.tool_name == "send_email"]
    # the model never saw the awaiting_approval placeholder, only the real result
    assert all("awaiting_approval" not in json.dumps(m, default=str) for m in prov.seen[2])

    # the chat transcript: one user message, two assistant messages
    coordinator.persist_exchange(db, convo, "email Priya the Q3 numbers", res)
    coordinator.persist_exchange(db, convo, None, res2)
    roles = [m.role for m in db.scalars(select(models.Message).where(models.Message.conversation_id == convo.id)
                                        .order_by(models.Message.created_at, models.Message.id))]
    assert roles.count("user") == 1 and roles.count("assistant") == 2


def test_decline_is_seen_by_model(env, monkeypatch):
    db, user, convo = env
    prov = use(monkeypatch, ScriptedProvider([
        reply("", call("send_email", id="s1", **SEND)), reply("Understood, I did not send it."),
    ]))
    _, res, item = start(db, user, convo)
    out = approvals.resolve(db, user, pend(db, item)[0].id, "reject")
    assert out["work_item"]["status"] == "ready"
    drain(coordinator.iter_resume(db, user, item.id))
    msg = json.loads(prov.tool_messages(1)[0]["content"])
    assert msg["status"] == "declined_by_user" and "Don't retry" in msg["message"]
    db.refresh(item)
    assert item.status == "failed" and "Needs you:" in item.report


def test_two_pending_in_one_step_resume_once_both_resolved(env, monkeypatch):
    db, user, convo = env
    t1 = models.Task(user_id=user.id, title="Old A")
    t2 = models.Task(user_id=user.id, title="Old B")
    db.add_all([t1, t2])
    db.commit()
    prov = use(monkeypatch, ScriptedProvider([
        reply("", call("delete_task", id="d1", task_id=t1.id), call("list_tasks", id="r1"),
              call("delete_task", id="d2", task_id=t2.id)),
        reply("Both deleted."),
    ]))
    events, res, item = start(db, user, convo)
    assert kinds(events).count("approval_needed") == 2
    waiting = next(e for e in events if e["type"] == "waiting")
    assert len(waiting["pending_action_ids"]) == 2
    a, b = pend(db, item)
    assert approvals.resolve(db, user, b.id, "once")["work_item"]["status"] == "waiting"
    db.refresh(item)
    with pytest.raises(taskloop.Busy):
        taskloop.claim_for_resume(db, user, item.id)
    assert approvals.resolve(db, user, a.id, "once")["work_item"]["status"] == "ready"
    drain(coordinator.iter_resume(db, user, item.id))
    msgs = prov.tool_messages(1)
    assert [m["tool_call_id"] for m in msgs] == ["d1", "r1", "d2"]  # original order
    assert json.loads(msgs[0]["content"])["status"] == "approved_and_run"
    assert db.get(models.Task, t1.id) is None and db.get(models.Task, t2.id) is None


def test_expired_action_resumes_with_expiry(env, monkeypatch):
    db, user, convo = env
    prov = use(monkeypatch, ScriptedProvider([
        reply("", call("send_email", id="s1", **SEND)), reply("It was not answered, so nothing was sent."),
    ]))
    _, _, item = start(db, user, convo)
    pa = pend(db, item)[0]
    pa.expires_at = models.utcnow() - timedelta(minutes=1)
    db.commit()
    assert approvals.expire_due(db) == 1
    db.refresh(item)
    assert item.status == "ready"
    drain(coordinator.iter_resume(db, user, item.id))
    assert json.loads(prov.tool_messages(1)[0]["content"])["status"] == "expired"
    db.refresh(item)
    assert item.status == "failed"
    assert any(s.kind == "approval" and s.status == "expired"
               for s in db.scalars(select(models.WorkStep).where(models.WorkStep.work_item_id == item.id)))


def test_continue_is_idempotent(env, monkeypatch):
    db, user, convo = env
    use(monkeypatch, ScriptedProvider([reply("", call("send_email", id="s1", **SEND)), reply("ok")]))
    _, _, item = start(db, user, convo)
    approvals.resolve(db, user, pend(db, item)[0].id, "reject")
    token = taskloop.claim_for_resume(db, user, item.id)
    with pytest.raises(taskloop.Busy) as busy:
        taskloop.claim_for_resume(db, user, item.id)
    assert busy.value.status == "running"
    drain(coordinator.iter_resume(db, user, item.id, token=token))
    with pytest.raises(taskloop.Busy) as busy:
        taskloop.claim_for_resume(db, user, item.id)
    assert busy.value.status == "failed"


def test_double_decision_runs_tool_once(env, monkeypatch):
    db, user, convo = env
    t = models.Task(user_id=user.id, title="Doomed")
    db.add(t)
    db.commit()
    use(monkeypatch, ScriptedProvider([reply("", call("delete_task", id="d1", task_id=t.id))]))
    _, _, item = start(db, user, convo)
    pa = pend(db, item)[0]
    approvals.resolve(db, user, pa.id, "once")
    with pytest.raises(approvals.ApprovalError) as err:
        approvals.resolve(db, user, pa.id, "once")
    assert err.value.status_code == 409
    runs = db.scalars(select(models.ActivityLog).where(models.ActivityLog.action == "delete_task")).all()
    assert len(runs) == 1
    steps = db.scalars(select(models.WorkStep).where(models.WorkStep.work_item_id == item.id,
                                                     models.WorkStep.kind == "tool")).all()
    assert len(steps) == 1


def test_crash_mid_step_marks_interrupted(env, monkeypatch):
    db, user, convo = env
    row = db.scalars(select(models.UserSettings)).first()
    row.permission_mode = "skip"
    db.commit()
    prov = use(monkeypatch, ScriptedProvider([
        reply("", call("create_task", id="c1", title="A"), call("create_task", id="c2", title="B")),
        reply("Checked what happened."),
    ]))
    gen = coordinator.iter_agent(db, user, convo, "add two tasks")
    while next(gen)["type"] != "step_started":
        pass
    gen.close()  # the page went away while the first call was in flight
    item = db.scalars(select(models.WorkItem)).one()
    assert item.status == "ready" and item.wait_reason == "interrupted"
    drain(coordinator.iter_resume(db, user, item.id))
    msgs = {m["tool_call_id"]: json.loads(m["content"]) for m in prov.tool_messages(1)}
    assert msgs["c1"]["status"] == "interrupted" and msgs["c2"]["status"] == "interrupted"
    sts = [s.status for s in db.scalars(select(models.WorkStep).where(models.WorkStep.work_item_id == item.id,
                                                                      models.WorkStep.kind == "tool"))]
    assert "interrupted" in sts


def test_stale_lease_recovered(env, monkeypatch):
    db, user, convo = env
    use(monkeypatch, ScriptedProvider([reply("", call("create_task", title="A"))]))
    gen = coordinator.iter_agent(db, user, convo, "add a task")
    next(gen)
    next(gen)
    item = db.scalars(select(models.WorkItem)).one()
    assert item.status == "running"
    item.lease_until = models.utcnow() - timedelta(seconds=1)
    db.commit()
    assert taskloop.running_item(db, convo.id) is None
    assert taskloop.recover_stale(db) == 1
    db.refresh(item)
    assert item.status == "ready" and item.wait_reason == "interrupted"


def test_stop_running_item(env, monkeypatch):
    db, user, convo = env
    row = db.scalars(select(models.UserSettings)).first()
    row.permission_mode = "skip"
    db.commit()
    use(monkeypatch, ScriptedProvider([
        reply("", call("create_task", id="c1", title="A"), call("create_task", id="c2", title="B")),
        reply("never"),
    ]))
    gen = coordinator.iter_agent(db, user, convo, "add two tasks")
    events = []
    while True:
        e = next(gen)
        events.append(e)
        if e["type"] == "step_finished":
            break
    item = db.scalars(select(models.WorkItem)).one()
    assert taskloop.stop(db, user, item)["status"] == "running"  # asked, not yet stopped
    rest, res = drain(gen)
    db.refresh(item)
    assert item.status == "stopped" and res.work_status == "stopped"
    assert [t.title for t in db.scalars(select(models.Task))] == ["A"]  # the second call did not run
    assert "Stopped" in res.text and rest[-1]["status"] == "stopped"


def test_stop_waiting_item_rejects_pending(env, monkeypatch):
    db, user, convo = env
    use(monkeypatch, ScriptedProvider([reply("", call("send_email", id="s1", **SEND))]))
    _, _, item = start(db, user, convo)
    pa = pend(db, item)[0]
    out = taskloop.stop(db, user, item)
    assert out["status"] == "stopped"
    db.refresh(pa)
    assert pa.status == "rejected"
    with pytest.raises(approvals.ApprovalError):
        approvals.resolve(db, user, pa.id, "once")
    last = db.scalars(select(models.Message).where(models.Message.conversation_id == convo.id)
                      .order_by(models.Message.created_at.desc())).first()
    assert last.content == "Stopped. Nothing further was done." and last.meta["work_status"] == "stopped"
    assert taskloop.stop(db, user, item)["status"] == "stopped"  # a finished item is left alone


def test_screenshot_not_persisted_in_loop_state(env, monkeypatch):
    db, user, convo = env
    row = db.scalars(select(models.UserSettings)).first()
    row.permission_mode = "skip"
    db.commit()
    real = coordinator.execute_tool

    def fake(db_, user_, name, args, **kw):
        if name == "create_task":
            return {"message": "shot", "image": {"media_type": "image/png", "data": "QUJD" * 50}}
        return real(db_, user_, name, args, **kw)

    monkeypatch.setattr(coordinator, "execute_tool", fake)
    monkeypatch.setattr(settings, "max_agent_steps", 1)
    prov = use(monkeypatch, ScriptedProvider([reply("", call("create_task", id="c1", title="A")), reply("fine")]))
    _, res, item = start(db, user, convo)
    assert item.status == "ready"
    assert "QUJDQUJD" not in json.dumps(item.loop_state)
    assert "screenshot taken earlier" in json.dumps(item.loop_state)
    drain(coordinator.iter_resume(db, user, item.id))


def test_new_message_409_while_running(env, monkeypatch):
    db, user, convo = env
    row = db.scalars(select(models.UserSettings)).first()
    row.permission_mode = "skip"
    db.commit()
    use(monkeypatch, ScriptedProvider([reply("", call("create_task", id="c1", title="A")), reply("x")]))
    gen = coordinator.iter_agent(db, user, convo, "add a task")
    next(gen)
    next(gen)
    held = taskloop.running_item(db, convo.id)
    assert held is not None and held.status == "running"
    gen.close()
    assert taskloop.running_item(db, convo.id) is None  # parked, no longer holding the conversation


# ---------------------------------------------------------------------------
# Through HTTP: decision, continue, stop and the 409 answers
# ---------------------------------------------------------------------------

EMAIL = "loop-resume@example.com"


@pytest.fixture(scope="module")
def client():
    ensure_test_schema(engine)
    with TestClient(app) as c:
        yield c
    db = SessionLocal()
    try:
        for row in db.query(models.User).filter(models.User.email == EMAIL).all():
            db.delete(row)
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module")
def auth(client):
    resp = client.post("/api/auth/demo", json={"email": EMAIL, "name": "Loop"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def sse(resp):
    return [json.loads(l[6:]) for l in resp.text.splitlines() if l.startswith("data: ") and l != "data: [DONE]"]


def test_http_pause_decide_continue_and_409s(client, auth, monkeypatch):
    prov = use(monkeypatch, ScriptedProvider([
        reply("", call("send_email", id="s1", **SEND)), reply("Sent it. Checked: in Sent."),
    ]))
    first = sse(client.post("/api/chat/stream", headers=auth, json={"message": "email priya the numbers"}))
    done = first[-1]
    assert done["type"] == "done" and done["work_status"] == "waiting" and done["work_item_id"]
    item_id = done["work_item_id"]
    # not ready yet: 409 with the body the client reads
    r = client.post("/api/chat/continue", headers=auth, json={"work_item_id": item_id})
    assert r.status_code == 409 and r.json()["status"] == "waiting" and r.json()["work_item_id"] == item_id
    pending = [a for a in client.get("/api/pending-actions", headers=auth).json() if a["work_item_id"] == item_id]
    assert len(pending) == 1 and pending[0]["conversation_id"] == done["conversation_id"]
    dec = client.post(f"/api/pending-actions/{pending[0]['id']}", headers=auth, json={"decision": "once"})
    assert dec.status_code == 200 and dec.json()["work_item"] == {"id": item_id, "status": "ready"}
    again = client.post(f"/api/pending-actions/{pending[0]['id']}", headers=auth, json={"decision": "once"})
    assert again.status_code == 409
    cont = client.post("/api/chat/continue", headers=auth, json={"work_item_id": item_id})
    assert cont.status_code == 200
    ev = sse(cont)
    assert ev[0]["type"] == "start" and ev[-1]["type"] == "done" and ev[-1]["work_status"] in ("done", "partly_done")
    assert client.post("/api/chat/continue", headers=auth, json={"work_item_id": item_id}).status_code == 409
    msgs = client.get(f"/api/chat/conversations/{done['conversation_id']}", headers=auth).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "assistant"]
    # someone else's item is a 404
    assert client.post("/api/chat/continue", headers=auth, json={"work_item_id": "nope"}).status_code == 404
    assert client.post("/api/chat/stop", headers=auth, json={"work_item_id": "nope"}).status_code == 404


def test_http_stop_and_running_409(client, auth, monkeypatch):
    use(monkeypatch, ScriptedProvider([reply("", call("send_email", id="s1", **SEND))]))
    first = sse(client.post("/api/chat/stream", headers=auth, json={"message": "email priya again"}))
    done = first[-1]
    stop = client.post("/api/chat/stop", headers=auth, json={"work_item_id": done["work_item_id"]})
    assert stop.status_code == 200 and stop.json() == {"id": done["work_item_id"], "status": "stopped"}
    # a live lease on the conversation blocks a new message
    db = SessionLocal()
    try:
        item = db.get(models.WorkItem, done["work_item_id"])
        item2 = models.WorkItem(user_id=item.user_id, conversation_id=item.conversation_id, instruction="x",
                                title="x", status="running", lease_until=models.utcnow() + timedelta(minutes=2))
        db.add(item2)
        db.commit()
        item2_id = item2.id
    finally:
        db.close()
    r = client.post("/api/chat/stream", headers=auth,
                    json={"message": "hello", "conversation_id": done["conversation_id"]})
    assert r.status_code == 409 and r.json()["work_item_id"] == item2_id and r.json()["status"] == "running"
    r = client.post("/api/chat", headers=auth, json={"message": "hello", "conversation_id": done["conversation_id"]})
    assert r.status_code == 409
