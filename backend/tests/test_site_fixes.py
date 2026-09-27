"""Regressions from the September 2026 audit of the live site.

Each test pins a bug that was visible in the product:

* toggling or teaching a skill answered 500 after the change had already saved;
* a streamed chat never got a title ("New conversation" forever);
* demo-mode replies were raw JSON, and one of the chat's own suggestion chips
  listed calendar events instead of saving the preference it asked to save;
* the growth bar sat at 0% for as long as any one signal was zero;
* "today", "tomorrow 5pm", greetings and free slots were all computed in UTC.

Run from backend/:  pytest -q tests/test_site_fixes.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Must match test_smoke.py exactly — see the note in test_password_auth.py.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("ALLOW_DEMO_LOGIN", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import timeutil  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
EMAILS = (
    "fixes-demo@example.com",
    "fixes-ist@example.com",
    "fixes-signup@example.com",
    "fixes-badzone@example.com",
)


def _purge() -> None:
    from app.models import User

    db = SessionLocal()
    try:
        for row in db.query(User).filter(User.email.in_(EMAILS)).all():
            db.delete(row)
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module")
def client():
    # Same shape as test_password_auth.py: schema if absent, only our rows,
    # and no lifespan (test_smoke.py already runs it in this process).
    Base.metadata.create_all(bind=engine)
    _purge()
    c = TestClient(app)
    try:
        yield c
    finally:
        c.close()
        _purge()


def _demo(client, email: str, tz: str = "") -> dict:
    resp = client.post(
        "/api/auth/demo", json={"email": email, "name": "Fix Test", "timezone": tz}
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _stream(client, headers: dict, message: str, conversation_id: str | None = None):
    resp = client.post(
        "/api/chat/stream",
        headers=headers,
        json={"message": message, "conversation_id": conversation_id},
    )
    assert resp.status_code == 200, resp.text
    events = []
    for frame in resp.text.split("\n\n"):
        frame = frame.strip()
        if frame.startswith("data:") and frame[5:].strip() != "[DONE]":
            events.append(json.loads(frame[5:].strip()))
    text = "".join(e["value"] for e in events if e["type"] == "delta")
    return events, text


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


def test_toggling_a_skill_answers_200_with_the_skill(client):
    auth = _demo(client, "fixes-demo@example.com")
    off = client.patch("/api/skills/TK01", headers=auth, json={"enabled": False})
    assert off.status_code == 200, off.text
    assert off.json()["code"] == "TK01" and off.json()["enabled"] is False

    on = client.patch("/api/skills/tk01", headers=auth, json={"enabled": True})
    assert on.status_code == 200, on.text
    assert on.json()["enabled"] is True


def test_teaching_answers_200_and_stores_one_lesson(client):
    auth = _demo(client, "fixes-demo@example.com")
    resp = client.post(
        "/api/skills/CA01/teach", headers=auth, json={"note": "Keep Fridays clear"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["learned_notes"].count("Keep Fridays clear") == 1


def test_a_skill_from_an_uninstalled_plugin_is_a_clean_404(client):
    auth = _demo(client, "fixes-demo@example.com")
    client.post("/api/plugins/research/uninstall", headers=auth)
    resp = client.post("/api/skills/RS01/teach", headers=auth, json={"note": "Cite sources"})
    assert resp.status_code == 404
    assert "isn't installed" in resp.json()["detail"]
    assert client.patch(
        "/api/skills/RS01", headers=auth, json={"enabled": False}
    ).status_code == 404
    assert client.patch(
        "/api/skills/NOPE1", headers=auth, json={"enabled": False}
    ).status_code == 404


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


def test_a_streamed_chat_gets_a_title_and_keeps_its_messages(client):
    auth = _demo(client, "fixes-demo@example.com")
    events, _ = _stream(client, auth, "Add a task to renew the domain")
    assert events[0]["type"] == "start"
    assert events[-1]["type"] == "done"
    convo_id = events[0]["conversation_id"]

    convo = client.get(f"/api/chat/conversations/{convo_id}", headers=auth).json()
    assert convo["title"] == "Add a task to renew the domain"
    assert [m["role"] for m in convo["messages"]] == ["user", "assistant"]
    assert convo["updated_at"] > convo["created_at"]


def test_demo_mode_replies_read_as_sentences(client):
    auth = _demo(client, "fixes-demo@example.com")
    for prompt in (
        "What does my day look like?",
        "Summarise my inbox",
        "When am I free for 45 minutes this week?",
    ):
        _, text = _stream(client, auth, prompt)
        assert text.strip(), prompt
        assert "```" not in text and '"start_at"' not in text, (prompt, text[:300])


def test_the_remember_chip_saves_a_memory_instead_of_listing_events(client):
    auth = _demo(client, "fixes-demo@example.com")
    events, text = _stream(client, auth, "Remember that I prefer afternoon meetings")
    codes = [e["code"] for e in events if e["type"] == "skill"]
    assert "CA01" not in codes
    memories = [m["content"] for m in client.get("/api/memories", headers=auth).json()]
    assert any("afternoon meetings" in m for m in memories), memories
    assert "remember" in text.lower()


def test_free_time_honours_the_requested_length():
    from app.llm import MockProvider

    assert MockProvider._duration_minutes("free for 45 minutes this week?") == 45
    assert MockProvider._duration_minutes("an hour tomorrow") == 60
    assert MockProvider._duration_minutes("1.5 hours") == 90
    assert MockProvider._duration_minutes("half an hour") == 30
    assert MockProvider._duration_minutes("when am I free") is None


# ---------------------------------------------------------------------------
# Time zones
# ---------------------------------------------------------------------------


def test_relative_times_are_read_on_the_users_clock():
    from app.agent.tools import parse_when

    due = parse_when("tomorrow 5pm", default_hour=17, tz=IST)
    local = due.astimezone(IST)
    assert (local.hour, local.minute) == (17, 0)
    assert local.date() == datetime.now(IST).date() + timedelta(days=1)
    assert due.tzinfo == timezone.utc  # stored as UTC

    # A timestamp without an offset is a wall-clock time for the user.
    naive = parse_when("2026-10-01T09:00:00", tz=IST)
    assert naive == datetime(2026, 10, 1, 3, 30, tzinfo=timezone.utc)
    # One with an offset is left alone.
    assert parse_when("2026-10-01T09:00:00Z", tz=IST) == datetime(
        2026, 10, 1, 9, 0, tzinfo=timezone.utc
    )
    # The longer phrase wins.
    later = parse_when("day after tomorrow", tz=IST).astimezone(IST)
    assert later.date() == datetime.now(IST).date() + timedelta(days=2)


def test_a_local_day_starts_at_local_midnight():
    ref = datetime(2026, 9, 26, 20, 0, tzinfo=timezone.utc)  # 01:30 on the 27th in IST
    start, end = timeutil.day_bounds(IST, ref)
    assert start == datetime(2026, 9, 26, 18, 30, tzinfo=timezone.utc)
    assert end - start == timedelta(days=1)
    assert timeutil.zone("Not/AZone") == timezone.utc
    assert timeutil.is_valid_zone("Asia/Kolkata") and not timeutil.is_valid_zone("Mars/Base")


def test_free_slots_start_at_nine_in_the_morning_locally(client):
    auth = _demo(client, "fixes-ist@example.com", tz="Asia/Kolkata")
    slots = client.get(
        "/api/events/free-slots", headers=auth, params={"duration_minutes": 30, "days": 7}
    ).json()
    assert slots
    now = datetime.now(timezone.utc)
    for slot in slots:
        start = datetime.fromisoformat(slot["start_at"]).astimezone(IST)
        assert start >= now - timedelta(minutes=1)
        assert 9 <= start.hour < 18, slot  # the user's working day, not UTC's
        assert start.weekday() < 5


def test_sample_meetings_are_seeded_on_the_users_clock(client):
    auth = _demo(client, "fixes-ist@example.com", tz="Asia/Kolkata")
    events = client.get("/api/events", headers=auth, params={"days": 7}).json()
    standup = next(e for e in events if e["title"] == "Standup")
    local = datetime.fromisoformat(standup["start_at"])
    local = (local if local.tzinfo else local.replace(tzinfo=timezone.utc)).astimezone(IST)
    assert (local.hour, local.minute) == (9, 0)


def test_the_zone_is_stored_at_sign_up_and_can_be_updated(client):
    resp = client.post(
        "/api/auth/register",
        json={
            "email": "fixes-badzone@example.com",
            "password": "long-enough-password",
            "timezone": "Mars/Olympus_Mons",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["timezone"] == "UTC"  # unknown zone -> UTC
    auth = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    ok = client.patch("/api/auth/me", headers=auth, json={"timezone": "Asia/Kolkata"})
    assert ok.status_code == 200 and ok.json()["timezone"] == "Asia/Kolkata"
    bad = client.patch("/api/auth/me", headers=auth, json={"timezone": "Nowhere/Special"})
    assert bad.status_code == 422
    assert client.patch("/api/auth/me", headers=auth, json={"name": "  "}).status_code == 422


def test_the_prompt_tells_the_model_the_users_local_time():
    from app.agent.prompts import build_system_prompt

    ref = datetime(2026, 9, 26, 17, 0, tzinfo=timezone.utc)
    prompt = build_system_prompt("Uday", "u@example.com", "Asia/Kolkata", now=ref)
    assert "22:30 (Asia/Kolkata)" in prompt
    assert "(timezone: Asia/Kolkata)" in prompt


# ---------------------------------------------------------------------------
# Growth
# ---------------------------------------------------------------------------


def test_progress_moves_even_while_one_signal_is_zero():
    from app.services import identity

    stranger = identity.STAGES[0]
    progress = identity.progress_to_next(
        {"interactions": 3, "memories": 0, "actions": 7}, stranger
    )
    assert 0 < progress["percent"] < 100
    assert progress["needs"] == {"interactions": 2, "memories": 3, "actions": 0}
    done = identity.progress_to_next(
        {"interactions": 99, "memories": 2, "actions": 99}, stranger
    )
    assert done["percent"] < 100  # a stage not yet reached never reads 100%
