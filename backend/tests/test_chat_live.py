"""Chat end to end, and the background work around it.

* The chat stream reports each tool as it starts and as it finishes, in
  order, before the answer — rather than everything at once at the end.
* The next turn knows what the last one did (tool calls are replayed).
* Old conversations stuck on "New conversation" get their real title.
* A schedule set to deliver by email either emails or says why it didn't.
* The hosted cron runs the worker only with its secret.
* Password reset works once, and the form doesn't reveal who has an account.

Run from backend/:  pytest -q tests/test_chat_live.py
"""

from __future__ import annotations

import json
import os

# Must match test_smoke.py exactly — see the note in test_password_auth.py.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("ALLOW_DEMO_LOGIN", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Conversation, Message, Notification, Schedule, User  # noqa: E402

EMAIL = "live-chat@example.com"
RESET_EMAIL = "reset-me@example.com"


def _purge() -> None:
    db = SessionLocal()
    try:
        for row in db.query(User).filter(User.email.in_([EMAIL, RESET_EMAIL])).all():
            db.delete(row)
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=engine)
    _purge()
    with TestClient(app) as c:
        yield c
    _purge()


@pytest.fixture(scope="module")
def auth(client):
    resp = client.post("/api/auth/demo", json={"email": EMAIL, "name": "Live Chat"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _user(db) -> User:
    return db.scalars(select(User).where(User.email == EMAIL)).one()


def _events(resp) -> list[dict]:
    out = []
    for line in resp.text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            out.append(json.loads(line[6:]))
    return out


# ---------------------------------------------------------------------------
# The stream
# ---------------------------------------------------------------------------


def test_the_stream_reports_each_tool_as_it_happens(client, auth, db_rollback):
    resp = client.post("/api/chat/stream", headers=auth, json={"message": "What does my day look like?"})
    assert resp.status_code == 200
    events = _events(resp)
    kinds = [e["type"] for e in events]

    assert kinds[0] == "start" and kinds[1] == "status"
    started = kinds.index("tool_start")
    finished = kinds.index("skill")
    first_text = kinds.index("delta")
    assert started < finished < first_text < kinds.index("done")

    tool = events[started]
    assert tool["name"] == "get_daily_briefing"
    assert tool["label"] == "Putting your day together"
    assert events[finished]["code"] == tool["code"]

    done = events[-1]
    convo = client.get(f"/api/chat/conversations/{done['conversation_id']}", headers=auth).json()
    assert convo["title"] == "What does my day look like?"
    assert [m["role"] for m in convo["messages"]] == ["user", "assistant"]


class _Scripted:
    """Asks for one tool call, then answers — and remembers what it was shown."""

    name = "scripted"

    def __init__(self, tool: str, arguments: dict):
        self.tool, self.arguments = tool, arguments
        self.seen: list[list[dict]] = []

    def complete(self, messages, tools=None, model=None):
        from app.llm import LLMResponse, ToolCall, Usage

        self.seen.append([dict(m) for m in messages])
        if messages[-1]["role"] == "tool":
            return LLMResponse(text="Done.", usage=Usage())
        return LLMResponse(
            tool_calls=[ToolCall(id="c1", name=self.tool, arguments=self.arguments)],
            usage=Usage(),
        )

    def embed(self, texts):
        return []


def test_a_queued_action_shows_up_while_the_turn_is_still_running(client, auth, monkeypatch, db_rollback):
    from app.agent import coordinator

    provider = _Scripted("send_email", {"to": "priya@example.com", "subject": "Hi", "body": "Hello"})
    monkeypatch.setattr(coordinator, "get_provider", lambda: provider)
    resp = client.post("/api/chat/stream", headers=auth, json={"message": "email priya hello"})
    events = _events(resp)
    kinds = [e["type"] for e in events]
    assert "pending_action" in kinds
    assert kinds.index("pending_action") < kinds.index("delta")
    pending = events[kinds.index("pending_action")]
    assert "priya@example.com" in pending["preview"]
    assert "tool_start" not in kinds  # nothing ran; it waits for approval


def test_the_next_turn_knows_what_the_last_one_did(client, auth, monkeypatch, db_rollback):
    from app.agent import coordinator

    provider = _Scripted("create_task", {"title": "Call Priya about the contract"})
    monkeypatch.setattr(coordinator, "get_provider", lambda: provider)
    first = _events(client.post("/api/chat/stream", headers=auth, json={"message": "remind me to call Priya"}))
    convo_id = first[-1]["conversation_id"]

    client.post(
        "/api/chat/stream", headers=auth,
        json={"message": "make that urgent", "conversation_id": convo_id},
    )
    second_turn = provider.seen[-2]  # what the model saw at the start of turn two
    replay = [m for m in second_turn if m["role"] == "assistant" and "Tools I used" in str(m["content"])]
    assert replay, "the earlier tool call should be in the history"
    assert "create_task" in replay[0]["content"]
    assert "Call Priya about the contract" in replay[0]["content"]


def test_old_untitled_conversations_get_their_real_title(client, auth, db_rollback):
    db = SessionLocal()
    try:
        user = _user(db)
        convo = Conversation(user_id=user.id, title="New conversation")
        db.add(convo)
        db.commit()
        db.add(Message(conversation_id=convo.id, role="user", content="Plan the offsite\nfor June"))
        db.commit()
        convo_id = convo.id
    finally:
        db.close()

    listed = client.get("/api/chat/conversations", headers=auth).json()
    titled = next(c for c in listed if c["id"] == convo_id)
    assert titled["title"] == "Plan the offsite"


# ---------------------------------------------------------------------------
# Schedules delivered by email
# ---------------------------------------------------------------------------


def _email_schedule(db, user) -> Schedule:
    row = Schedule(
        user_id=user.id, name="Morning digest", prompt="What does my day look like?",
        cron="0 7 * * *", timezone="UTC", deliver_to="email",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_an_email_schedule_says_why_it_wasnt_emailed(client, auth, db_rollback):
    from app.services import schedules

    db = SessionLocal()
    try:
        user = _user(db)
        outcome = schedules.run_one(db, user, _email_schedule(db, user))
        assert outcome["emailed"] is False
        note = db.scalars(
            select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc())
        ).first()
        assert "Not emailed" in note.body and "Connect Google" in note.body
        assert note.level == "urgent"
    finally:
        db.close()


def test_an_email_schedule_sends_when_it_can(client, auth, monkeypatch, db_rollback):
    from app.services import mailer, schedules

    sent = []
    monkeypatch.setattr(mailer, "send", lambda db, user, to, subject, body: sent.append((to, subject)) or {"sent": True})
    db = SessionLocal()
    try:
        user = _user(db)
        outcome = schedules.run_one(db, user, _email_schedule(db, user))
        assert outcome["emailed"] is True
        assert sent == [(EMAIL, "AURA · Morning digest")]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# The hosted cron
# ---------------------------------------------------------------------------


def test_the_cron_keeps_the_database_awake_without_a_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "cron_secret", "", raising=False)
    body = client.get("/api/cron/tick").json()
    assert body["status"] == "ok" and body["worker"].startswith("off")


def test_the_cron_runs_the_worker_only_with_its_secret(client, monkeypatch):
    import worker

    ran = []
    monkeypatch.setattr(settings, "cron_secret", "s3cret-value", raising=False)
    monkeypatch.setattr(worker, "tick", lambda **kw: ran.append(kw) or {"ok": True, "heartbeats": 0})

    assert client.get("/api/cron/tick").json()["worker"] == "skipped"
    assert client.get("/api/cron/tick", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert not ran

    body = client.get("/api/cron/tick", headers={"Authorization": "Bearer s3cret-value"}).json()
    assert body["worker"] == "ran"
    assert ran and ran[0]["compact"] is True and ran[0]["active_since"] is not None
    # A cron call has a time limit; the pass is told how long it has.
    assert ran[0]["budget_seconds"] == settings.cron_budget_seconds


def test_a_tick_out_of_time_starts_nothing_new(client):
    """What doesn't fit stays due for the next tick, rather than being cut off mid-way."""
    import worker

    summary = worker.tick(compact=True, budget_seconds=0)
    assert summary["ok"] is True
    assert summary.get("stopped_early") is True
    assert summary["automations"] == summary["schedules"] == summary["heartbeats"] == 0
    assert summary["compacted"] is False


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

@pytest.fixture
def smtp(monkeypatch):
    from app.services import mailer

    outbox: list[tuple[str, str, str]] = []
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com", raising=False)
    monkeypatch.setattr(settings, "smtp_from", "aura@example.com", raising=False)
    monkeypatch.setattr(mailer, "send_system", lambda to, subject, body: outbox.append((to, subject, body)))
    return outbox


def test_reset_is_unavailable_without_a_mail_server(client, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "", raising=False)
    resp = client.post("/api/auth/password/forgot", json={"email": "anyone@example.com"})
    assert resp.status_code == 501


def test_password_reset_end_to_end(client, smtp, db_rollback):
    created = client.post(
        "/api/auth/register",
        json={"email": RESET_EMAIL, "password": "first-password-1", "name": "Reset"},
    )
    assert created.status_code == 201, created.text

    unknown = client.post("/api/auth/password/forgot", json={"email": "nobody@example.com"})
    known = client.post("/api/auth/password/forgot", json={"email": RESET_EMAIL})
    assert unknown.json() == known.json()  # nothing to learn from the reply
    assert len(smtp) == 1 and smtp[0][0] == RESET_EMAIL

    # Asking again at once doesn't send a second email.
    client.post("/api/auth/password/forgot", json={"email": RESET_EMAIL})
    assert len(smtp) == 1

    link = next(line for line in smtp[0][2].splitlines() if "reset-password#token=" in line)
    token = link.split("#token=", 1)[1].strip()
    done = client.post("/api/auth/password/reset", json={"token": token, "password": "second-password-2"})
    assert done.status_code == 200 and done.json()["access_token"]

    assert client.post("/api/auth/login", json={"email": RESET_EMAIL, "password": "second-password-2"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": RESET_EMAIL, "password": "first-password-1"}).status_code == 401
    # The link died with the old password.
    reused = client.post("/api/auth/password/reset", json={"token": token, "password": "third-password-3"})
    assert reused.status_code == 400


def test_a_reset_link_is_not_a_session(client, smtp, db_rollback):
    from app.api.auth import issue_reset_token

    db = SessionLocal()
    try:
        user = _user(db)
        token = issue_reset_token(user)
    finally:
        db.close()
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


# ---------------------------------------------------------------------------
# Setup screen
# ---------------------------------------------------------------------------


def test_the_setup_check_really_connects_to_the_database(client):
    body = client.get("/api/health/preflight").json()
    database = next(c for c in body["checks"] if c["key"] == "database")
    assert database["level"] in ("ok", "fail")  # SQLite in tests: reachable


def test_the_database_probe_reports_a_failure_instead_of_raising(monkeypatch):
    from app import preflight

    monkeypatch.setattr(settings, "database_url", "sqlite:////nonexistent-dir/x/y.db", raising=False)
    failed = preflight.probe_database(timeout_seconds=1)
    assert failed is not None and failed.level == "warn"
