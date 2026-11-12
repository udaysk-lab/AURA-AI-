"""The 1.0 to 2.0 mapping, revision pm02 (spec 10 section 12, decision 14).

The central test proves that nobody's behaviour changes: for a table of calls under each old trust
level, the gate's decisions before the mapping (computed the way the read-time derivation did, see
legacy_oracle.py) equal its decisions after the rows are written. The rest is one test per row of the
spec's table, plus idempotency, folders, parked approvals and the audit note.
"""

from __future__ import annotations

import os
from datetime import timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import models  # noqa: E402
from app.config import settings  # noqa: E402
from app.device import net  # noqa: E402
from app.device.runtime import reset_cache  # noqa: E402
from app.models import (  # noqa: E402
    ApprovalGrant, AutomationRule, DeviceAppBlock, DeviceFolder, PendingAction, PermissionEvent, PermissionRule,
    Schedule, UserSettings, utcnow,
)
from app.permissions import access, gate, legacy  # noqa: E402
from tests import legacy_oracle  # noqa: E402
from tests.perm_helpers import apply_legacy, decide, make_session, make_user, pm02  # noqa: E402

TIERS = ("strict", "conservative", "relaxed", "full")
SEND = {"to": "bob@example.com", "subject": "Hi", "body": "x"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine, db = make_session(tmp_path)
    monkeypatch.setattr(settings, "device_control", "off", raising=False)
    monkeypatch.setattr(settings, "local_mode", False, raising=False)
    reset_cache()
    yield tmp_path, db
    db.close()
    engine.dispose()


@pytest.fixture
def device(env, monkeypatch):
    tmp, db = env
    root = tmp / "work"
    root.mkdir()
    monkeypatch.setattr(settings, "device_control", "on", raising=False)
    monkeypatch.setattr(settings, "local_mode", True, raising=False)
    monkeypatch.setattr(settings, "aura_home", str(tmp / "aura-home"), raising=False)
    monkeypatch.setattr(settings, "device_allowed_roots", str(root), raising=False)
    monkeypatch.setattr(settings, "device_command_denylist", "", raising=False)
    monkeypatch.setattr(settings, "device_exec_mode", "ask", raising=False)
    monkeypatch.setattr(settings, "device_exec_allowlist", "", raising=False)
    monkeypatch.setattr(gate, "_dialect", lambda policy: "posix")
    monkeypatch.setattr(net, "resolve", lambda host, port: ["93.184.216.34"])
    reset_cache()
    yield root, db
    reset_cache()


def rules_of(db, user, **where):
    q = select(PermissionRule).where(PermissionRule.user_id == user.id)
    for k, v in where.items():
        q = q.where(getattr(PermissionRule, k) == v)
    return db.scalars(q).all()


def keyset(rows):
    return {(r.effect, r.system, r.tool, r.target_kind, r.pattern, r.applies_to) for r in rows}


# ---------------------------------------------------------------------------
# Identical behaviour before and after
# ---------------------------------------------------------------------------


EVENT = {"title": "t", "start": "2030-01-01T10:00:00Z", "end": "2030-01-01T11:00:00Z"}


def call_table(root):
    """(label, tool, args, trigger kwargs): reads, AURA-workspace writes, external writes, irreversible
    actions, files, shell, app, clipboard and fetch, in chat and on each unattended trigger."""
    chat: dict = {}
    sched = {"trigger": "schedule", "approval_mode": "ask"}
    auto = {"trigger": "automation", "approval_mode": "ask"}
    hb = {"trigger": "heartbeat"}
    inbound = {"trigger": "inbound", "origin_id": "email"}
    f = str(root / "a.txt")
    out = [
        ("read", "list_tasks", {}, chat),
        ("read-mail", "search_emails", {"query": "x"}, chat),
        ("ws-create", "create_task", {"title": "x"}, chat),
        ("ws-complete", "complete_task", {"task_id": "t"}, chat),
        ("ws-memory", "save_memory", {"content": "x"}, chat),
        ("ws-notify", "notify", {"title": "x", "body": "y"}, chat),
        ("ws-delegate", "delegate_task", {"task": "x", "assignee": "bob"}, chat),
        ("ws-delete", "delete_task", {"task_id": "t"}, chat),
        ("send-bob", "send_email", SEND, chat),
        ("send-eve", "send_email", {**SEND, "to": "eve@evil.org"}, chat),
        ("send-two", "send_email", {**SEND, "to": "bob@example.com, eve@evil.org"}, chat),
        ("archive", "archive_email", {"email_id": "e"}, chat),
        ("event", "create_event", EVENT, chat),
        ("event-attendee", "create_event", {**EVENT, "attendees": ["bob@example.com"]}, chat),
        ("event-delete", "delete_event", {"event_id": "e"}, chat),
        ("file-read", "read_file", {"path": f}, chat),
        ("file-write", "write_file", {"path": f, "content": "x"}, chat),
        ("file-write-out", "write_file", {"path": str(root.parent / "elsewhere.txt"), "content": "x"}, chat),
        ("file-edit", "edit_file", {"path": f, "find": "a", "replace": "b"}, chat),
        ("file-mkdir", "make_directory", {"path": str(root / "sub")}, chat),
        ("file-delete", "delete_file", {"path": f}, chat),
        ("cmd-git", "run_command", {"command": "git status -s"}, chat),
        ("cmd-compound", "run_command", {"command": "git status && git push"}, chat),
        ("cmd-make", "run_command", {"command": "make build"}, chat),
        ("cmd-rm", "run_command", {"command": "rm -rf build"}, chat),
        ("cmd-subst", "run_command", {"command": "ls $(echo)"}, chat),
        ("cmd-bg", "run_in_background", {"command": "git status"}, chat),
        ("cmd-sqlite", "run_command", {"command": "sqlite3 aura.db"}, chat),
        ("app-open", "open_app", {"name": "notepad"}, chat),
        ("app-close", "close_app", {"name": "notepad"}, chat),
        ("clip-set", "set_clipboard", {"text": "x"}, chat),
        ("fetch", "fetch_url", {"url": "https://docs.example.com/a"}, chat),
        ("fetch-local", "fetch_url", {"url": "http://127.0.0.1:8765/api/x"}, chat),
        ("kill", "stop_process", {"session_id": "x"}, chat),
    ]
    out += [(f"sched-{label}", tool, args, sched) for label, tool, args, _ in out[:36]
            if tool in ("send_email", "create_event", "create_task", "write_file", "run_command", "fetch_url", "open_app", "delete_task", "list_tasks")]
    out += [(f"auto-{label}", tool, args, auto) for label, tool, args, _ in out[:36]
            if tool in ("send_email", "create_event", "create_task", "write_file", "fetch_url", "delete_event", "open_app", "delete_task", "run_command")]
    out += [(f"hb-{label}", tool, args, hb) for label, tool, args, _ in out[:36]
            if tool in ("create_task", "notify", "list_tasks", "send_email")]
    out += [(f"in-{label}", tool, args, inbound) for label, tool, args, _ in out[:36]
            if tool in ("send_email", "create_task", "create_event", "run_command", "fetch_url")]
    return out


def shape(d):
    return (d.kind, d.reason_code, d.source, d.always_user, d.card, tuple(d.options))


def decisions(db, user, table, origins):
    out = {}
    for label, tool, args, kw in table:
        kw = dict(kw)
        trigger = kw.pop("trigger", "chat")
        if trigger in ("schedule", "automation"):
            kw["origin_id"] = origins[trigger]
        out[label] = shape(decide(db, user, tool, args, trigger=trigger, instruction="do it", **kw))
    return out


def seed_user(db, tier, root):
    """A 1.0 user at this level with grants, a schedule and an automation, as a real account would have."""
    user = make_user(db, email=f"{tier}@example.com", level=tier)
    db.add_all([
        ApprovalGrant(user_id=user.id, tool_name="send_email", scope="always", target="bob@example.com"),
        ApprovalGrant(user_id=user.id, tool_name="send_email", scope="always", target=""),
        ApprovalGrant(user_id=user.id, tool_name="delete_event", scope="window", expires_at=utcnow() + timedelta(minutes=30)),
        ApprovalGrant(user_id=user.id, tool_name="archive_email", scope="window", expires_at=utcnow() - timedelta(minutes=30)),
        ApprovalGrant(user_id=user.id, tool_name="delete_task", scope="always", revoked=True),
        ApprovalGrant(user_id=user.id, tool_name="delete_file", scope="always"),
        ApprovalGrant(user_id=user.id, tool_name="open_app", scope="always"),
        ApprovalGrant(user_id=user.id, tool_name="run_in_background", scope="always"),
    ])
    sched = Schedule(user_id=user.id, name="S", prompt="p")
    auto = AutomationRule(user_id=user.id, name="A", actions=[
        {"tool": "create_event", "arguments": EVENT}, {"tool": "send_email", "arguments": SEND},
        {"tool": "delete_event", "arguments": {"event_id": "e"}}, {"tool": "write_file", "arguments": {}},
        {"tool": "fetch_url", "arguments": {}}, {"tool": "create_task", "arguments": {}},
    ])
    db.add_all([sched, auto])
    db.commit()
    return user, {"schedule": sched.id, "automation": auto.id}


EXEC_VARIANTS = [("ask", ""), ("allowlist", "git status,ls,make"), ("full", "")]


@pytest.mark.parametrize("exec_mode,allowlist", EXEC_VARIANTS)
@pytest.mark.parametrize("tier", TIERS)
def test_decisions_are_identical_before_and_after_the_mapping(device, monkeypatch, tier, exec_mode, allowlist):
    root, db = device
    monkeypatch.setattr(settings, "device_exec_mode", exec_mode, raising=False)
    monkeypatch.setattr(settings, "device_exec_allowlist", allowlist, raising=False)
    user, origins = seed_user(db, tier, root)
    table = call_table(root)
    assert len(table) > 80

    with legacy_oracle.read_time_derivation(monkeypatch):
        before = decisions(db, user, table, origins)
    assert db.scalars(select(PermissionRule)).all() == [], "the oracle writes nothing"
    assert db.scalars(select(DeviceFolder)).all() == []

    apply_legacy(db, user)
    after = decisions(db, user, table, origins)

    assert {k: v for k, v in after.items() if before[k] != v} == {}, "behaviour changed for some call"
    assert after == before
    # the table really exercised every outcome, so equality means something
    kinds = {v[0] for v in before.values()}
    assert kinds == {"allow", "ask", "deny"}
    assert {v[1] for v in before.values()} >= {"RULE_ALLOW", "MODE_ASK", "DEFAULT_READ", "ALWAYS_USER", "TRIGGER_DENIED"}
    if tier == "strict":
        assert before["ws-create"][1] == "RULE_ASK"
    if tier == "full":
        assert before["send-eve"][1] == "MODE_SKIP" and before["sched-send-eve"][1] == "RULE_ALLOW"
    if tier == "relaxed":
        assert before["file-write"][1] == "RULE_ALLOW"


def test_a_user_with_no_grants_schedules_or_settings_row_maps_cleanly(env):
    _, db = env
    user = models.User(email="bare@example.com", name="Bare")
    db.add(user)
    db.commit()
    summary = pm02().map_legacy(db.connection(), {"device_control": False, "allowed_roots": [], "exec_mode": "ask",
                                                  "exec_allowlist": [], "command_denylist": []}, only_user_id=user.id)
    db.commit()
    assert summary[user.id]["rules"] == 0 and rules_of(db, user) == []
    assert decide(db, user, "send_email", SEND).reason_code == "MODE_ASK"


# ---------------------------------------------------------------------------
# One test per row of the spec's table
# ---------------------------------------------------------------------------


def test_strict_asks_before_aura_writes(env):
    _, db = env
    user = make_user(db, level="strict")
    apply_legacy(db, user)
    rows = rules_of(db, user)
    assert {(r.effect, r.tool, r.system, r.target_kind, r.pattern, r.applies_to, r.source) for r in rows} == {
        ("ask", t, s, "", "", "", "legacy") for t, s in [
            ("create_task", "core-tasks"), ("complete_task", "core-tasks"), ("save_memory", "core-memory"),
            ("notify", "core-planning"), ("delegate_task", "delegation"), ("chase_delegation", "delegation"),
            ("desktop_notify", "device-control")]
    }
    assert gate.stored_mode(db, user) == "ask"


def test_conservative_has_no_rules(env):
    _, db = env
    user = make_user(db, level="conservative")
    apply_legacy(db, user)
    assert rules_of(db, user) == [] and gate.stored_mode(db, user) == "ask"
    assert decide(db, user, "create_task", {"title": "x"}).reason_code == "DEFAULT_WORKSPACE"  # decision 3a


def test_relaxed_allows_what_it_ran_alone(env):
    _, db = env
    user = make_user(db, level="relaxed")
    apply_legacy(db, user)
    assert {r.tool for r in rules_of(db, user)} == set(pm02().RELAXED_ALLOW)
    assert {r.effect for r in rules_of(db, user)} == {"allow"} and gate.stored_mode(db, user) == "ask"


def test_full_is_skip_with_schedule_rules_for_each_assigned_system(env):
    _, db = env
    user = make_user(db, level="full")
    s1, s2 = Schedule(user_id=user.id, name="A", prompt="p"), Schedule(user_id=user.id, name="B", prompt="p")
    db.add_all([s1, s2])
    db.commit()
    apply_legacy(db, user)
    assert gate.stored_mode(db, user) == "skip" and gate.mode_label(db, user) == "Skip approvals"
    rows = rules_of(db, user)
    core = {"core-calendar", "core-inbox", "core-memory", "core-planning", "core-tasks"}
    assert keyset(rows) == {("allow", sysname, None, "", "", f"schedule:{s.id}") for s in (s1, s2) for sysname in core}
    # a schedule made after the upgrade is not covered: it has its own approval mode
    s3 = Schedule(user_id=user.id, name="C", prompt="p")
    db.add(s3)
    db.commit()
    assert decide(db, user, "send_email", SEND, trigger="schedule", origin_id=s3.id, approval_mode="ask").kind == "ask"
    assert decide(db, user, "send_email", SEND, trigger="schedule", origin_id=s1.id, approval_mode="ask").reason_code == "RULE_ALLOW"


def test_full_schedule_rules_follow_the_systems_assigned(device):
    _, db = device
    user = make_user(db, level="full")
    db.add(Schedule(user_id=user.id, name="A", prompt="p"))
    db.add(models.InstalledPlugin(user_id=user.id, plugin_id="research", enabled=True))
    db.add(models.InstalledPlugin(user_id=user.id, plugin_id="documents", enabled=False))
    db.commit()
    apply_legacy(db, user)
    assert {r.system for r in rules_of(db, user)} == {
        "core-calendar", "core-inbox", "core-memory", "core-planning", "core-tasks", "device-control", "research"}


def test_automation_rules_for_what_the_old_engine_ran_alone(env):
    _, db = env
    user = make_user(db, level="strict")
    auto = AutomationRule(user_id=user.id, name="A", actions=[
        {"tool": "create_event"}, {"tool": "delete_event"}, {"tool": "send_email"}, {"tool": "create_task"},
        {"tool": "fetch_url"}, {"tool": "nonsense"}, "junk"])
    db.add(auto)
    db.commit()
    apply_legacy(db, user)
    mine = rules_of(db, user, applies_to=f"automation:{auto.id}")
    assert {(r.tool, r.system, r.effect) for r in mine} == {("create_event", "core-calendar", "allow"), ("fetch_url", "device-control", "allow")}


def test_grants(env):
    _, db = env
    user = make_user(db, level="conservative")
    soon = utcnow() + timedelta(minutes=20)
    db.add_all([
        ApprovalGrant(user_id=user.id, tool_name="send_email", scope="always", target="Bob@Example.com"),
        ApprovalGrant(user_id=user.id, tool_name="send_email", scope="always", target=""),
        ApprovalGrant(user_id=user.id, tool_name="send_email", scope="always", target="not an address"),
        ApprovalGrant(user_id=user.id, tool_name="open_app", scope="always"),
        ApprovalGrant(user_id=user.id, tool_name="delete_event", scope="window", expires_at=soon),
        ApprovalGrant(user_id=user.id, tool_name="archive_email", scope="window", expires_at=utcnow() - timedelta(minutes=1)),
        ApprovalGrant(user_id=user.id, tool_name="delete_task", scope="always", revoked=True),
        ApprovalGrant(user_id=user.id, tool_name="delete_file", scope="always"),
        ApprovalGrant(user_id=user.id, tool_name="gone_tool", scope="always"),
    ])
    db.commit()
    summary = apply_legacy(db, user)
    got = {(r.tool, r.target_kind, r.pattern): r for r in rules_of(db, user)}
    assert set(got) == {
        ("send_email", "recipient", "bob@example.com"), ("open_app", "", ""), ("delete_event", "", ""),
    }
    assert got[("open_app", "", "")].expires_at is None
    assert abs((got[("delete_event", "", "")].expires_at.replace(tzinfo=None) - soon.replace(tzinfo=None)).total_seconds()) < 1
    assert summary["grants_not_carried"] == ["delete_file"]


def test_a_grant_for_delete_file_is_not_carried_and_the_delete_still_asks(device):
    root, db = device
    user = make_user(db, level="full")
    db.add(ApprovalGrant(user_id=user.id, tool_name="delete_file", scope="always"))
    db.commit()
    apply_legacy(db, user)
    assert rules_of(db, user, tool="delete_file") == []
    d = decide(db, user, "delete_file", {"path": str(root / "a.txt")})
    assert (d.kind, d.reason_code) == ("ask", "ALWAYS_USER")


def test_exec_full_allowlist_and_denylist(device, monkeypatch):
    root, db = device
    shell = {"run_command", "run_in_background"}
    monkeypatch.setattr(settings, "device_exec_mode", "full", raising=False)
    monkeypatch.setattr(settings, "device_command_denylist", "Format C:, shutdown", raising=False)
    user = make_user(db, email="a@example.com")
    apply_legacy(db, user)
    assert keyset(rules_of(db, user, effect="allow")) == {("allow", "device-control", t, "", "", "") for t in shell}
    assert keyset(rules_of(db, user, effect="block")) == {
        ("block", "device-control", t, "command", "~" + e, "") for t in shell for e in ("format c:", "shutdown")}
    assert decide(db, user, "run_command", {"command": "make build"}).reason_code == "RULE_ALLOW"
    assert decide(db, user, "run_command", {"command": "shutdown -h now"}).kind == "deny"

    monkeypatch.setattr(settings, "device_exec_mode", "allowlist", raising=False)
    monkeypatch.setattr(settings, "device_exec_allowlist", "git status,  ls ,~bad", raising=False)
    monkeypatch.setattr(settings, "device_command_denylist", "", raising=False)
    other = make_user(db, email="b@example.com")
    apply_legacy(db, other)
    assert keyset(rules_of(db, other)) == {("allow", "device-control", t, "command", p, "") for t in shell for p in ("git status", "ls")}


def test_exec_settings_are_ignored_where_device_control_is_off(env, monkeypatch):
    _, db = env
    monkeypatch.setattr(settings, "device_exec_mode", "full", raising=False)
    user = make_user(db)
    apply_legacy(db, user)
    assert rules_of(db, user) == []


def test_roots_to_folders(device):
    root, db = device
    user = make_user(db, level="relaxed")
    summary = apply_legacy(db, user)
    real = os.path.realpath(str(root))
    (row,) = db.scalars(select(DeviceFolder).where(DeviceFolder.user_id == user.id)).all()
    assert (row.path, row.path_key, bool(row.can_delete), row.source, row.revoked_at) == (real, access.path_key(real), True, "legacy", None)
    assert summary["folders"] == 1
    # the same key the read-time derivation used, so nothing is listed twice
    assert [f.path_key for f in access.snapshot(db, user).folders] == [access.path_key(real)]


def test_blank_roots_mean_the_home_folder(device, monkeypatch):
    root, db = device
    monkeypatch.setattr(settings, "device_allowed_roots", "", raising=False)
    user = make_user(db)
    apply_legacy(db, user)
    (row,) = db.scalars(select(DeviceFolder).where(DeviceFolder.user_id == user.id)).all()
    assert row.path == os.path.realpath(os.path.expanduser("~")) and row.can_delete


def test_no_device_rows_on_a_hosted_install(env):
    _, db = env
    user = make_user(db, level="full")
    apply_legacy(db, user)
    assert db.scalars(select(DeviceFolder)).all() == [] and db.scalars(select(DeviceAppBlock)).all() == []


def test_the_blocklist_is_seeded_once(device):
    root, db = device
    user = make_user(db)
    apply_legacy(db, user)
    seeded = db.scalars(select(DeviceAppBlock).where(DeviceAppBlock.user_id == user.id)).all()
    assert {(b.app_id, b.match) for b in seeded} == set(access.DEFAULT_BLOCKS) and {b.source for b in seeded} == {"default"}
    assert dict(pm02().DEFAULT_BLOCKS) == dict(access.DEFAULT_BLOCKS)
    assert db.scalars(select(UserSettings).where(UserSettings.user_id == user.id)).one().extra["app_blocklist_seeded"] == 1
    assert access.seed_blocklist(db, user.id) == 0


def test_old_pending_expired(env):
    _, db = env
    user = make_user(db)
    now = utcnow()
    mk = lambda **kw: PendingAction(user_id=user.id, tool_name="send_email", arguments=SEND, status="pending", **kw)  # noqa: E731
    fresh_chat, fresh_auto, stale = mk(conversation_id="c1"), mk(), mk(conversation_id="c1")
    done = PendingAction(user_id=user.id, tool_name="send_email", arguments=SEND, status="approved")
    db.add_all([fresh_chat, fresh_auto, stale, done])
    db.commit()
    for row, age in ((fresh_chat, 2), (fresh_auto, 3), (stale, 30), (done, 40)):
        row.created_at = now - timedelta(hours=age)
    db.commit()
    summary = apply_legacy(db, user)
    db.expire_all()
    assert (summary["pending_given_expiry"], summary["pending_expired"]) == (3, 1)
    assert (fresh_chat.status, fresh_chat.origin, fresh_chat.options) == ("pending", "chat", ["once", "window", "always", "reject"])
    assert fresh_auto.origin == "automation" and fresh_auto.status == "pending"
    delta = fresh_chat.expires_at.replace(tzinfo=None) - fresh_chat.created_at.replace(tzinfo=None)
    assert delta == timedelta(hours=24)
    assert stale.status == "expired" and stale.resolved_at is not None
    assert done.status == "approved" and done.expires_at is None


def test_nobody_on_auto(env):
    _, db = env
    users = [make_user(db, email=f"{t}@example.com", level=t) for t in TIERS] + [make_user(db, email="odd@example.com", level="trusted")]
    for u in users:
        apply_legacy(db, u)
    modes = {db.scalars(select(UserSettings).where(UserSettings.user_id == u.id)).one().permission_mode for u in users}
    assert modes == {"ask", "skip"}


def test_old_spellings_of_the_level(env):
    _, db = env
    ask, trusted, junk = (make_user(db, email=f"{n}@example.com", level=n) for n in ("ask", "trusted", "weird"))
    for u in (ask, trusted, junk):
        apply_legacy(db, u)
    assert {r.effect for r in rules_of(db, ask)} == {"ask"} and len(rules_of(db, ask)) == 7
    assert {r.tool for r in rules_of(db, trusted)} == set(pm02().RELAXED_ALLOW)
    assert rules_of(db, junk) == []


def test_idempotent(env):
    _, db = env
    user = make_user(db, level="relaxed")
    db.add(ApprovalGrant(user_id=user.id, tool_name="open_app", scope="always"))
    db.commit()
    first = apply_legacy(db, user)
    n = len(rules_of(db, user))
    assert n and first["rules"] == n
    again = pm02().map_legacy(db.connection(), {"device_control": False, "allowed_roots": [], "exec_mode": "ask",
                                                "exec_allowlist": [], "command_denylist": []})
    db.commit()
    assert again == {} and len(rules_of(db, user)) == n, "a user already mapped is skipped"
    # even without the marker, a rule is only inserted when absent, and a revoked one stays revoked
    row = rules_of(db, user, tool="open_app")[0]
    row.revoked_at = utcnow()
    db.commit()
    extra = dict(db.scalars(select(UserSettings).where(UserSettings.user_id == user.id)).one().extra)
    extra.pop("permissions_migrated")
    db.scalars(select(UserSettings).where(UserSettings.user_id == user.id)).one().extra = extra
    db.commit()
    pm02().map_legacy(db.connection(), {"device_control": False, "allowed_roots": [], "exec_mode": "ask",
                                        "exec_allowlist": [], "command_denylist": []}, only_user_id=user.id)
    db.commit()
    assert len(rules_of(db, user)) == n
    assert rules_of(db, user, tool="open_app")[0].revoked_at is not None


def test_a_user_changes_after_the_mapping_are_kept(env):
    """Rules the user revoked or added are theirs; a second upgrade pass never touches them."""
    _, db = env
    user = make_user(db, level="strict")
    apply_legacy(db, user)
    mine = PermissionRule(user_id=user.id, effect="block", system="core-inbox", tool="send_email", source="user")
    db.add(mine)
    db.commit()
    assert pm02().map_legacy(db.connection(), {"device_control": False, "allowed_roots": [], "exec_mode": "ask",
                                               "exec_allowlist": [], "command_denylist": []}) == {}
    assert db.get(PermissionRule, mine.id) is not None


# ---------------------------------------------------------------------------
# The audit note
# ---------------------------------------------------------------------------


def test_the_mapping_is_recorded_in_the_log_once(env):
    _, db = env
    user = make_user(db, level="relaxed")
    apply_legacy(db, user)
    row = db.scalars(select(UserSettings).where(UserSettings.user_id == user.id)).one()
    assert row.extra["permissions_migrated"]["from_tier"] == "relaxed" and len(row.extra["legacy_events_pending"]) == 1
    assert legacy.emit_pending_events(db, user) == 1
    assert legacy.emit_pending_events(db, user) == 0
    (event,) = db.scalars(select(PermissionEvent).where(PermissionEvent.user_id == user.id)).all()
    assert (event.kind, event.actor) == ("legacy_mapped", "migration")
    assert event.detail["tier"] == "relaxed" and event.detail["rules"] == len(pm02().RELAXED_ALLOW)
    from app.permissions import events

    assert events.verify(db, user.id)["ok"]
    db.refresh(row)
    assert "legacy_events_pending" not in row.extra and row.extra["permissions_migrated"]


def test_the_frozen_tables_match_the_catalogue(env):
    from app.permissions import catalogue

    mod = pm02()
    assert mod.TOOL_SYSTEM == {n: p.system for n, p in catalogue.CATALOGUE.items() if not p.loop_internal}
    assert set(mod.CORE_SYSTEMS) == {"core-calendar", "core-inbox", "core-memory", "core-planning", "core-tasks"}
    from app import plugins

    assert set(mod.CORE_SYSTEMS) == plugins.CORE_IDS
    # what the old engine ran alone is what the new defaults would have asked for
    asked = {n for n, p in catalogue.CATALOGUE.items() if not p.loop_internal and catalogue.default_disposition(p.kind, "ask") != "allow"}
    ran_alone = {n for n in asked if legacy_oracle.risk_class(n) in ("safe", "external")}
    assert ran_alone == set(mod.AUTOMATION_RAN_ALONE)
    assert mod.path_key("/Some/Path") == access.path_key("/Some/Path")
