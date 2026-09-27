"""Regressions from the September 2026 audit of the live site.

Each test pins a bug that was visible in the product:

* toggling or teaching a skill answered 500 after the change had already saved;
* a streamed chat never got a title ("New conversation" forever);
* demo-mode replies were raw JSON, and one of the chat's own suggestion chips
  listed calendar events instead of saving the preference it asked to save;
* "today", "tomorrow 5pm", greetings and free slots were all computed in UTC;
* password sign-ups landed in an empty app although the landing page promises
  sample data — and the sample data, once there, couldn't be removed;
* the growth bar sat at 0% for as long as any one signal was zero.

The later tests pin what an independent review of those fixes turned up:
removing sample data also deleted the user's own look-alike rows, a region
name such as "America" was a 500, schedules fired on UTC's clock, and demo mode
saved "Do you remember…?" as a memory and read the model's own instructions
aloud when an action was queued for approval.

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
    "fixes-keep@example.com",
    "fixes-legacy@example.com",
    "fixes-sched@example.com",
    "fixes-region@example.com",
    "fixes-slots@example.com",
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
# Sample data
# ---------------------------------------------------------------------------


def test_password_sign_up_gets_sample_data_and_can_remove_it(client):
    resp = client.post(
        "/api/auth/register",
        json={
            "email": "fixes-signup@example.com",
            "password": "long-enough-password",
            "name": "New Person",
            "timezone": "Asia/Kolkata",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["timezone"] == "Asia/Kolkata"
    auth = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    assert len(client.get("/api/emails", headers=auth).json()) > 0
    status = client.get("/api/settings/demo-data", headers=auth).json()
    assert status["present"] is True

    mine = client.post(
        "/api/tasks", headers=auth, json={"title": "My own real task", "priority": "high"}
    ).json()

    cleared = client.delete("/api/settings/demo-data", headers=auth)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["total"] > 0

    assert client.get("/api/emails", headers=auth).json() == []
    assert client.get("/api/settings/demo-data", headers=auth).json()["present"] is False
    titles = [t["title"] for t in client.get("/api/tasks", headers=auth).json()]
    assert titles == [mine["title"]]  # the user's own task survives
    assert client.get("/api/documents", headers=auth).json() == []
    # Idempotent.
    assert client.delete("/api/settings/demo-data", headers=auth).json()["total"] == 0


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


# ---------------------------------------------------------------------------
# Review follow-ups
# ---------------------------------------------------------------------------


def _user(email: str):
    from app.models import User

    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == email).one()
    finally:
        db.close()


def test_removing_sample_data_keeps_the_users_own_lookalikes(client):
    auth = _demo(client, "fixes-keep@example.com", tz="Asia/Kolkata")
    soon = datetime.now(timezone.utc) + timedelta(days=1)

    # The user's own rows, named exactly like sample ones.
    standup = client.post(
        "/api/events", headers=auth,
        json={"title": "Standup", "start_at": soon.isoformat(),
              "end_at": (soon + timedelta(minutes=15)).isoformat()},
    )
    assert standup.status_code == 200, standup.text
    board = client.post(
        "/api/tasks", headers=auth, json={"title": "Draft Q3 board update", "tags": ["board"]}
    ).json()
    memory = client.post(
        "/api/memories", headers=auth,
        json={"content": "Wants email replies drafted short.", "kind": "preference"},
    ).json()
    # …and one of theirs filed under a sample task.
    sample_parent = next(
        t for t in client.get("/api/tasks", headers=auth).json()
        if t["title"] == "Sign and return Series A term sheet"
    )
    subtask = client.post(
        "/api/tasks", headers=auth,
        json={"title": "Ask Priya about the option pool", "parent_id": sample_parent["id"]},
    ).json()

    cleared = client.delete("/api/settings/demo-data", headers=auth)
    assert cleared.status_code == 200, cleared.text
    removed = cleared.json()["removed"]
    assert removed["events"] == 7 and removed["tasks"] == 6 and removed["emails"] == 7

    events = client.get("/api/events", headers=auth, params={"days": 14}).json()
    assert [e["id"] for e in events] == [standup.json()["id"]]
    tasks = {t["id"]: t for t in client.get("/api/tasks", headers=auth).json()}
    assert set(tasks) == {board["id"], subtask["id"]}
    assert tasks[subtask["id"]]["parent_id"] is None  # kept, as a top-level task
    contents = [m["content"] for m in client.get("/api/memories", headers=auth).json()]
    assert contents == [memory["content"]]
    assert client.get("/api/settings/demo-data", headers=auth).json()["present"] is False


def test_sample_data_from_before_the_ledger_is_removed_exactly(client):
    from app.models import CalendarEvent, SampleRow

    auth = _demo(client, "fixes-legacy@example.com")
    user = _user("fixes-legacy@example.com")
    db = SessionLocal()
    try:
        # Accounts seeded before this change have no ledger rows.
        db.query(SampleRow).filter(SampleRow.user_id == user.id).delete()
        # A field-for-field twin of a sample event, made an hour apart from the
        # seeding: the user's own, and it must survive.
        start = datetime.now(timezone.utc) + timedelta(days=3)
        twin = CalendarEvent(
            user_id=user.id, title="Standup", description="Daily engineering sync.",
            location="Zoom", start_at=start, end_at=start + timedelta(minutes=15),
            attendees=[], source="local",
        )
        twin.created_at = datetime.now(timezone.utc) + timedelta(hours=1)
        db.add(twin)
        db.commit()
        twin_id = twin.id
    finally:
        db.close()
    own_task = client.post(
        "/api/tasks", headers=auth, json={"title": "Draft Q3 board update"}
    ).json()

    assert client.get("/api/settings/demo-data", headers=auth).json()["counts"]["events"] == 7
    removed = client.delete("/api/settings/demo-data", headers=auth).json()["removed"]
    assert removed["events"] == 7 and removed["tasks"] == 6
    assert removed["schedules"] == 1 and removed["heartbeats"] == 1

    assert [e["id"] for e in client.get(
        "/api/events", headers=auth, params={"days": 14}
    ).json()] == [twin_id]
    assert [t["id"] for t in client.get("/api/tasks", headers=auth).json()] == [own_task["id"]]
    assert client.get("/api/settings/demo-data", headers=auth).json()["present"] is False


def test_a_subtask_needs_a_parent_the_user_owns(client):
    auth = _demo(client, "fixes-keep@example.com")
    resp = client.post(
        "/api/tasks", headers=auth, json={"title": "Orphan", "parent_id": "no-such-task"}
    )
    assert resp.status_code == 404


def test_a_region_is_not_a_time_zone(client):
    # ZoneInfo("America") raises IsADirectoryError, which escaped as a 500.
    assert timeutil.zone("America") == timezone.utc
    assert not timeutil.is_valid_zone("Asia") and not timeutil.is_valid_zone("Etc")
    auth = _demo(client, "fixes-region@example.com", tz="Europe")
    assert client.get("/api/auth/me", headers=auth).json()["timezone"] == "UTC"
    resp = client.patch("/api/auth/me", headers=auth, json={"timezone": "America"})
    assert resp.status_code == 422, resp.text


def _next_monday_at(hour: int, minute: int = 0) -> datetime:
    """A UTC time on the coming Monday, so these tests don't age out."""
    today = datetime.now(timezone.utc).date()
    monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    return datetime(monday.year, monday.month, monday.day, hour, minute, tzinfo=timezone.utc)


def test_schedules_and_scheduled_automations_run_on_the_owners_clock(client, monkeypatch):
    from app.automation import engine
    from app.services import schedules

    _demo(client, "fixes-sched@example.com", tz="Asia/Kolkata")
    uid = _user("fixes-sched@example.com").id
    ran: list[str] = []
    monkeypatch.setattr(
        schedules, "run_one", lambda db, user, s: ran.append(f"{user.id}:{s.name}") or {}
    )
    monkeypatch.setattr(
        engine, "run_rule", lambda db, user, rule, ctx: ran.append(f"{user.id}:{rule.name}") or {}
    )

    db = SessionLocal()
    try:
        # The sample schedule is 07:30 on weekdays; the sample automation 08:00.
        ist_0730 = _next_monday_at(2, 0)
        schedules.run_due(db, now=ist_0730)
        assert f"{uid}:Morning briefing" in ran
        engine.run_due_schedules(db, now=ist_0730 + timedelta(minutes=30))
        assert f"{uid}:Morning briefing at 08:00" in ran

        ran.clear()
        utc_0730 = _next_monday_at(7, 30)  # 13:00 in India
        schedules.run_due(db, now=utc_0730)
        engine.run_due_schedules(db, now=utc_0730 + timedelta(minutes=30))
        assert not [r for r in ran if r.startswith(f"{uid}:")]
    finally:
        db.close()


def test_a_half_hourly_worker_still_fires_every_minute_of_the_clock():
    from app.services.schedules import due_since

    kathmandu = ZoneInfo("Asia/Kathmandu")  # UTC+5:45
    tick = _next_monday_at(2, 30)  # the worker runs on :00 and :30 UTC
    # 08:00 in Kathmandu is 02:15 UTC: no tick lands on it, the 02:30 one owes it.
    assert due_since("0 8 * * 1-5", None, tick, kathmandu)
    # 07:15 in India is 01:45 UTC, owed by the 02:00 tick.
    assert due_since("15 7 * * 1-5", None, tick - timedelta(minutes=30), IST)
    # Already run for that slot: not again.
    assert not due_since("0 8 * * 1-5", tick - timedelta(minutes=10), tick, kathmandu)
    # A long outage doesn't replay the backlog.
    assert not due_since("0 8 * * 1-5", None, tick + timedelta(hours=3), kathmandu)


def test_a_failing_schedule_is_not_retried_every_tick(client, monkeypatch):
    from app.models import Schedule
    from app.services import schedules

    _demo(client, "fixes-sched@example.com", tz="Asia/Kolkata")
    uid = _user("fixes-sched@example.com").id
    calls: list[str] = []

    def boom(db, user, s):
        calls.append(s.id)
        raise RuntimeError("model down")

    monkeypatch.setattr(schedules, "run_one", boom)
    db = SessionLocal()
    try:
        mine = db.query(Schedule).filter(Schedule.user_id == uid).one()
        mine.last_run_at = None
        db.commit()
        tick = _next_monday_at(2, 0)
        schedules.run_due(db, now=tick)
        schedules.run_due(db, now=tick + timedelta(minutes=1))
        assert calls.count(mine.id) == 1
    finally:
        db.close()


def test_schedules_follow_the_account_to_a_new_zone(client):
    from app.models import Schedule

    auth = _demo(client, "fixes-region@example.com")  # UTC, seeded on UTC
    uid = _user("fixes-region@example.com").id
    resp = client.patch("/api/auth/me", headers=auth, json={"timezone": "Asia/Kolkata"})
    assert resp.status_code == 200, resp.text
    db = SessionLocal()
    try:
        zones = {s.timezone for s in db.query(Schedule).filter(Schedule.user_id == uid)}
    finally:
        db.close()
    assert zones == {"Asia/Kolkata"}


def test_free_slots_start_on_the_hour_on_the_users_clock(client, monkeypatch):
    from app.agent import tools
    from app.models import User

    fixed = datetime(2026, 9, 28, 4, 40, tzinfo=timezone.utc)  # Monday 10:10 in India

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr(tools, "datetime", Frozen)
    db = SessionLocal()
    try:
        user = User(email="fixes-slots@example.com", name="Slots", timezone="Asia/Kolkata")
        db.add(user)
        db.commit()
        slots = tools._find_free_time(
            tools.ToolContext(db=db, user=user), {"duration_minutes": 30, "days": 1}
        )
    finally:
        db.close()
    first = datetime.fromisoformat(slots[0]["start_at"]).astimezone(IST)
    assert (first.hour, first.minute) == (11, 0), slots[0]  # not 10:30


def test_a_question_about_memory_is_a_lookup_not_a_save(client):
    auth = _demo(client, "fixes-demo@example.com")
    before = client.get("/api/memories", headers=auth).json()
    events, _ = _stream(client, auth, "Do you remember what I prefer?")
    after = client.get("/api/memories", headers=auth).json()
    assert len(after) == len(before)
    assert not any(m["content"].lower().startswith("do you remember") for m in after)


def test_a_queued_action_is_described_to_the_user():
    from app.llm import MockProvider

    payload = {
        "status": "awaiting_approval",
        "message": "send_email is queued and waiting for the user to approve it. "
                   "Tell them what is pending, then stop.",
        "preview": "Send email to priya@northwind.vc\nSubject: Signed\n\nAttached.",
        "pending_action_id": "x",
    }
    text = MockProvider._summarise_tool_output(
        [{"role": "tool", "name": "send_email", "content": json.dumps(payload)}]
    )
    assert "Tell them" not in text
    assert "Send email to priya@northwind.vc" in text and "Subject" not in text


def test_memory_questions_and_instructions_are_told_apart():
    import re

    from app.llm import MockProvider

    def route(text: str) -> str | None:
        return next(
            (tool for pat, tool, _ in MockProvider.ROUTES if re.search(pat, text.lower())), None
        )

    assert route("Do you remember what I prefer?") == "search_memory"
    assert route("Do you know what's on my calendar today?") == "list_events"
    assert route("Do you know if I have any tasks due?") == "list_tasks"
    assert route("Do you know when I'm free tomorrow?") == "find_free_time"
    assert route("Can you remember that I prefer afternoon meetings") == "save_memory"
    assert route("Hey, remember that I travel on Fridays") == "save_memory"
    assert route("Remember that I prefer afternoon meetings") == "save_memory"
    args = MockProvider._extract_args(
        "save_memory", "Could you please remember that I prefer afternoon meetings?"
    )
    assert args["content"] == "I prefer afternoon meetings"


def test_a_bare_time_is_the_next_time_the_clock_shows_it():
    from app.agent.tools import parse_when

    now = datetime.now(IST)
    at = parse_when("3pm", default_hour=10, tz=IST).astimezone(IST)
    assert (at.hour, at.minute) == (15, 0)
    assert now < at <= now + timedelta(days=1)
    assert parse_when("sometime", tz=IST) is None


def test_the_event_approval_card_shows_the_time_that_will_be_booked():
    from app.agent.tools import _event_times, build_preview

    args = {"title": "Call with Priya", "start_at": "2026-10-05T09:30:00Z", "duration_minutes": 45}
    preview = build_preview("create_event", args, IST)
    assert "Add to your calendar: Call with Priya" in preview
    assert "When: Mon 5 Oct, 15:00–15:45" in preview  # the user's clock, not 09:30Z
    start, end = _event_times(args, IST)
    assert start == datetime(2026, 10, 5, 9, 30, tzinfo=timezone.utc)
    assert end - start == timedelta(minutes=45)
    # "tomorrow 3pm" until "4pm" ends on the same day.
    start, end = _event_times({"start_at": "tomorrow 3pm", "end_at": "4pm"}, IST)
    assert end - start == timedelta(hours=1)


def test_an_empty_parent_id_means_no_parent(client):
    auth = _demo(client, "fixes-keep@example.com")
    resp = client.post("/api/tasks", headers=auth, json={"title": "Loose", "parent_id": ""})
    assert resp.status_code == 200, resp.text
    assert resp.json()["parent_id"] is None
