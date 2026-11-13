"""Unattended work: schedules and automations with their own approval_mode, parked approvals, the
heartbeat's narrow remit and the per-trigger limits (spec 10 section 9)."""

from __future__ import annotations

import os
from datetime import timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("ALLOW_DEMO_LOGIN", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import models  # noqa: E402
from app.agent import coordinator  # noqa: E402
from app.automation import engine  # noqa: E402
from app.config import settings  # noqa: E402
from app.device.runtime import reset_cache  # noqa: E402
from app.models import utcnow  # noqa: E402
from app.permissions import approvals, events, gate, reviewer, rules  # noqa: E402
from app.services import heartbeat, schedules, usage  # noqa: E402
from tests.perm_helpers import ctx as make_ctx  # noqa: E402
from tests.perm_helpers import apply_legacy, make_session, make_user  # noqa: E402

SEND = {"to": "bob@example.com", "subject": "Hi", "body": "x"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine_, db = make_session(tmp_path)
    monkeypatch.setattr(settings, "device_control", "off", raising=False)
    monkeypatch.setattr(settings, "local_mode", False, raising=False)
    reset_cache()
    yield db
    db.close()
    engine_.dispose()


@pytest.fixture
def device(env, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "device_control", "on", raising=False)
    monkeypatch.setattr(settings, "local_mode", True, raising=False)
    monkeypatch.setattr(settings, "aura_home", str(tmp_path / "aura-home"), raising=False)
    monkeypatch.setattr(settings, "device_command_denylist", "", raising=False)
    monkeypatch.setattr(gate, "_dialect", lambda policy: "posix")
    reset_cache()
    yield env
    reset_cache()


def user_in(db, mode="ask"):
    return make_user(db, email=f"{mode}-{utcnow().timestamp()}@example.com", mode=mode, migrated=True)


# ---------------------------------------------------------------------------
# The limits of each trigger (spec 9.1)
# ---------------------------------------------------------------------------

DEVICE_CALLS = {
    "run_command": {"command": "echo hi"},
    "stop_process": {"session_id": "s"},
    "take_screenshot": {},
    "mouse_click": {"x": 1, "y": 1},
    "read_clipboard": {},
    "set_clipboard": {"text": "x"},
    "open_app": {"name": "notes"},
    "delete_file": {"path": "/tmp/aura-test-file"},
    "write_file": {"path": "/tmp/aura-test-file", "content": "x"},
    "read_file": {"path": "/tmp/aura-test-file"},
    "fetch_url": {"url": "https://example.com/"},
}
# tool -> the trigger that may NOT use it
DENIED = {
    "schedule": {"take_screenshot", "mouse_click", "read_clipboard", "set_clipboard", "open_app"},
    "automation": {"run_command", "stop_process", "take_screenshot", "mouse_click", "read_clipboard",
                   "set_clipboard", "open_app", "delete_file"},
    "inbound": {"run_command", "stop_process", "take_screenshot", "mouse_click", "read_clipboard",
                "set_clipboard", "open_app", "delete_file"},
    "heartbeat": set(DEVICE_CALLS),  # the whole device-control system
}


@pytest.mark.parametrize("trigger", ["schedule", "automation", "inbound", "heartbeat"])
@pytest.mark.parametrize("tool", sorted(DEVICE_CALLS))
def test_trigger_limits_table(device, trigger, tool):
    db = device
    user = user_in(db, "skip")  # even the most permissive mode cannot widen a trigger's limits
    c = make_ctx(db, user, trigger, origin_id="o1", approval_mode="ask")
    d = gate.decide(db, user, tool, DEVICE_CALLS[tool], c)
    if tool in DENIED[trigger]:
        assert (d.kind, d.reason_code) == ("deny", "TRIGGER_DENIED"), (trigger, tool)
    else:
        assert d.reason_code != "TRIGGER_DENIED", (trigger, tool, d.reason_code)


def test_heartbeat_does_workspace_and_reads_only(env):
    db = env
    user = user_in(db, "skip")

    def code(tool, args):
        d = gate.decide(db, user, tool, args, make_ctx(db, user, "heartbeat"))
        return d.kind, d.reason_code

    assert code("create_task", {"title": "x"}) == ("allow", "DEFAULT_WORKSPACE")
    assert code("notify", {"title": "a", "body": "b"}) == ("allow", "DEFAULT_WORKSPACE")
    assert code("detect_conflicts", {}) == ("allow", "DEFAULT_READ")
    assert code("send_email", SEND) == ("deny", "TRIGGER_DENIED")
    assert code("delete_task", {"task_id": "x"}) == ("deny", "TRIGGER_DENIED")


# ---------------------------------------------------------------------------
# approval_mode: never skip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("trigger", ["schedule", "automation"])
@pytest.mark.parametrize("mode", ["ask", "auto", "skip", "", None, "bogus"])
def test_unattended_never_skips(env, trigger, mode):
    db = env
    user = user_in(db, "skip")  # the user skips approvals in chat; unattended work does not
    c = make_ctx(db, user, trigger, origin_id="o1", approval_mode=mode)
    d = gate.decide(db, user, "send_email", SEND, c)
    # `auto` has no reviewer behind it on the mock provider, so it asks as well
    assert d.kind == "ask", (trigger, mode)
    chat = gate.decide(db, user, "send_email", SEND, make_ctx(db, user, "chat"))
    assert chat.kind == "allow"


def test_auto_with_a_reviewer_is_used_by_the_row_not_the_user(env, monkeypatch):
    db = env
    user = user_in(db, "ask")
    monkeypatch.setattr(reviewer, "available", lambda: True)
    monkeypatch.setattr(reviewer, "review", lambda *a, **k: reviewer.ReviewerVerdict(True, "fine", "REVIEWER_ALLOW"))
    c = make_ctx(db, user, "schedule", origin_id="s1", approval_mode="auto")
    assert gate.decide(db, user, "send_email", SEND, c).reason_code == "REVIEWER_ALLOW"
    c = make_ctx(db, user, "schedule", origin_id="s1", approval_mode="ask")
    assert gate.decide(db, user, "send_email", SEND, c).reason_code == "MODE_ASK"


# ---------------------------------------------------------------------------
# A schedule that needs an approval
# ---------------------------------------------------------------------------


class Script:
    name = "scripted"

    def __init__(self, tool, args):
        self.tool, self.args = tool, args

    def complete(self, messages, tools=None, model=None):
        from app.llm import LLMResponse, ToolCall, Usage

        if messages[-1]["role"] == "tool":
            return LLMResponse(text="Parked it.", usage=Usage())
        return LLMResponse(tool_calls=[ToolCall("c1", self.tool, self.args)], usage=Usage())

    def embed(self, texts):
        return []


def test_a_schedule_parks_notifies_and_expires(env, monkeypatch):
    db = env
    user = user_in(db, "skip")
    sched = schedules.create(db, user, "email bob the numbers", name="Weekly numbers", approval_mode="ask")
    monkeypatch.setattr(coordinator, "get_provider", lambda: Script("send_email", SEND))
    out = schedules.run_one(db, user, sched)
    assert out["pending"] == 1
    pa = db.scalars(select(models.PendingAction).where(models.PendingAction.user_id == user.id)).one()
    assert (pa.origin, pa.origin_id, pa.status, pa.card) == ("schedule", sched.id, "pending", "tool")
    assert pa.conversation_id == out["conversation_id"]
    left = approvals._aware(pa.expires_at) - utcnow()
    assert timedelta(hours=11, minutes=55) < left <= timedelta(hours=12)
    notes = db.scalars(select(models.Notification).where(models.Notification.user_id == user.id)).all()
    assert any("Weekly numbers" in n.body and n.level == "urgent" for n in notes)
    assert all(n.level == "urgent" for n in notes)
    # nobody answers: the card lapses
    pa.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert approvals.expire_due(db) == 1 and pa.status == "expired"


def test_an_always_on_a_schedule_card_only_covers_that_schedule(env):
    db = env
    user = user_in(db, "skip")
    sched = schedules.create(db, user, "p", name="s")
    c = make_ctx(db, user, "schedule", origin_id=sched.id, approval_mode="ask")
    d = gate.decide(db, user, "send_email", SEND, c)
    a = approvals.queue_action(db, user, c, __import__("app.llm", fromlist=["ToolCall"]).ToolCall("", "send_email", SEND), d)
    assert "only in that schedule" in a.card_payload["rule_text"]


# ---------------------------------------------------------------------------
# Automations
# ---------------------------------------------------------------------------


def make_rule(db, user, actions, mode="ask"):
    rule = models.AutomationRule(user_id=user.id, name="Weekly", natural_language="n", trigger_type="manual",
                                 trigger_config={}, actions=actions, approval_mode=mode)
    db.add(rule)
    db.commit()
    return rule


def test_an_automation_runs_parks_and_reports_blocked(device):
    db = device
    user = user_in(db, "skip")
    rule = make_rule(db, user, [
        {"tool": "create_task", "arguments": {"title": "{subject}"}},
        {"tool": "send_email", "arguments": SEND},
        {"tool": "run_command", "arguments": {"command": "echo hi"}},
    ])
    out = engine.run_rule(db, user, rule, {"subject": "Weekly"})
    assert [e["tool"] for e in out["executed"]] == ["create_task"]
    assert out["queued_for_approval"] == ["send_email"]
    assert [(b["tool"], b["reason_code"]) for b in out["blocked"]] == [("run_command", "TRIGGER_DENIED")]
    pa = db.scalars(select(models.PendingAction).where(models.PendingAction.user_id == user.id)).one()
    assert (pa.origin, pa.origin_id, pa.conversation_id, pa.work_item_id) == ("automation", rule.id, None, None)
    assert pa.preview.startswith("Automation “Weekly” wants to:")
    assert timedelta(hours=23, minutes=55) < approvals._aware(pa.expires_at) - utcnow() <= timedelta(hours=24)
    n = db.scalars(select(models.Notification).where(models.Notification.user_id == user.id)).all()
    assert len(n) == 1 and n[0].level == "urgent" and "Weekly" in n[0].body
    # it fires again: the same question is not asked twice, nor announced twice
    engine.run_rule(db, user, rule, {"subject": "Weekly"})
    assert len(db.scalars(select(models.PendingAction).where(models.PendingAction.user_id == user.id)).all()) == 1
    assert len(db.scalars(select(models.Notification).where(models.Notification.user_id == user.id)).all()) == 1


def test_an_always_answer_on_an_automation_card_is_scoped_to_it(env):
    db = env
    user = user_in(db, "ask")
    rule = make_rule(db, user, [{"tool": "send_email", "arguments": SEND}])
    engine.run_rule(db, user, rule, {})
    pa = db.scalars(select(models.PendingAction).where(models.PendingAction.user_id == user.id)).one()
    approvals.resolve(db, user, pa.id, "always")
    row = db.scalars(select(models.PermissionRule).where(models.PermissionRule.user_id == user.id)).one()
    assert row.applies_to == f"automation:{rule.id}" and row.source == "card"
    out = engine.run_rule(db, user, rule, {})
    assert [e["tool"] for e in out["executed"]] == ["send_email"], "next firing is allowed by that rule"
    other = make_rule(db, user, [{"tool": "send_email", "arguments": SEND}])
    assert engine.run_rule(db, user, other, {})["queued_for_approval"] == ["send_email"]


def test_screening_drops_what_cannot_run_unattended(env):
    db = env
    user = user_in(db)
    kept, warnings = engine.screen_actions(db, user, [
        {"tool": "create_task", "arguments": {}},
        {"tool": "run_command", "arguments": {"command": "ls"}},
        {"tool": "open_app", "arguments": {"name": "x"}},
        {"tool": "delete_file", "arguments": {}},
        {"tool": "no_such_tool", "arguments": {}},
        {"tool": "aura_plan", "arguments": {}},
        {"tool": "send_email", "arguments": SEND},
    ])
    assert [a["tool"] for a in kept] == ["create_task", "send_email"]
    assert len(warnings) == 5 and all(w.startswith("Left out") for w in warnings)
    assert any("run commands" in w for w in warnings) and any("no such action" in w for w in warnings)


def test_a_rule_with_nothing_left_is_refused(env, monkeypatch):
    db = env
    user = user_in(db)
    monkeypatch.setattr(engine, "compile_rule", lambda text: {
        "name": "n", "trigger_type": "manual", "trigger_config": {},
        "actions": [{"tool": "run_command", "arguments": {"command": "ls"}}]})
    with pytest.raises(ValueError, match="run commands"):
        engine.create_rule_checked(db, user, "run ls every day")


def test_a_rule_is_created_with_its_mode_and_hint(env, monkeypatch):
    db = env
    user = user_in(db)
    rule, warnings = engine.create_rule_checked(db, user, "when a task is due notify me", approval_mode="ask")
    assert rule.approval_mode == "ask" and warnings == [] and rule.requires_confirmation is False
    rule, _ = engine.create_rule_checked(db, user, "x", approval_mode="skip")
    assert rule.approval_mode == "ask", "skip is not a thing for unattended work"


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


def test_the_heartbeat_has_no_tier_floor(env):
    db = env
    import inspect

    src = inspect.getsource(heartbeat)
    assert "heartbeat_allows" not in src and "autonomy_floor" not in src and "autonomy" not in src
    enabled = {"EM01", "TK01", "CA03", "MP01", "TK02", "SY01", "NT01"}
    # proactive and enabled is all that is asked, whatever the old tier said
    assert all(heartbeat._can_run(enabled, code) for code in ("EM01", "TK01", "CA03", "TK02"))
    assert not heartbeat._can_run(set(), "TK01")
    assert not heartbeat._can_run(enabled, "EM03"), "a skill that is not proactive never runs"


def _urgent_email(db, user):
    db.add(models.EmailMessage(user_id=user.id, sender="a@x.com", subject="Do it", importance="urgent",
                               received_at=utcnow(), action_items=["File the VAT return"]))
    db.commit()


@pytest.mark.parametrize("level", ["strict", "conservative", "relaxed", "full"])
def test_a_heartbeat_creates_tasks_at_every_old_tier(env, level):
    """Workspace writes need no tier (decision 3a), except where the user's own ask rule says so."""
    db = env
    user = make_user(db, email=f"{level}@example.com", level=level)
    apply_legacy(db, user)
    _urgent_email(db, user)
    heartbeat.run(db, user, force=True)
    titles = [t.title for t in db.scalars(select(models.Task).where(models.Task.user_id == user.id)).all()]
    if level == "strict":  # a strict user's migrated ask rule on create_task: the heartbeat cannot ask
        assert titles == []
    else:
        assert titles == ["File the VAT return"]
    assert db.scalars(select(models.PendingAction)).first() is None, "the heartbeat never parks anything"


def test_the_heartbeat_never_asks_or_reviews(env, monkeypatch):
    db = env
    user = user_in(db, "auto")
    _urgent_email(db, user)
    rules.add(db, user, effect="ask", system="core-tasks", tool="create_task")
    monkeypatch.setattr(reviewer, "available", lambda: True)
    monkeypatch.setattr(reviewer, "review", lambda *a, **k: (_ for _ in ()).throw(AssertionError("reviewer called")))
    heartbeat.run(db, user, force=True)
    assert db.scalars(select(models.Task)).first() is None
    assert db.scalars(select(models.PendingAction)).first() is None
    kinds = [(e.kind, e.reason_code) for e in events.listing(db, user.id, limit=200)]
    assert ("decision", "HEARTBEAT_NO_ASK") in kinds


def test_run_all_due_passes_no_tier(env, monkeypatch):
    db = env
    user = user_in(db)
    row = db.scalars(select(models.UserSettings).where(models.UserSettings.user_id == user.id)).one()
    row.heartbeat_enabled = True
    row.quiet_hours = ""
    db.commit()
    seen = []
    monkeypatch.setattr(heartbeat, "run", lambda db_, u, *a, **k: seen.append((a, k)) or models.HeartbeatReport())
    heartbeat.run_all_due(db)
    assert seen == [((), {})]


# ---------------------------------------------------------------------------
# Spend: the reviewer's share
# ---------------------------------------------------------------------------


def test_review_spend_counts_in_the_background_share_when_unattended(env, monkeypatch):
    db = env
    user = user_in(db)
    monkeypatch.setattr(settings, "spend_cap_enabled", True, raising=False)
    monkeypatch.setattr(settings, "daily_spend_cap_usd", 1.0, raising=False)
    db.add(models.UsageRecord(user_id=user.id, model="m", trigger="review", cost_usd=0.5))
    db.commit()
    share = usage.BACKGROUND_BUDGET_SHARE
    assert 0 < share < 0.5
    usage.check(db, user.id, trigger="review")  # watching: counts against the whole allowance
    usage.check(db, user.id, trigger="review", background=False)
    with pytest.raises(usage.SpendCapReached):
        usage.check(db, user.id, trigger="review", background=True)
    with pytest.raises(usage.SpendCapReached):
        usage.check(db, user.id, trigger="schedule")
    usage.check(db, user.id, trigger="chat")


@pytest.mark.parametrize("trigger,counts_as_background", [
    ("chat", False), ("inbound", True), ("schedule", True), ("automation", True), ("heartbeat", True),
    ("resume", True),
])
def test_first_segment_of_every_trigger_uses_its_own_share(env, monkeypatch, trigger, counts_as_background):
    """A first-segment inbound run is unattended work: it counts against the background share (spec 20 s7)."""
    db = env
    user = user_in(db)
    monkeypatch.setattr(settings, "spend_cap_enabled", True, raising=False)
    monkeypatch.setattr(settings, "daily_spend_cap_usd", 1.0, raising=False)
    db.add(models.UsageRecord(user_id=user.id, model="m", trigger="chat", cost_usd=0.5))
    db.commit()
    if counts_as_background:
        with pytest.raises(usage.SpendCapReached):
            usage.check(db, user.id, trigger=trigger)
    else:
        usage.check(db, user.id, trigger=trigger)


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    from app.db import engine as db_engine
    from app.main import app
    from tests.schema import drop_test_schema, ensure_test_schema

    drop_test_schema(db_engine)
    ensure_test_schema(db_engine)
    with TestClient(app) as c:
        yield c
    drop_test_schema(db_engine)


@pytest.fixture(scope="module")
def auth(client):
    resp = client.post("/api/auth/demo", json={"email": "unattended@aura.ai", "name": "Unattended"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_schedules_carry_an_approval_mode(client, auth, monkeypatch):
    made = client.post("/api/schedules", headers=auth, json={"prompt": "summarise my inbox", "name": "Inbox"})
    assert made.status_code == 200 and made.json()["approval_mode"] == "ask"
    sid = made.json()["id"]
    # the mock provider has no reviewer, so auto is refused
    assert client.patch(f"/api/schedules/{sid}", headers=auth, json={"approval_mode": "auto"}).status_code == 409
    assert client.post("/api/schedules", headers=auth,
                       json={"prompt": "summarise", "approval_mode": "auto"}).status_code == 409
    assert client.patch(f"/api/schedules/{sid}", headers=auth, json={"approval_mode": "skip"}).status_code == 422
    # with a reviewer it is accepted, logged, and listed
    monkeypatch.setattr(reviewer, "available", lambda: True)
    patched = client.patch(f"/api/schedules/{sid}", headers=auth, json={"approval_mode": "auto"})
    assert patched.status_code == 200 and patched.json()["approval_mode"] == "auto"
    same = client.patch(f"/api/schedules/{sid}", headers=auth, json={"name": "Inbox 2", "approval_mode": "auto"})
    assert same.status_code == 200
    listed = {s["id"]: s for s in client.get("/api/schedules", headers=auth).json()}
    assert listed[sid]["approval_mode"] == "auto"
    log = client.get("/api/activity", headers=auth)  # sanity: the app still serves
    assert log.status_code == 200
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        user = db.scalars(select(models.User).where(models.User.email == "unattended@aura.ai")).one()
        changes = [e for e in events.listing(db, user.id, limit=100) if e.kind == "schedule_mode_changed"]
        assert len(changes) == 1 and changes[0].detail["to"] == "auto" and changes[0].target == f"schedule:{sid}"
    finally:
        db.close()


def test_automations_carry_an_approval_mode_and_warnings(client, auth, monkeypatch):
    made = client.post("/api/automations", headers=auth,
                       json={"natural_language": "when a task is due notify me"})
    assert made.status_code == 200
    body = made.json()
    assert body["approval_mode"] == "ask" and body["warnings"] == []
    assert client.post("/api/automations", headers=auth, json={
        "natural_language": "when a task is due notify me", "approval_mode": "auto"}).status_code == 409
    assert client.patch(f"/api/automations/{body['id']}", headers=auth,
                        json={"approval_mode": "auto"}).status_code == 409
    monkeypatch.setattr(reviewer, "available", lambda: True)
    ok = client.patch(f"/api/automations/{body['id']}", headers=auth, json={"approval_mode": "auto"})
    assert ok.status_code == 200 and ok.json()["approval_mode"] == "auto"
    # a rule whose steps cannot run unattended is turned down with the reason
    monkeypatch.setattr(engine, "compile_rule", lambda text: {
        "name": "n", "trigger_type": "manual", "trigger_config": {},
        "actions": [{"tool": "run_command", "arguments": {"command": "ls"}}]})
    refused = client.post("/api/automations", headers=auth, json={"natural_language": "run ls every day"})
    assert refused.status_code == 422 and "run commands" in refused.json()["detail"]
    # a mixed rule is created and says what it left out
    monkeypatch.setattr(engine, "compile_rule", lambda text: {
        "name": "n", "trigger_type": "manual", "trigger_config": {},
        "actions": [{"tool": "run_command", "arguments": {"command": "ls"}},
                    {"tool": "notify", "arguments": {"title": "x"}}]})
    mixed = client.post("/api/automations", headers=auth, json={"natural_language": "ls then notify me"})
    assert mixed.status_code == 200
    assert [a["tool"] for a in mixed.json()["actions"]] == ["notify"] and len(mixed.json()["warnings"]) == 1


def test_channel_owner_endpoint(client, auth):
    assert client.put("/api/channels/telegram/owner", headers=auth,
                      json={"owner_sender": "me"}).status_code == 404  # not connected yet
    assert client.put("/api/channels/web/owner", headers=auth, json={"owner_sender": "me"}).status_code == 404
    token = client.post("/api/channels/connect", headers=auth, json={"kind": "telegram"}).json()["token"]
    listed = {c["kind"]: c for c in client.get("/api/channels", headers=auth).json()}
    assert listed["telegram"]["owner_sender"] == "" and listed["telegram"]["bridge_owner_only"] is False
    out = client.put("/api/channels/telegram/owner", headers=auth,
                     json={"owner_sender": "@Me", "bridge_owner_only": False})
    assert out.status_code == 200 and out.json()["owner_sender"] == "@Me"
    stranger = client.post("/api/channels/inbound/telegram", json={"token": token, "text": "do evil", "sender": "eve"})
    assert stranger.status_code == 200 and stranger.json()["reply"] == ""
    notes = client.get("/api/notifications", headers=auth).json()
    assert any(n["title"].startswith("Message on Telegram from eve") and n["body"] == "do evil" for n in notes)
