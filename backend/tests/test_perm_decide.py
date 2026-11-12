"""decide(): the single decision function, in the spec's order (spec 10 section 4.3)."""

from __future__ import annotations

import os
from datetime import timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import models, plugins  # noqa: E402
from app.config import settings  # noqa: E402
from app.device.runtime import reset_cache  # noqa: E402
from app.models import ApprovalGrant, PermissionEvent, utcnow  # noqa: E402
from app.permissions import events, gate, reviewer, rules  # noqa: E402
from tests.perm_helpers import ctx, decide, make_session, make_user  # noqa: E402

SEND = {"to": "bob@example.com", "subject": "Hi", "body": "x"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine, db = make_session(tmp_path)
    monkeypatch.setattr(settings, "device_control", "off", raising=False)
    monkeypatch.setattr(settings, "local_mode", False, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    reset_cache()
    yield tmp_path, db
    db.close()
    engine.dispose()


@pytest.fixture
def device(env, monkeypatch):
    tmp, _db = env
    monkeypatch.setattr(settings, "device_control", "on", raising=False)
    monkeypatch.setattr(settings, "local_mode", True, raising=False)
    monkeypatch.setattr(settings, "aura_home", str(tmp / "aura-home"), raising=False)
    monkeypatch.setattr(settings, "device_command_denylist", "", raising=False)
    monkeypatch.setattr(settings, "device_exec_mode", "ask", raising=False)
    # The install's folder limit; users made by mk() get it as a connected folder (as pm02 will do).
    monkeypatch.setattr(settings, "device_allowed_roots", str(tmp), raising=False)
    # Command rules are tested in the POSIX dialect on every OS (the parser has its own tests).
    monkeypatch.setattr(gate, "_dialect", lambda policy: "posix")
    reset_cache()
    yield tmp
    reset_cache()


def mk(db, mode="ask", **kw):
    """A user who has been through the legacy mapping, so the stored mode is the truth."""
    return make_user(db, mode=mode, migrated=True, **kw)


def codes(d):
    return (d.kind, d.reason_code)


@pytest.mark.parametrize("mode", ["ask", "auto", "skip"])
def test_read_allow_all_modes(env, mode):
    _, db = env
    user = mk(db, mode)
    for tool, args in (("list_tasks", {}), ("search_emails", {"query": "x"}), ("get_daily_briefing", {})):
        assert codes(decide(db, user, tool, args)) == ("allow", "DEFAULT_READ"), tool


@pytest.mark.parametrize("mode", ["ask", "auto", "skip"])
def test_workspace_allow_all_modes(env, mode):
    """Decision 3a: AURA's own workspace never prompts by default."""
    _, db = env
    user = mk(db, mode)
    plugins.install(db, user.id, "delegation")
    for tool, args in (
        ("create_task", {"title": "t"}), ("complete_task", {"task_id": "x"}), ("save_memory", {"content": "x"}),
        ("notify", {"title": "a", "body": "b"}), ("delegate_task", {"task": "t", "assignee": "a"}),
    ):
        assert codes(decide(db, user, tool, args)) == ("allow", "DEFAULT_WORKSPACE"), tool


def test_delete_task_asks_in_ask(env):
    _, db = env
    user = mk(db, "ask")
    d = decide(db, user, "delete_task", {"task_id": "x"})
    assert codes(d) == ("ask", "MODE_ASK") and d.source == "mode"
    assert d.options == ("once", "window", "always", "reject")
    assert d.proposed_rule and d.proposed_rule.tool == "delete_task" and d.proposed_rule.items == ()
    assert codes(decide(db, mk(db, "skip", email="s@example.com"), "delete_task", {"task_id": "x"})) == ("allow", "MODE_SKIP")


def test_skip_allows_change_and_auto_without_a_reviewer_asks(env):
    _, db = env
    assert codes(decide(db, mk(db, "skip"), "send_email", SEND)) == ("allow", "MODE_SKIP")
    other = mk(db, "auto", email="a@example.com")
    assert not reviewer.available()
    assert codes(decide(db, other, "send_email", SEND)) == ("ask", "MODE_ASK")
    assert gate.mode_label(db, other) == "Auto (acting as Ask first)"


def test_block_rule_beats_allow(env):
    _, db = env
    user = mk(db, "skip")
    rules.add(db, user, effect="allow", system="core-inbox", tool="send_email")
    r = rules.add(db, user, effect="block", system="core-inbox", tool="send_email", target_kind="recipient", pattern="@example.com")
    d = decide(db, user, "send_email", SEND)
    assert codes(d) == ("deny", "RULE_BLOCK") and d.rule_id == r.id and d.source == "rule"
    # a different recipient still goes
    assert codes(decide(db, user, "send_email", {**SEND, "to": "ann@other.org"})) == ("allow", "RULE_ALLOW")
    # block holds for any mode, even on a read tool
    rules.add(db, user, effect="block", system="core-tasks", tool="list_tasks")
    assert codes(decide(db, user, "list_tasks", {})) == ("deny", "RULE_BLOCK")


def test_ask_rule_prompts_in_skip(env):
    _, db = env
    user = mk(db, "skip")
    r = rules.add(db, user, effect="ask", system="core-inbox", tool="send_email", target_kind="recipient", pattern="bob@example.com")
    rules.add(db, user, effect="allow", system="core-inbox", tool="send_email")
    d = decide(db, user, "send_email", SEND)
    assert codes(d) == ("ask", "RULE_ASK") and d.rule_id == r.id
    assert codes(decide(db, user, "send_email", {**SEND, "to": "ann@other.org"})) == ("allow", "RULE_ALLOW")


def test_ask_rule_prompts_in_auto_no_review(env, monkeypatch):
    _, db = env
    user = mk(db, "auto")
    called = []
    monkeypatch.setattr(reviewer, "available", lambda: True)
    monkeypatch.setattr(reviewer, "review", lambda *a, **k: called.append(1) or reviewer.ReviewerVerdict(True))
    rules.add(db, user, effect="ask", system="core-inbox", tool="send_email")
    assert codes(decide(db, user, "send_email", SEND)) == ("ask", "RULE_ASK")
    assert not called, "an ask rule is answered by the user, never by the reviewer"


def test_always_user_ignores_allow_and_skip(device):
    tmp = device
    engine, db = make_session(tmp)
    try:
        user = mk(db, "skip")
        rules.add(db, user, effect="allow", system="device-control", tool="delete_file")
        rules.add(db, user, effect="allow", system="device-control")
        target = tmp / "work" / "old.txt"
        d = decide(db, user, "delete_file", {"path": str(target)})
        assert codes(d) == ("ask", "ALWAYS_USER") and d.always_user and d.source == "always_user"
        assert d.options == ("once", "reject") and d.proposed_rule is None
        # a block rule still wins over the question
        rules.add(db, user, effect="block", system="device-control", tool="delete_file", target_kind="path", pattern=str(tmp / "work") + "/**")
        assert codes(decide(db, user, "delete_file", {"path": str(target)})) == ("deny", "RULE_BLOCK")
        # and a shell command that deletes needs the user too, whatever allows it
        user2 = mk(db, "skip", email="two@example.com")
        rules.add(db, user2, effect="allow", system="device-control", tool="run_command")
        d = decide(db, user2, "run_command", {"command": "rm -rf build", "workdir": str(tmp)})
        assert codes(d) == ("ask", "ALWAYS_USER")
    finally:
        db.close()
        engine.dispose()


def test_system_not_assigned_denied_even_if_called(env):
    _, db = env
    user = mk(db, "skip")
    assert codes(decide(db, user, "web_research", {"query": "x"})) == ("deny", "SYSTEM_NOT_ASSIGNED")
    plugins.install(db, user.id, "research")
    assert codes(decide(db, user, "web_research", {"query": "x"})) == ("allow", "DEFAULT_READ")
    plugins.uninstall(db, user.id, "research")
    d = decide(db, user, "web_research", {"query": "x"})
    assert codes(d) == ("deny", "SYSTEM_NOT_ASSIGNED") and d.source == "system"


def test_unknown_tool_bad_args_and_bookkeeping(env):
    _, db = env
    user = mk(db, "skip")
    assert codes(decide(db, user, "rm_rf_everything", {})) == ("deny", "UNKNOWN_TOOL")
    assert codes(decide(db, user, "send_email", {"subject": "x"})) == ("deny", "BAD_ARGS")
    assert codes(decide(db, user, "send_email", "not a dict")) == ("deny", "BAD_ARGS")
    d = decide(db, user, "aura_plan", {"steps": []})
    assert codes(d) == ("allow", "DEFAULT_WORKSPACE")


def test_install_ceiling_when_device_control_is_off(env):
    _, db = env
    user = mk(db, "skip")
    d = decide(db, user, "run_command", {"command": "echo x"})
    assert d.kind == "deny" and d.reason_code in ("INSTALL_OFF", "SYSTEM_NOT_ASSIGNED")


def test_screen_ceiling(device, monkeypatch):
    engine, db = make_session(device)
    try:
        user = mk(db, "skip")
        monkeypatch.setattr(settings, "device_screen_control", False, raising=False)
        assert codes(decide(db, user, "take_screenshot", {})) == ("deny", "INSTALL_OFF")
    finally:
        db.close()
        engine.dispose()


def test_heartbeat_ask_becomes_deny(env):
    _, db = env
    user = mk(db, "skip")
    rules.add(db, user, effect="ask", system="core-tasks", tool="create_task")  # a strict-migrated user's rule
    d = decide(db, user, "create_task", {"title": "x"}, trigger="heartbeat")
    assert codes(d) == ("deny", "HEARTBEAT_NO_ASK")
    # reads and workspace writes go; anything else is not the heartbeat's to do
    assert codes(decide(db, user, "detect_conflicts", {}, trigger="heartbeat")) == ("allow", "DEFAULT_READ")
    assert codes(decide(db, user, "notify", {"title": "a", "body": "b"}, trigger="heartbeat")) == ("allow", "DEFAULT_WORKSPACE")
    assert codes(decide(db, user, "send_email", SEND, trigger="heartbeat")) == ("deny", "TRIGGER_DENIED")
    assert codes(decide(db, user, "delete_task", {"task_id": "x"}, trigger="heartbeat")) == ("deny", "TRIGGER_DENIED")


def test_schedule_uses_approval_mode(env, monkeypatch):
    _, db = env
    user = mk(db, "skip")
    # the user's own mode is skip, but an unattended run never skips
    d = decide(db, user, "send_email", SEND, trigger="schedule", approval_mode="ask", origin_id="s1")
    assert codes(d) == ("ask", "MODE_ASK")
    assert codes(decide(db, user, "send_email", SEND, trigger="schedule", approval_mode=None, origin_id="s1")) == ("ask", "MODE_ASK")
    assert codes(decide(db, user, "send_email", SEND, trigger="chat")) == ("allow", "MODE_SKIP")
    # screen, clipboard and apps are never for a schedule
    for tool, args in (("set_clipboard", {"text": "x"}), ("open_app", {"name": "notes"}), ("take_screenshot", {})):
        assert decide(db, user, tool, args, trigger="schedule", approval_mode="ask", origin_id="s1").kind == "deny"


def test_automation_auto_uses_reviewer(env, monkeypatch):
    _, db = env
    user = mk(db, "ask")
    verdicts = []
    monkeypatch.setattr(reviewer, "available", lambda: True)

    def fake_review(db_, user_, policy, args, targets, c):
        verdicts.append((policy.name, c.trigger))
        return reviewer.ReviewerVerdict(args.get("subject") != "bad", "because", "" if args.get("subject") != "bad" else "REVIEWER_BLOCK", model="m")

    monkeypatch.setattr(reviewer, "review", fake_review)
    kw = dict(trigger="automation", approval_mode="auto", origin_id="rule-1")
    d = decide(db, user, "send_email", SEND, **kw)
    assert codes(d) == ("allow", "REVIEWER_ALLOW") and d.source == "reviewer" and d.reviewer.model == "m"
    d = decide(db, user, "send_email", {**SEND, "subject": "bad"}, **kw)
    assert codes(d) == ("deny", "REVIEWER_BLOCK") and d.source == "reviewer"
    # failure of the reviewer is a block
    monkeypatch.setattr(reviewer, "review", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert codes(decide(db, user, "send_email", SEND, **kw)) == ("deny", "REVIEWER_ERROR")
    monkeypatch.setattr(reviewer, "review", lambda *a, **k: "garbage")
    assert codes(decide(db, user, "send_email", SEND, **kw)) == ("deny", "REVIEWER_ERROR")
    # an automation in `ask` mode never reaches the reviewer
    monkeypatch.setattr(reviewer, "review", lambda *a, **k: pytest.fail("reviewer must not be called"))
    assert codes(decide(db, user, "send_email", SEND, trigger="automation", approval_mode="ask", origin_id="rule-1")) == ("ask", "MODE_ASK")
    # events were written for the reviews
    kinds = [e.kind for e in db.scalars(select(PermissionEvent).where(PermissionEvent.user_id == user.id)).all()]
    assert kinds.count("review") == 4


def test_reviewer_fallback_after_three_in_a_row_or_twenty_in_a_run(env, monkeypatch):
    _, db = env
    user = mk(db, "auto")
    monkeypatch.setattr(reviewer, "available", lambda: True)
    monkeypatch.setattr(reviewer, "review", lambda *a, **k: pytest.fail("not asked once the fallback applies"))
    assert codes(decide(db, user, "send_email", SEND, reviewer_streak=3)) == ("ask", "REVIEWER_FALLBACK")
    assert codes(decide(db, user, "send_email", SEND, reviewer_total=20)) == ("ask", "REVIEWER_FALLBACK")


def test_expired_rule_ignored(env):
    _, db = env
    user = mk(db, "ask")
    rules.add(db, user, effect="allow", system="core-inbox", tool="send_email", expires_at=utcnow() + timedelta(minutes=10))
    assert codes(decide(db, user, "send_email", SEND)) == ("allow", "RULE_ALLOW")
    old = rules.add(db, user, effect="allow", system="core-calendar", tool="delete_event", expires_at=utcnow() - timedelta(minutes=1))
    assert old.id
    assert codes(decide(db, user, "delete_event", {"event_id": "e"})) == ("ask", "MODE_ASK")


def test_revoked_rule_ignored_next_call(env):
    _, db = env
    user = mk(db, "ask")
    r = rules.add(db, user, effect="allow", system="core-inbox", tool="send_email")
    assert decide(db, user, "send_email", SEND).kind == "allow"
    assert rules.revoke(db, user, r.id)
    assert not rules.revoke(db, user, r.id)
    assert codes(decide(db, user, "send_email", SEND)) == ("ask", "MODE_ASK")
    kinds = [e.kind for e in events.listing(db, user.id)]
    assert "rule_added" in kinds and "rule_revoked" in kinds


def test_applies_to_scope(env):
    _, db = env
    user = mk(db, "ask")
    rules.add(db, user, effect="allow", system="core-inbox", tool="send_email", applies_to="schedule:s1")
    kw = dict(trigger="schedule", approval_mode="ask")
    assert codes(decide(db, user, "send_email", SEND, origin_id="s1", **kw)) == ("allow", "RULE_ALLOW")
    assert codes(decide(db, user, "send_email", SEND, origin_id="s2", **kw)) == ("ask", "MODE_ASK")
    assert codes(decide(db, user, "send_email", SEND)) == ("ask", "MODE_ASK")  # not in chat
    with pytest.raises(Exception):
        rules.add(db, user, effect="allow", system="core-inbox", tool="send_email", applies_to="everything")


def test_rule_for_a_target_the_tool_does_not_have_is_skipped(env):
    _, db = env
    user = mk(db, "ask")
    with pytest.raises(Exception):
        rules.add(db, user, effect="allow", system="core-tasks", tool="delete_task", target_kind="recipient", pattern="a@b.co")
    # a stored rule that no longer fits (tool changed) must not widen anything: simulate with a raw row
    db.add(models.PermissionRule(user_id=user.id, effect="allow", system="core-tasks", tool="delete_task", target_kind="recipient", pattern="a@b.co"))
    db.commit()
    assert codes(decide(db, user, "delete_task", {"task_id": "x"})) == ("ask", "MODE_ASK")


def test_allow_needs_every_recipient_and_block_needs_any(env):
    _, db = env
    user = mk(db, "ask")
    rules.add(db, user, effect="allow", system="core-inbox", tool="send_email", target_kind="recipient", pattern="bob@example.com")
    assert codes(decide(db, user, "send_email", SEND)) == ("allow", "RULE_ALLOW")
    d = decide(db, user, "send_email", {**SEND, "to": "bob@example.com, eve@evil.org"})
    assert codes(d) == ("ask", "MODE_ASK")
    assert ("recipient", "bob@example.com") in d.proposed_rule.items and ("recipient", "eve@evil.org") in d.proposed_rule.items


def test_proposed_rules_and_option_sets(device):
    engine, db = make_session(device)
    try:
        user = mk(db, "ask")
        tmp = device
        d = decide(db, user, "write_file", {"path": str(tmp / "work" / "a.txt"), "content": "x"})
        assert codes(d) == ("ask", "MODE_ASK")
        assert d.proposed_rule.items == (("path", os.path.join(os.path.realpath(str(tmp / "work")), "**")),)
        d = decide(db, user, "run_command", {"command": "git status && npm test"})
        assert d.options == ("once", "window", "always", "reject")
        assert d.proposed_rule.items == (("command", "git status"), ("command", "npm test"))
        d = decide(db, user, "run_command", {"command": "echo $(whoami)"})
        assert d.kind == "ask" and d.options == ("once", "reject") and d.proposed_rule is None
        d = decide(db, user, "send_process_input", {"session_id": "x", "text": "y"})
        assert d.kind == "ask" and d.options == ("once", "reject")
        d = decide(db, user, "fetch_url", {"url": "https://docs.example.com/a"})
        assert d.proposed_rule.items == (("domain", "docs.example.com"),)
    finally:
        db.close()
        engine.dispose()


def test_floors_beat_rules_and_modes(device):
    engine, db = make_session(device)
    try:
        user = mk(db, "skip")
        rules.add(db, user, effect="allow", system="*")
        assert decide(db, user, "read_file", {"path": str(device / "work" / "x")}).kind == "allow"
        d = decide(db, user, "read_file", {"path": str(device / "aura-home" / "aura.env")})
        assert codes(d) == ("deny", "FLOOR_SELF_PATH")
        home = device / "home" / ".ssh" / "id_rsa"
        d = decide(db, user, "read_file", {"path": str(home)})
        assert codes(d) == ("deny", "FLOOR_PROTECTED_PATH") and d.source == "floor"
        d = decide(db, user, "run_command", {"command": "curl http://localhost:8765/api/settings"})
        assert codes(d) == ("deny", "FLOOR_SELF_API")
        d = decide(db, user, "fetch_url", {"url": "http://127.0.0.1:8765/api/settings"})
        assert codes(d) == ("deny", "FLOOR_PRIVATE_NETWORK")
    finally:
        db.close()
        engine.dispose()


def test_decisions_are_logged_but_plain_reads_are_not(env):
    _, db = env
    user = mk(db, "ask")
    decide(db, user, "list_tasks", {})
    assert events.listing(db, user.id) == []
    decide(db, user, "send_email", SEND)
    decide(db, user, "create_task", {"title": "x"})
    decide(db, user, "bogus", {})
    rows = list(reversed(events.listing(db, user.id)))
    assert [(e.tool, e.outcome, e.reason_code) for e in rows] == [
        ("send_email", "ask", "MODE_ASK"),
        ("create_task", "allow", "DEFAULT_WORKSPACE"),
        ("bogus", "deny", "UNKNOWN_TOOL"),
    ]
    assert events.verify(db, user.id)["ok"]
    assert all("Hi" not in (e.target + str(e.detail)) for e in rows), "no message content in the log"


# --- until the legacy migration: the 1.0 tier is read at decision time ------------------------


def test_interim_tiers_keep_existing_behaviour(env):
    _, db = env
    strict = make_user(db, "strict@example.com", level="strict")
    cons = make_user(db, "cons@example.com", level="conservative")
    relaxed = make_user(db, "relaxed@example.com", level="relaxed")
    full = make_user(db, "full@example.com", level="full")
    # strict asks before its own writes; the others do not
    assert codes(decide(db, strict, "create_task", {"title": "x"})) == ("ask", "RULE_ASK")
    assert codes(decide(db, cons, "create_task", {"title": "x"})) == ("allow", "DEFAULT_WORKSPACE")
    # relaxed acts alone on external writes but still asks for irreversible ones
    ev = {"title": "t", "start": "2030-01-01T10:00:00Z", "end": "2030-01-01T11:00:00Z"}
    assert codes(decide(db, relaxed, "create_event", ev)) == ("allow", "RULE_ALLOW")
    assert codes(decide(db, cons, "create_event", ev)) == ("ask", "MODE_ASK")
    assert decide(db, relaxed, "send_email", SEND).kind == "ask"
    assert decide(db, relaxed, "delete_event", {"event_id": "e"}).kind == "ask"
    # full does everything the gate allows, schedules included
    assert codes(decide(db, full, "send_email", SEND)) == ("allow", "MODE_SKIP")
    assert codes(decide(db, full, "send_email", SEND, trigger="schedule", approval_mode="ask", origin_id="s")) == ("allow", "RULE_ALLOW")
    assert codes(decide(db, cons, "send_email", SEND, trigger="schedule", approval_mode="ask", origin_id="s")) == ("ask", "MODE_ASK")
    # nobody is put on auto, and nothing was written
    assert db.scalars(select(models.PermissionRule)).all() == []
    assert gate.mode_label(db, full) == "Skip approvals" and gate.mode_label(db, cons) == "Ask first"


def test_interim_grants_and_the_loop_tier(env):
    _, db = env
    user = make_user(db, level="conservative")
    db.add(ApprovalGrant(user_id=user.id, tool_name="send_email", scope="always", target="bob@example.com"))
    db.add(ApprovalGrant(user_id=user.id, tool_name="delete_event", scope="window", expires_at=utcnow() + timedelta(minutes=5)))
    db.add(ApprovalGrant(user_id=user.id, tool_name="archive_email", scope="window", expires_at=utcnow() - timedelta(minutes=5)))
    db.add(ApprovalGrant(user_id=user.id, tool_name="delete_task", scope="always", revoked=True))
    db.commit()
    assert codes(decide(db, user, "send_email", SEND)) == ("allow", "RULE_ALLOW")
    assert decide(db, user, "send_email", {**SEND, "to": "eve@evil.org"}).kind == "ask"
    assert codes(decide(db, user, "delete_event", {"event_id": "e"})) == ("allow", "RULE_ALLOW")
    assert decide(db, user, "archive_email", {"email_id": "e"}).kind == "ask"
    assert decide(db, user, "delete_task", {"task_id": "t"}).kind == "ask"
    # a tier passed by the loop wins over the stored one
    assert codes(decide(db, user, "create_task", {"title": "x"}, legacy_tier="strict")) == ("ask", "RULE_ASK")


def test_interim_stops_once_the_user_is_migrated(env):
    _, db = env
    user = make_user(db, level="full", migrated=True)  # stored mode is ask (the column default)
    assert codes(decide(db, user, "send_email", SEND)) == ("ask", "MODE_ASK")


def test_interim_exec_allowlist(device, monkeypatch):
    engine, db = make_session(device)
    try:
        user = make_user(db, level="conservative")
        monkeypatch.setattr(settings, "device_exec_mode", "allowlist", raising=False)
        monkeypatch.setattr(settings, "device_exec_allowlist", "git status,ls", raising=False)
        assert codes(decide(db, user, "run_command", {"command": "git status -s"})) == ("allow", "RULE_ALLOW")
        assert decide(db, user, "run_command", {"command": "git status && git push"}).kind == "ask"
        assert decide(db, user, "run_command", {"command": "ls; rm x"}).kind == "ask"
        assert decide(db, user, "run_command", {"command": "ls $(echo)"}).kind == "ask"
        monkeypatch.setattr(settings, "device_exec_mode", "full", raising=False)
        assert codes(decide(db, user, "run_command", {"command": "make build"})) == ("allow", "RULE_ALLOW")
        # but a command that deletes still needs the user, and the floors still hold
        assert codes(decide(db, user, "run_command", {"command": "rm -rf build"})) == ("ask", "ALWAYS_USER")
        assert decide(db, user, "run_command", {"command": "sqlite3 aura.db"}).kind == "deny"
    finally:
        db.close()
        engine.dispose()


# --- approval re-check, console, buttons -----------------------------------------------------


def _parked(db, user, tool, args, d, **kw):
    a = models.PendingAction(user_id=user.id, tool_name=tool, arguments=args, args_digest=d.args_digest, **kw)
    db.add(a)
    db.commit()
    return a


def test_approved_call_is_rechecked(env):
    _, db = env
    user = mk(db, "ask")
    d = decide(db, user, "send_email", SEND)
    a = _parked(db, user, "send_email", SEND, d)
    ok = gate.decide_approved(db, user, a)
    assert codes(ok) == ("allow", "APPROVED_BY_USER") and gate.is_sealed(ok)
    # a block rule added after the card was shown wins
    rules.add(db, user, effect="block", system="core-inbox", tool="send_email")
    assert codes(gate.decide_approved(db, user, a)) == ("deny", "RULE_BLOCK")
    # arguments edited in the database after the card
    a.arguments = {**SEND, "to": "eve@evil.org"}
    assert codes(gate.decide_approved(db, user, a)) == ("deny", "ARGS_CHANGED")


def test_approved_schedule_card_keeps_its_scope_and_limits(device):
    engine, db = make_session(device)
    user = mk(db, "ask")
    a = models.PendingAction(user_id=user.id, tool_name="open_app", arguments={"name": "notes"}, origin="schedule", origin_id="s1")
    db.add(a)
    db.commit()
    assert codes(gate.decide_approved(db, user, a)) == ("deny", "TRIGGER_DENIED")


def test_console_and_buttons(device, monkeypatch):
    engine, db = make_session(device)
    try:
        user = mk(db, "ask")
        d = gate.console_decision(db, user, "echo hello")
        assert codes(d) == ("allow", "USER_CONSOLE") and gate.is_sealed(d)
        # the user's own console is not asked about, but block rules and the install denylist hold
        monkeypatch.setattr(settings, "device_command_denylist", "shutdown", raising=False)
        assert codes(gate.console_decision(db, user, "shutdown now")) == ("deny", "FLOOR_DENYLIST")
        monkeypatch.setattr(settings, "device_command_denylist", "", raising=False)
        rules.add(db, user, effect="block", system="device-control", tool="run_command", target_kind="command", pattern="git push")
        assert codes(gate.console_decision(db, user, "git push origin main")) == ("deny", "RULE_BLOCK")
        # buttons: reads only
        assert gate.ui_decision(db, user, "find_free_time", {"duration_minutes": 30, "days": 3}).kind == "allow"
        assert codes(gate.ui_decision(db, user, "send_email", SEND)) == ("deny", "UI_NOT_READ_ONLY")
    finally:
        db.close()
        engine.dispose()


def test_context_rejects_unknown_triggers(env):
    _, db = env
    user = mk(db)
    with pytest.raises(ValueError):
        ctx(db, user, "cron")
    c = ctx(db, user, "chat")
    assert c.run_key and c.reviewer_streak == 0 and not c.present
    assert gate.expiry_for(ctx(db, user, "schedule")) - utcnow() < timedelta(hours=12, seconds=5)
    assert gate.expiry_for(c, "folder_access") - utcnow() < timedelta(minutes=30, seconds=5)
