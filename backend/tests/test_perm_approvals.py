"""Approvals lifecycle: queueing, the card, the rule each answer stores, who may answer, idempotence,
the re-check, expiry and the task-loop hook (spec 10 section 6)."""

from __future__ import annotations

import os
import sys
import types
from datetime import timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("ALLOW_DEMO_LOGIN", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import models, plugins  # noqa: E402
from app.config import settings  # noqa: E402
from app.device.runtime import reset_cache  # noqa: E402
from app.llm import ToolCall  # noqa: E402
from app.models import PendingAction, PermissionRule, Task, utcnow  # noqa: E402
from app.permissions import approvals, events, gate, rules  # noqa: E402
from app.permissions.approvals import ApprovalError, Caller  # noqa: E402
from tests.perm_helpers import ctx as make_ctx  # noqa: E402
from tests.perm_helpers import make_session, make_user  # noqa: E402

SEND = {"to": "bob@example.com", "subject": "Hi", "body": "x"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine, db = make_session(tmp_path)
    monkeypatch.setattr(settings, "device_control", "off", raising=False)
    monkeypatch.setattr(settings, "local_mode", False, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    reset_cache()
    yield tmp_path, db
    db.close()
    engine.dispose()


@pytest.fixture
def device(env, monkeypatch):
    tmp, db = env
    monkeypatch.setattr(settings, "device_control", "on", raising=False)
    monkeypatch.setattr(settings, "local_mode", True, raising=False)
    monkeypatch.setattr(settings, "aura_home", str(tmp / "aura-home"), raising=False)
    monkeypatch.setattr(settings, "device_command_denylist", "", raising=False)
    monkeypatch.setattr(settings, "device_exec_mode", "ask", raising=False)
    monkeypatch.setattr(settings, "device_allowed_roots", str(tmp), raising=False)
    monkeypatch.setattr(gate, "_dialect", lambda policy: "posix")
    reset_cache()
    yield tmp, db
    reset_cache()


def mk(db, mode="ask"):
    return make_user(db, mode=mode, migrated=True)


def park(db, user, tool, args, trigger="chat", **ctxkw):
    """Decide a call the way the loop does and park it; returns (action, decision)."""
    wid = ctxkw.get("work_item_id")
    if wid and db.get(models.WorkItem, wid) is None:  # pending_actions.work_item_id is a real foreign key now
        db.add(models.WorkItem(id=wid, user_id=user.id, instruction="x", title="x", task_type="other"))
        db.commit()
    c = make_ctx(db, user, trigger, **ctxkw)
    d = gate.decide(db, user, tool, args, c)
    assert d.kind == "ask", (d.kind, d.reason_code)
    return approvals.queue_action(db, user, c, ToolCall(id="c1", name=tool, arguments=args), d), d


def answer(db, user, action, what, caller=None):
    return approvals.resolve(db, user, action.id, what, caller)


def status_of(db, action):
    db.refresh(action)
    return action.status


# ---------------------------------------------------------------------------
# Creation and the card
# ---------------------------------------------------------------------------


def test_options_per_card(device):
    tmp, db = device
    user = mk(db)
    a, _ = park(db, user, "send_email", SEND)
    assert a.options == ["once", "window", "always", "reject"] and not a.always_user
    a, _ = park(db, user, "run_command", {"command": "echo $(whoami)"})
    assert a.options == ["once", "reject"]
    a, _ = park(db, user, "delete_file", {"path": str(tmp / "x.txt")})
    assert a.options == ["once", "reject"] and a.always_user
    a, _ = park(db, user, "send_process_input", {"session_id": "s", "text": "y"})
    assert a.options == ["once", "reject"]
    # a folder / app / delete-permission card answers approve or reject, and is always the user's
    c = make_ctx(db, user, "chat")
    d = gate._make(kind="ask", source="always_user", reason_code="FOLDER", reason="Needs a folder.",
                   tool="write_file", args_digest=gate.args_digest({"path": "p"}), card="folder_access",
                   card_payload={"path": str(tmp)}, options=("once", "reject"), always_user=True, user_id=user.id)
    a = approvals.queue_action(db, user, c, ToolCall(id="", name="write_file", arguments={"path": "p"}), d)
    assert a.card == "folder_access" and a.options == ["approve", "reject"] and a.always_user
    assert a.card_payload["path"] == str(tmp)


def test_queue_fills_the_card_and_the_log(env):
    _, db = env
    user = mk(db)
    a, d = park(db, user, "send_email", SEND, conversation_id="conv-1")
    assert (a.tool_name, a.origin, a.conversation_id, a.status) == ("send_email", "chat", "conv-1", "pending")
    assert a.reason_code == "MODE_ASK" and a.reason == d.reason and a.args_digest == d.args_digest
    assert "Why I'm asking:" in a.preview and "bob@example.com" in a.preview
    assert a.card == "tool" and a.skill_code
    text = a.card_payload["rule_text"]
    assert "sending email" in text and "bob@example.com" in text and text.startswith("Always allow")
    assert "10 minutes" in a.card_payload["window_text"]
    logged = [e for e in events.listing(db, user.id, limit=50) if e.kind == "approval_created"]
    assert len(logged) == 1 and logged[0].detail["pending_action_id"] == a.id, "ids survive redaction"
    assert events.verify(db, user.id)["ok"]
    # chat is watched: no notification
    assert db.scalars(select(models.Notification).where(models.Notification.user_id == user.id)).first() is None


@pytest.mark.parametrize(
    "trigger,hours",
    [("chat", 24), ("inbound", 24), ("schedule", 12), ("automation", 24)],
)
def test_expiry_per_trigger(env, trigger, hours):
    _, db = env
    user = mk(db)
    a, _ = park(db, user, "send_email", SEND, trigger, origin_id="o1", approval_mode="ask")
    left = approvals._aware(a.expires_at) - utcnow()
    assert timedelta(hours=hours) - timedelta(minutes=2) < left <= timedelta(hours=hours)
    assert a.origin == trigger and a.origin_id == "o1"


def test_special_cards_expire_in_half_an_hour(env):
    _, db = env
    user = mk(db)
    d = gate._make(kind="ask", source="always_user", reason_code="X", reason="x", tool="write_file",
                   args_digest=gate.args_digest({}), card="app_grant", options=("once", "reject"),
                   always_user=True, user_id=user.id)
    a = approvals.queue_action(db, user, make_ctx(db, user, "chat"), ToolCall("", "write_file", {}), d)
    assert timedelta(minutes=28) < approvals._aware(a.expires_at) - utcnow() <= timedelta(minutes=30)


def test_unattended_cards_notify_and_repeats_do_not_pile_up(env):
    _, db = env
    user = mk(db)
    sched = models.Schedule(user_id=user.id, name="Morning mail", prompt="p")
    db.add(sched)
    db.commit()
    a, _ = park(db, user, "send_email", SEND, "schedule", origin_id=sched.id, approval_mode="ask")
    b, _ = park(db, user, "send_email", SEND, "schedule", origin_id=sched.id, approval_mode="ask")
    assert a.id == b.id, "an identical card already waits for this schedule"
    notes = db.scalars(select(models.Notification).where(models.Notification.user_id == user.id)).all()
    assert len(notes) == 1 and notes[0].level == "urgent"
    assert "Morning mail" in notes[0].body and "send email" in notes[0].body
    # chat cards are never merged
    c1, _ = park(db, user, "send_email", SEND)
    c2, _ = park(db, user, "send_email", SEND)
    assert c1.id != c2.id


@pytest.mark.parametrize(
    "tool,items,applies,expected",
    [
        ("send_email", (("recipient", "a@x.com"), ("recipient", "b@y.com")), "", "Always allow sending email to a@x.com, b@y.com"),
        ("write_file", (("path", "/work/finance/**"),), "", "Always allow writing files in /work/finance and the folders below it"),
        ("run_command", (("command", "git status"),), "", "Always allow running commands for commands starting with `git status`"),
        ("run_command", (("command", "=ls -la"),), "", "for exactly `ls -la`"),
        ("fetch_url", (("domain", "docs.example.com"),), "", "Always allow fetching web pages for docs.example.com and its subdomains"),
        ("open_app", (("app", "chrome"),), "", "Always allow opening apps for chrome"),
        ("create_event", (), "", "Always allow creating calendar events"),
        ("send_email", (("recipient", "a@x.com"),), "schedule:s1", "Always allow sending email to a@x.com, only in that schedule"),
        ("delete_task", (), "automation:r9", "Always allow deleting tasks, only in that automation"),
    ],
)
def test_rule_text_says_what_always_allows(tool, items, applies, expected):
    draft = gate.RuleDraft(system="x", tool=tool, applies_to=applies, items=items)
    assert expected in approvals.rule_text(draft)


# ---------------------------------------------------------------------------
# What each answer stores
# ---------------------------------------------------------------------------


def test_once_stores_nothing_and_runs(env):
    _, db = env
    user = mk(db)
    task = Task(user_id=user.id, title="Renew")
    db.add(task)
    db.commit()
    a, _ = park(db, user, "delete_task", {"task_id": task.id})
    out = answer(db, user, a, "once")
    assert out["decision"] == "once" and out["work_item"] is None
    assert status_of(db, a) == "approved" and a.resolved_by == "session" and a.rule_id is None
    assert db.get(Task, task.id) is None
    assert db.scalars(select(PermissionRule)).first() is None


def test_window_rule_lasts_ten_minutes(env):
    _, db = env
    user = mk(db)
    a, _ = park(db, user, "send_email", SEND)
    out = answer(db, user, a, "window")
    row = db.scalars(select(PermissionRule).where(PermissionRule.user_id == user.id)).one()
    assert (row.effect, row.source, row.tool, row.target_kind, row.pattern) == (
        "allow", "card", "send_email", "recipient", "bob@example.com")
    left = approvals._aware(row.expires_at) - utcnow()
    assert timedelta(minutes=9) < left <= timedelta(minutes=10)
    assert a.rule_id == row.id and out["rule"]["id"] == row.id and "10 minutes" in out["rule"]["text"]
    d = gate.decide(db, user, "send_email", SEND, make_ctx(db, user, "chat"))
    assert (d.kind, d.reason_code) == ("allow", "RULE_ALLOW")
    # the old `minutes` field means nothing any more
    assert "minutes" not in PendingAction.__table__.c


def test_always_rule_is_limited_to_the_target(env):
    _, db = env
    user = mk(db)
    a, _ = park(db, user, "send_email", {**SEND, "to": "A <a@x.com>, b@y.com"})
    answer(db, user, a, "always")
    made = db.scalars(select(PermissionRule).where(PermissionRule.user_id == user.id)).all()
    assert sorted(r.pattern for r in made) == ["a@x.com", "b@y.com"]
    assert all(r.expires_at is None and r.applies_to == "" for r in made)
    chat = lambda to: gate.decide(db, user, "send_email", {**SEND, "to": to}, make_ctx(db, user, "chat")).kind  # noqa: E731
    assert chat("a@x.com") == "allow" and chat("a@x.com, b@y.com") == "allow"
    assert chat("c@z.com") == "ask" and chat("a@x.com, c@z.com") == "ask"


def test_always_for_a_file_allows_that_folder(device):
    tmp, db = device
    user = mk(db)
    work = tmp / "work"
    work.mkdir()
    a, _ = park(db, user, "write_file", {"path": str(work / "a.txt"), "content": "one"})
    answer(db, user, a, "always")
    assert (work / "a.txt").read_text() == "one"
    row = db.scalars(select(PermissionRule).where(PermissionRule.user_id == user.id)).one()
    assert row.target_kind == "path" and row.pattern.endswith("**")
    ok = gate.decide(db, user, "write_file", {"path": str(work / "sub" / "b.txt"), "content": "x"}, make_ctx(db, user, "chat"))
    other = gate.decide(db, user, "write_file", {"path": str(tmp / "else.txt"), "content": "x"}, make_ctx(db, user, "chat"))
    assert ok.kind == "allow" and other.kind == "ask"


def test_schedule_card_rule_is_scoped_to_that_schedule(env):
    _, db = env
    user = mk(db)
    a, _ = park(db, user, "send_email", SEND, "schedule", origin_id="s1", approval_mode="ask")
    answer(db, user, a, "always")
    row = db.scalars(select(PermissionRule).where(PermissionRule.user_id == user.id)).one()
    assert row.applies_to == "schedule:s1"

    def kind(trigger, **kw):
        return gate.decide(db, user, "send_email", SEND, make_ctx(db, user, trigger, **kw)).kind

    assert kind("schedule", origin_id="s1", approval_mode="ask") == "allow"
    assert kind("schedule", origin_id="s2", approval_mode="ask") == "ask"
    assert kind("chat") == "ask"


def test_an_older_card_without_a_recorded_draft_is_worked_out_again(device):
    tmp, db = device
    user = mk(db)
    a, _ = park(db, user, "run_command", {"command": "echo hi"})
    a.options, a.card_payload = [], {}  # as a card written before the draft was recorded
    db.commit()
    out = answer(db, user, a, "always")
    row = db.scalars(select(PermissionRule).where(PermissionRule.user_id == user.id)).one()
    assert (row.tool, row.target_kind, row.pattern) == ("run_command", "command", "=echo hi")  # F8: exact
    assert out["rule"]["id"] == row.id


def test_a_call_that_may_only_be_allowed_once_saves_no_rule(device):
    tmp, db = device
    user = mk(db)
    a, _ = park(db, user, "send_process_input", {"session_id": "s", "text": "y"})
    a.options = []  # an older row that never recorded its options: all four are offered
    db.commit()
    out = answer(db, user, a, "always")
    assert status_of(db, a) == "approved" and "rule" not in out and "one time" in out["note"]
    assert db.scalars(select(PermissionRule)).first() is None


def test_reject_runs_nothing(env):
    _, db = env
    user = mk(db)
    task = Task(user_id=user.id, title="Keep")
    db.add(task)
    db.commit()
    a, _ = park(db, user, "delete_task", {"task_id": task.id})
    out = answer(db, user, a, "reject")
    assert "not run" in out["message"] and out["status"] == "rejected"
    assert status_of(db, a) == "rejected" and a.decision == "reject"
    assert db.get(Task, task.id) is not None
    kinds = [(e.kind, e.outcome) for e in events.listing(db, user.id)]
    assert ("approval_resolved", "rejected") in kinds


def test_an_answer_the_card_did_not_offer_is_422(env):
    _, db = env
    user = mk(db)
    a, _ = park(db, user, "send_email", SEND)
    with pytest.raises(ApprovalError) as e:
        answer(db, user, a, "approve")
    assert e.value.status_code == 422
    assert status_of(db, a) == "pending"
    b, _ = park(db, user, "send_email", {**SEND, "to": "z@z.com"})
    b.options = ["once", "reject"]
    db.commit()
    with pytest.raises(ApprovalError) as e:
        answer(db, user, b, "always")
    assert e.value.status_code == 422


def test_unknown_or_foreign_action_is_404(env):
    _, db = env
    user, other = mk(db), make_user(db, email="other@example.com", migrated=True)
    a, _ = park(db, user, "send_email", SEND)
    for uid in ("nope", a.id):
        with pytest.raises(ApprovalError) as e:
            approvals.resolve(db, other, uid, "once")
        assert e.value.status_code == 404


# ---------------------------------------------------------------------------
# Idempotence, re-check
# ---------------------------------------------------------------------------


def test_double_resolve_409(env):
    _, db = env
    user = mk(db)
    task = Task(user_id=user.id, title="Once")
    db.add(task)
    db.commit()
    a, _ = park(db, user, "delete_task", {"task_id": task.id})
    answer(db, user, a, "once")
    with pytest.raises(ApprovalError) as e:
        answer(db, user, a, "once")
    assert e.value.status_code == 409
    assert e.value.body()["status"] == "approved" and e.value.body()["decision"] == "once"
    with pytest.raises(ApprovalError) as e:
        answer(db, user, a, "reject")
    assert e.value.status_code == 409 and status_of(db, a) == "approved"


def test_the_claim_is_atomic(env):
    """Two sessions answer at once: the conditional UPDATE lets exactly one through."""
    from sqlalchemy.orm import Session

    _, db = env
    user = mk(db)
    a, _ = park(db, user, "send_email", SEND)
    second = Session(bind=db.get_bind(), expire_on_commit=False)
    try:
        stale = second.get(PendingAction, a.id)  # this session still believes the row is pending
        first_claim = approvals._claim(db, user, a)
        assert stale.status == "pending"
        second_claim = approvals._claim(second, second.get(models.User, user.id), stale)
    finally:
        second.close()
    assert first_claim is True and second_claim is False
    assert status_of(db, a) == "resolving"


def test_recheck_refuses_after_block_rule(env):
    _, db = env
    user = mk(db)
    a, _ = park(db, user, "send_email", SEND)
    rules.add(db, user, effect="block", system="core-inbox", tool="send_email")
    out = answer(db, user, a, "always")
    assert out["status"] == "refused" and "no longer allowed" in out["message"]
    assert status_of(db, a) == "refused" and a.reason
    assert db.scalars(select(PermissionRule).where(PermissionRule.source == "card")).first() is None


def test_recheck_when_the_arguments_changed_or_the_system_is_off(env):
    _, db = env
    user = mk(db)
    a, _ = park(db, user, "send_email", SEND)
    a.arguments = {**SEND, "to": "eve@evil.org"}  # edited in the database after the card
    db.commit()
    out = answer(db, user, a, "once")
    assert out["status"] == "refused" and "changed after it was shown" in out["message"]
    b, _ = park(db, user, "send_email", SEND)
    plugins.assigned_systems(db, user.id)
    row = db.scalars(select(models.InstalledPlugin).where(
        models.InstalledPlugin.user_id == user.id, models.InstalledPlugin.plugin_id == "core-inbox")).one()
    row.enabled = False
    db.commit()
    out = answer(db, user, b, "once")
    assert out["status"] == "refused" and "not turned on" in out["message"]


def test_recheck_after_folder_removed(device):
    tmp, db = device
    user = mk(db)
    folder = tmp / "proj"
    folder.mkdir()
    victim = folder / "old.txt"
    victim.write_text("x")
    real = os.path.realpath(str(folder))
    row = models.DeviceFolder(user_id=user.id, path=real, path_key=approvals.path_key(real), can_delete=False,
                              source="user")
    db.add(row)
    db.commit()
    d = gate._make(kind="ask", source="always_user", reason_code="NO_DELETE", reason="Deleting needs permission.",
                   tool="delete_file", args_digest=gate.args_digest({"path": str(victim)}),
                   card="delete_permission", card_payload={"path": real}, options=("once", "reject"),
                   always_user=True, user_id=user.id)
    a = approvals.queue_action(db, user, make_ctx(db, user, "chat"),
                               ToolCall("", "delete_file", {"path": str(victim)}), d)
    row.revoked_at = utcnow()  # the folder was disconnected while the card waited
    db.commit()
    out = answer(db, user, a, "approve")
    assert out["status"] == "refused" and "no longer connected" in out["message"]
    assert victim.exists()


# ---------------------------------------------------------------------------
# Special cards
# ---------------------------------------------------------------------------


def _special(db, user, card, tool, args, payload):
    d = gate._make(kind="ask", source="always_user", reason_code="CARD", reason="Needs you.", tool=tool,
                   args_digest=gate.args_digest(args), card=card, card_payload=payload,
                   options=("once", "reject"), always_user=True, user_id=user.id)
    return approvals.queue_action(db, user, make_ctx(db, user, "chat"), ToolCall("", tool, args), d)


def test_folder_card_connects_then_runs(device):
    tmp, db = device
    user = mk(db)
    db.query(models.DeviceFolder).delete()  # a user with no connected folder yet
    db.commit()
    folder = tmp / "newdir"
    folder.mkdir()
    args = {"path": str(folder / "n.txt"), "content": "hello"}
    a = _special(db, user, "folder_access", "write_file", args, {"path": str(folder)})
    out = answer(db, user, a, "approve")
    assert out["decision"] == "once" and (folder / "n.txt").read_text() == "hello"
    row = db.scalars(select(models.DeviceFolder).where(models.DeviceFolder.user_id == user.id)).one()
    assert row.path == os.path.realpath(str(folder)) and row.can_delete is False and row.source == "request"
    assert row.path_key == approvals.path_key(row.path)
    assert "folder_added" in [e.kind for e in events.listing(db, user.id)]


def test_folder_card_is_undone_when_the_recheck_refuses(device):
    tmp, db = device
    user = mk(db)
    db.query(models.DeviceFolder).delete()  # a user with no connected folder yet
    db.commit()
    folder = tmp / "blocked"
    folder.mkdir()
    args = {"path": str(folder / "n.txt"), "content": "hello"}
    a = _special(db, user, "folder_access", "write_file", args, {"path": str(folder)})
    rules.add(db, user, effect="block", system="device-control", tool="write_file")
    out = answer(db, user, a, "approve")
    assert out["status"] == "refused" and not (folder / "n.txt").exists()
    row = db.scalars(select(models.DeviceFolder).where(models.DeviceFolder.user_id == user.id)).one()
    assert row.revoked_at is not None, "a refused card must not leave the folder connected"


def test_folder_card_refuses_a_missing_folder_and_hosted_installs(device, monkeypatch):
    tmp, db = device
    user = mk(db)
    db.query(models.DeviceFolder).delete()  # a user with no connected folder yet
    db.commit()
    a = _special(db, user, "folder_access", "write_file", {"path": "x", "content": ""}, {"path": str(tmp / "ghost")})
    assert answer(db, user, a, "approve")["status"] == "refused"
    b = _special(db, user, "folder_access", "write_file", {"path": "y", "content": ""}, {"path": str(tmp)})
    monkeypatch.setattr(settings, "device_control", "off", raising=False)
    assert answer(db, user, b, "approve")["status"] == "refused"
    assert db.scalars(select(models.DeviceFolder)).first() is None


def test_delete_permission_card(device):
    tmp, db = device
    user = mk(db)
    folder = tmp / "docs"
    folder.mkdir()
    victim = folder / "gone.txt"
    victim.write_text("bye")
    real = os.path.realpath(str(folder))
    row = models.DeviceFolder(user_id=user.id, path=real, path_key=approvals.path_key(real), can_delete=False)
    db.add(row)
    db.commit()
    a = _special(db, user, "delete_permission", "delete_file", {"path": str(victim)}, {"path": real})
    out = answer(db, user, a, "approve")
    assert out["decision"] == "once"
    db.refresh(row)
    assert row.can_delete is True and not victim.exists()


@pytest.mark.parametrize(
    "app_id,tier,ok",
    [("chrome", "click", True), ("vscode", "click", True), ("vscode", "full", False),
     ("terminal", "full", False), ("chrome", "root", False), ("", "view", False)],
)
def test_app_grant_card(device, app_id, tier, ok):
    tmp, db = device
    user = mk(db)
    a = _special(db, user, "app_grant", "take_screenshot", {}, {"app_id": app_id, "tier": tier, "display_name": "App"})
    out = answer(db, user, a, "approve")
    grants = db.scalars(select(models.DeviceAppGrant).where(models.DeviceAppGrant.user_id == user.id)).all()
    if ok:
        assert [(g.app_id, g.tier) for g in grants] == [(app_id, tier)] and out["decision"] == "once"
    else:
        assert out["status"] == "refused" and not grants


def test_app_grant_card_refuses_a_blocked_app(device):
    tmp, db = device
    user = mk(db)
    db.add(models.DeviceAppBlock(user_id=user.id, app_id="bank", match="keyword"))
    db.commit()
    a = _special(db, user, "app_grant", "take_screenshot", {}, {"app_id": "my-bank-app", "tier": "view"})
    assert answer(db, user, a, "approve")["status"] == "refused"


# ---------------------------------------------------------------------------
# Who may answer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("caller", [Caller("remote", "dev-1")])
def test_restricted_callers_say_once_or_no_only(env, caller):
    _, db = env
    user = mk(db)
    a, _ = park(db, user, "send_email", SEND)
    for what in ("window", "always"):
        with pytest.raises(ApprovalError) as e:
            answer(db, user, a, what, caller)
        assert e.value.status_code == 403 and "longer" in e.value.detail
        assert status_of(db, a) == "pending"
    answer(db, user, a, "once", caller)
    assert status_of(db, a) == "approved" and a.resolved_by == caller.resolved_by
    assert db.scalars(select(PermissionRule)).first() is None


def test_remote_device_once_only(env):
    _, db = env
    user = mk(db)
    a, _ = park(db, user, "send_email", SEND)
    answer(db, user, a, "once", Caller("remote", "phone-7"))
    assert a.resolved_by == "remote:phone-7"


@pytest.mark.parametrize("caller", [Caller("remote", "d")])
def test_always_user_card_remote_403(device, caller):
    tmp, db = device
    user = mk(db)
    victim = tmp / "v.txt"
    victim.write_text("x")
    a, _ = park(db, user, "delete_file", {"path": str(victim)})
    with pytest.raises(ApprovalError) as e:
        answer(db, user, a, "once", caller)
    assert e.value.status_code == 403 and e.value.detail == "Answer this on the computer."
    assert victim.exists() and status_of(db, a) == "pending"
    # saying no is always allowed
    answer(db, user, a, "reject", caller)
    assert status_of(db, a) == "rejected"
    b = _special(db, user, "folder_access", "write_file", {"path": "p", "content": ""}, {"path": str(tmp)})
    with pytest.raises(ApprovalError) as e:
        answer(db, user, b, "approve", caller)
    assert e.value.status_code == 403


def test_the_owners_session_may_answer_everything(env):
    _, db = env
    user = mk(db)
    a, _ = park(db, user, "send_email", SEND)
    answer(db, user, a, "always", Caller("session"))
    assert status_of(db, a) == "approved"


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


@pytest.fixture
def hook(monkeypatch):
    """A stand-in for agent.taskloop with the hook the loop will provide."""
    calls = []
    mod = types.ModuleType("app.agent.taskloop")

    def on_pending_resolved(db, action, result):
        calls.append((action.id, action.status, result))
        return {"id": action.work_item_id, "status": "ready"}

    mod.on_pending_resolved = on_pending_resolved
    monkeypatch.setitem(sys.modules, "app.agent.taskloop", mod)
    return calls


def test_expire_due_calls_hook(env, hook):
    _, db = env
    user = mk(db)
    with_item, _ = park(db, user, "send_email", SEND, work_item_id="w-1")
    without, _ = park(db, user, "send_email", {**SEND, "to": "c@c.com"})
    fresh, _ = park(db, user, "send_email", {**SEND, "to": "d@d.com"})
    for a in (with_item, without):
        a.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert approvals.expire_due(db) == 2
    assert [status_of(db, a) for a in (with_item, without, fresh)] == ["expired", "expired", "pending"]
    assert hook == [(with_item.id, "expired", None)], "only a parked run needs telling"
    assert with_item.resolved_by == "system"
    assert approvals.expire_due(db) == 0 and len(hook) == 1, "idempotent"
    assert ("approval_expired", "expired") in [(e.kind, e.outcome) for e in events.listing(db, user.id)]


def test_answering_an_overdue_card_expires_it(env, hook):
    _, db = env
    user = mk(db)
    a, _ = park(db, user, "send_email", SEND, work_item_id="w-2")
    a.expires_at = utcnow() - timedelta(seconds=5)
    db.commit()
    with pytest.raises(ApprovalError) as e:
        answer(db, user, a, "once")
    assert e.value.status_code == 409 and "expired" in e.value.detail and e.value.body()["status"] == "expired"
    assert hook and hook[0][1] == "expired"


def test_a_dead_claim_is_settled(env, hook):
    _, db = env
    user = mk(db)
    a, _ = park(db, user, "send_email", SEND, work_item_id="w-3")
    a.status = "resolving"
    a.updated_at = utcnow() - timedelta(minutes=6)
    young, _ = park(db, user, "send_email", {**SEND, "to": "y@y.com"})
    young.status = "resolving"
    db.commit()
    assert approvals.expire_due(db) == 1
    assert status_of(db, a) == "refused" and status_of(db, young) == "resolving"
    assert hook[0][1] == "refused" and hook[0][2]["status"] == "refused"


# ---------------------------------------------------------------------------
# The task-loop hook
# ---------------------------------------------------------------------------


def test_resolve_executes_and_hook_writes_result(env, hook):
    _, db = env
    user = mk(db)
    task = Task(user_id=user.id, title="Bye")
    db.add(task)
    db.commit()
    a, _ = park(db, user, "delete_task", {"task_id": task.id}, work_item_id="w-9")
    out = answer(db, user, a, "once")
    assert db.get(Task, task.id) is None, "the approved tool ran inside the request"
    assert out["work_item"] == {"id": "w-9", "status": "ready"}
    assert len(hook) == 1 and hook[0][0] == a.id and hook[0][1] == "approved"
    assert isinstance(hook[0][2], dict) and "image" not in hook[0][2]
    # reject and refuse also report to the hook
    b, _ = park(db, user, "delete_task", {"task_id": "x"}, work_item_id="w-9")
    answer(db, user, b, "reject")
    assert hook[-1][1] == "rejected"
    c, _ = park(db, user, "send_email", SEND, work_item_id="w-9")
    rules.add(db, user, effect="block", system="core-inbox", tool="send_email")
    answer(db, user, c, "once")
    assert hook[-1][1] == "refused" and "reason" in hook[-1][2]


def test_a_failing_hook_never_undoes_the_answer(env, monkeypatch):
    _, db = env
    user = mk(db)
    mod = types.ModuleType("app.agent.taskloop")

    def boom(db, action, result):
        raise RuntimeError("loop is down")

    mod.on_pending_resolved = boom
    monkeypatch.setitem(sys.modules, "app.agent.taskloop", mod)
    task = Task(user_id=user.id, title="Bye")
    db.add(task)
    db.commit()
    a, _ = park(db, user, "delete_task", {"task_id": task.id}, work_item_id="w-1")
    out = answer(db, user, a, "once")
    assert out["work_item"] is None and status_of(db, a) == "approved" and db.get(Task, task.id) is None


def test_without_a_loop_module_the_work_item_is_none(env, monkeypatch):
    _, db = env
    monkeypatch.setitem(sys.modules, "app.agent.taskloop", None)  # as if the module did not exist
    user = mk(db)
    task = Task(user_id=user.id, title="Bye")
    db.add(task)
    db.commit()
    a, _ = park(db, user, "delete_task", {"task_id": task.id}, work_item_id="w-1")
    assert answer(db, user, a, "once")["work_item"] is None


# ---------------------------------------------------------------------------
# The API: shapes and callers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    from app.db import engine
    from app.main import app
    from tests.schema import drop_test_schema, ensure_test_schema

    drop_test_schema(engine)
    ensure_test_schema(engine)
    with TestClient(app) as c:
        yield c
    drop_test_schema(engine)


@pytest.fixture(scope="module")
def session(client):
    resp = client.post("/api/auth/demo", json={"email": "approvals@aura.ai", "name": "Approvals"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"], resp.json()["user"]["id"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _api_park(user_id, to="api@example.com", **ctxkw):
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        user = db.get(models.User, user_id)
        action, _ = park(db, user, "send_email", {**SEND, "to": to}, **ctxkw)
        return action.id
    finally:
        db.close()


def test_pending_actions_have_the_clients_shape(client, session):
    token, uid = session
    aid = _api_park(uid, conversation_id="conv-42", work_item_id="w-7")
    rows = client.get("/api/pending-actions", headers=_bearer(token)).json()
    row = next(r for r in rows if r["id"] == aid)
    for key in ("id", "tool_name", "skill_code", "arguments", "preview", "status", "created_at", "card", "options",
                "expires_at", "origin", "reason", "work_item_id", "conversation_id", "card_payload", "always_user"):
        assert key in row, key
    assert row["conversation_id"] == "conv-42" and row["work_item_id"] == "w-7"
    assert row["card"] == "tool" and row["options"] == ["once", "window", "always", "reject"]
    assert row["origin"] == "chat" and row["always_user"] is False and row["status"] == "pending"
    assert row["card_payload"]["rule_text"].startswith("Always allow sending email")
    assert "rule_draft" not in row["card_payload"]
    assert row["expires_at"] and row["reason"]
    one = client.get(f"/api/pending-actions/{aid}", headers=_bearer(token)).json()
    assert one["id"] == aid and one["conversation_id"] == "conv-42"
    assert client.get("/api/pending-actions/nope", headers=_bearer(token)).status_code == 404


def test_listing_expires_overdue_cards(client, session):
    from app.db import SessionLocal

    token, uid = session
    aid = _api_park(uid, to="late@example.com")
    db = SessionLocal()
    try:
        db.get(PendingAction, aid).expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()
    rows = client.get("/api/pending-actions", headers=_bearer(token)).json()
    assert aid not in [r["id"] for r in rows]
    assert client.get(f"/api/pending-actions/{aid}", headers=_bearer(token)).json()["status"] == "expired"


def test_double_click_over_http_is_409_with_the_state(client, session):
    token, uid = session
    aid = _api_park(uid, to="twice@example.com")
    first = client.post(f"/api/pending-actions/{aid}", headers=_bearer(token), json={"decision": "once"})
    assert first.status_code == 200 and first.json()["decision"] == "once" and "work_item" in first.json()
    second = client.post(f"/api/pending-actions/{aid}", headers=_bearer(token), json={"decision": "once"})
    assert second.status_code == 409
    assert second.json()["status"] == "approved" and second.json()["decision"] == "once" and second.json()["detail"]


def test_the_caller_is_read_from_the_request():
    from types import SimpleNamespace

    from app.api.assistant import _caller
    from app.security import create_access_token

    from app.security import decode_access_token

    def req(device=None, authorization=""):
        claims = decode_access_token(authorization.split(" ", 1)[1]) if authorization else None
        return SimpleNamespace(state=SimpleNamespace(remote_device_id=device, token_claims=claims),
                               headers={"authorization": authorization})

    assert _caller(req("dev-1")) == Caller("remote", "dev-1")
    assert _caller(req(None, "Bearer " + create_access_token("u", {"amr": "local-key"}))) == Caller("session")
    assert _caller(req(None, "Bearer " + create_access_token("u", {"amr": "password"}))) == Caller("session")
    assert _caller(req(None, "Bearer " + create_access_token("u"))) == Caller("session")
    assert _caller(req(None, "")) == Caller("session")


def test_approve_is_accepted_over_http_for_special_cards_only(client, session):
    token, uid = session
    aid = _api_park(uid, to="approve@example.com")
    assert client.post(f"/api/pending-actions/{aid}", headers=_bearer(token), json={"decision": "approve"}).status_code == 422
    assert client.post(f"/api/pending-actions/{aid}", headers=_bearer(token), json={"decision": "reject"}).status_code == 200


# ---------------------------------------------------------------------------
# The terminal client
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body
        self.text = ""

    def json(self):
        return self._body


def _cli(monkeypatch, answers, body=None):
    import aura_cli

    sent, streamed = [], []
    c = aura_cli.Aura("http://127.0.0.1:1")
    monkeypatch.setattr(c, "_request", lambda method, path, **kw: sent.append((method, path, kw.get("json"))) or _Resp(body or {"message": "done"}))
    monkeypatch.setattr(c, "_stream", lambda path, payload, convo: streamed.append((path, payload)))
    monkeypatch.setattr("builtins.input", lambda prompt="": answers.pop(0))
    return c, sent, streamed


@pytest.mark.parametrize("typed,expected", [("y", "once"), ("", "once"), ("a", "reject"), ("10", "reject"), ("n", "reject")])
def test_cli_offers_once_or_no_only(monkeypatch, capsys, typed, expected):
    c, sent, _ = _cli(monkeypatch, [typed])
    c._approve({"id": "p1", "preview": "Send it", "options": ["once", "window", "always", "reject"]})
    assert sent == [("POST", "/api/pending-actions/p1", {"decision": expected})]
    out = capsys.readouterr().out
    assert "[y] once  [n] no" in out and "Open AURA to allow this for longer" in out


def test_cli_leaves_the_always_ask_cards_to_the_app(monkeypatch, capsys):
    c, sent, _ = _cli(monkeypatch, ["y"])
    c._approve({"id": "p2", "preview": "Delete it", "always_user": True, "options": ["once", "reject"]})
    assert sent == [] and "AURA app" in capsys.readouterr().out
    c, sent, _ = _cli(monkeypatch, ["n"])
    c._approve({"id": "p3", "preview": "Connect", "card": "folder_access", "options": ["approve", "reject"]})
    assert sent == [("POST", "/api/pending-actions/p3", {"decision": "reject"})]


def test_cli_continues_a_task_that_is_ready(monkeypatch):
    # An answer reports the task it freed; the CLI carries it on once the stream's approvals are done.
    c, sent, streamed = _cli(monkeypatch, ["y"], {"message": "ok", "work_item": {"id": "w1", "status": "ready"}})
    turn = __import__("aura_cli").Turn()
    turn.approvals.append({"pending_action_id": "p4", "work_item_id": "w1", "preview": "Go", "options": ["once", "reject"]})
    assert c._settle(turn) == "w1"
    c, sent, streamed = _cli(monkeypatch, ["y"], {"message": "ok", "work_item": {"id": "w1", "status": "waiting"}})
    turn = __import__("aura_cli").Turn()
    turn.approvals.append({"pending_action_id": "p5", "work_item_id": "w1", "preview": "Go", "options": ["once", "reject"]})
    assert c._settle(turn) is None
