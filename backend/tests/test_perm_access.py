"""Connected folders: the fence, its cards, changes without restart, the legacy roots (spec 10 sections 8.2, 10)."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.device import files  # noqa: E402
from app.device.runtime import DeviceUnavailable, reset_cache  # noqa: E402
from app.llm import ToolCall  # noqa: E402
from app.models import DeviceFolder, UserSettings, utcnow  # noqa: E402
from app.permissions import access, approvals, gate  # noqa: E402
from tests.perm_helpers import apply_legacy, connect, ctx, make_session, make_user  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine, db = make_session(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(settings, "device_control", "on", raising=False)
    monkeypatch.setattr(settings, "local_mode", True, raising=False)
    monkeypatch.setattr(settings, "aura_home", str(tmp_path / "aura-home"), raising=False)
    monkeypatch.setattr(settings, "device_allowed_roots", "", raising=False)
    monkeypatch.setattr(settings, "device_command_denylist", "", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(gate, "_dialect", lambda policy: "posix")
    reset_cache()
    access.reset_state()
    yield tmp_path, db
    db.close()
    engine.dispose()
    reset_cache()


def go(db, user, tool, args, trigger="chat", **kw):
    return gate.decide(db, user, tool, args, ctx(db, user, trigger, **kw))


def code(d):
    return d.kind, d.reason_code


def user_with(db, mode="ask", migrated=True):
    return make_user(db, mode=mode, migrated=migrated)


def test_unbound_file_layer_has_no_folders(env):
    tmp, db = env
    (tmp / "a.txt").write_text("x")
    with pytest.raises(DeviceUnavailable):
        files.resolve(str(tmp / "a.txt"))
    user = user_with(db)
    connect(db, user, tmp)
    with access.bound(db, user):
        assert files.resolve(str(tmp / "a.txt"))
        (tmp / "b").mkdir()
    with pytest.raises(DeviceUnavailable):
        files.resolve(str(tmp / "a.txt"))


def test_hosted_install_has_no_folders_and_touches_nothing(env, monkeypatch):
    tmp, db = env
    user = user_with(db)
    connect(db, user, tmp)
    monkeypatch.setattr(settings, "device_control", "off", raising=False)
    monkeypatch.setattr(settings, "local_mode", False, raising=False)
    assert access.snapshot(db, user) is access.EMPTY
    # nothing was seeded or written: the hosted site never touches these tables
    assert "app_blocklist_seeded" not in (db.scalars(select(UserSettings)).first().extra or {})
    assert code(go(db, user, "read_file", {"path": str(tmp / "a.txt")})) == ("deny", "UNKNOWN_TOOL") or \
        go(db, user, "read_file", {"path": str(tmp / "a.txt")}).kind == "deny"


def test_outside_a_connected_folder_asks_in_chat_and_denies_elsewhere(env):
    tmp, db = env
    user = user_with(db, "skip")
    work = tmp / "work"
    (work / "deep").mkdir(parents=True)
    d = go(db, user, "write_file", {"path": str(work / "deep" / "new.txt"), "content": "x"})
    assert code(d) == ("ask", "NEEDS_FOLDER") and d.card == "folder_access" and d.always_user
    assert d.card_payload == {"path": os.path.realpath(str(work / "deep"))}
    # a path whose folder does not exist yet: the nearest existing parent is offered
    d = go(db, user, "write_file", {"path": str(work / "nope" / "n.txt"), "content": "x"})
    assert d.card_payload["path"] == os.path.realpath(str(work))
    # reads too, in any mode
    assert go(db, user, "read_file", {"path": str(work / "x")}).card == "folder_access"
    for trigger in ("schedule", "heartbeat", "inbound"):
        d = go(db, user, "write_file", {"path": str(work / "x"), "content": "y"}, trigger, origin_id="s", approval_mode="ask")
        assert d.kind == "deny", trigger
    d = go(db, user, "read_file", {"path": str(work / "x")}, "schedule", origin_id="s", approval_mode="ask")
    assert code(d) == ("deny", "FLOOR_OUTSIDE_FOLDERS")
    # a whole drive is never offered
    assert code(go(db, user, "read_file", {"path": "/definitely-not-here/x"})) == ("deny", "FLOOR_OUTSIDE_FOLDERS")


def test_changes_apply_to_the_next_call(env):
    tmp, db = env
    user = user_with(db, "skip")
    target = {"path": str(tmp / "work" / "a.txt"), "content": "x"}
    (tmp / "work").mkdir()
    assert go(db, user, "write_file", target).card == "folder_access"
    row = connect(db, user, tmp / "work", can_delete=False)
    assert code(go(db, user, "write_file", target)) == ("allow", "MODE_SKIP")
    row.revoked_at = utcnow()
    db.commit()
    assert go(db, user, "write_file", target).card == "folder_access"


def test_a_block_rule_beats_the_card(env):
    from app.permissions import rules

    tmp, db = env
    user = user_with(db, "ask")
    rules.add(db, user, effect="block", system="device-control", tool="write_file")
    assert code(go(db, user, "write_file", {"path": str(tmp / "z.txt"), "content": "x"})) == ("deny", "RULE_BLOCK")


def test_delete_permission(env):
    tmp, db = env
    user = user_with(db, "skip")
    docs = tmp / "docs"
    docs.mkdir()
    victim = docs / "gone.txt"
    victim.write_text("x")
    row = connect(db, user, docs, can_delete=False)
    d = go(db, user, "delete_file", {"path": str(victim)})
    assert code(d) == ("ask", "NEEDS_DELETE_PERMISSION") and d.card == "delete_permission"
    assert d.card_payload == {"path": os.path.realpath(str(docs))}
    # moving out of the folder counts as a delete of the source
    d = go(db, user, "move_file", {"source": str(victim), "destination": str(docs / "b.txt")})
    assert d.card == "delete_permission"
    # elsewhere: denied
    d = go(db, user, "delete_file", {"path": str(victim)}, "automation", origin_id="a", approval_mode="ask")
    assert d.kind == "deny"
    # answering the card turns delete on and deletes (one click)
    c = ctx(db, user, "chat")
    d = gate.decide(db, user, "delete_file", {"path": str(victim)}, c)
    a = approvals.queue_action(db, user, c, ToolCall("", "delete_file", {"path": str(victim)}), d)
    assert a.options == ["approve", "reject"] and a.card_payload["path"] == os.path.realpath(str(docs))
    approvals.resolve(db, user, a.id, "approve")
    db.refresh(row)
    assert row.can_delete is True and not victim.exists()
    # deleting is still always-user every time
    (docs / "two.txt").write_text("y")
    d = go(db, user, "delete_file", {"path": str(docs / "two.txt")})
    assert code(d) == ("ask", "ALWAYS_USER") and d.card == "tool"


def test_delete_in_an_unconnected_folder_connects_and_allows_deleting_after_one_click(env):
    tmp, db = env
    user = user_with(db, "skip")
    fresh = tmp / "fresh"
    fresh.mkdir()
    victim = fresh / "v.txt"
    victim.write_text("x")
    c = ctx(db, user, "chat")
    d = gate.decide(db, user, "delete_file", {"path": str(victim)}, c)
    assert d.card == "delete_permission" and d.card_payload.get("connect") is True
    a = approvals.queue_action(db, user, c, ToolCall("", "delete_file", {"path": str(victim)}), d)
    approvals.resolve(db, user, a.id, "approve")
    row = db.scalars(select(DeviceFolder)).one()
    assert row.can_delete and row.source == "request" and not victim.exists()


def test_shell_needs_a_folder(env):
    tmp, db = env
    user = user_with(db, "skip")
    assert code(go(db, user, "run_command", {"command": "echo hi"})) == ("deny", "FLOOR_NO_FOLDERS")
    d = go(db, user, "run_command", {"command": "echo hi", "workdir": str(tmp)})
    assert d.card == "folder_access" and d.card_payload["path"] == os.path.realpath(str(tmp))
    connect(db, user, tmp)
    assert go(db, user, "run_command", {"command": "echo hi"}).kind == "allow"
    assert go(db, user, "run_command", {"command": "echo hi", "workdir": str(tmp)}).kind == "allow"


def test_the_install_limit_is_a_ceiling(env, monkeypatch):
    tmp, db = env
    user = user_with(db, "skip")
    inside, outside = tmp / "inside", tmp / "outside"
    inside.mkdir()
    outside.mkdir()
    monkeypatch.setattr(settings, "device_allowed_roots", str(inside), raising=False)
    connect(db, user, tmp)  # connected, but wider than the install allows: inert
    assert code(go(db, user, "read_file", {"path": str(outside / "x")})) == ("deny", "FLOOR_OUTSIDE_FOLDERS")
    d = go(db, user, "read_file", {"path": str(inside / "x")})
    assert d.card == "folder_access" or d.kind == "allow"
    snap = access.snapshot(db, user)
    assert snap.folders[0].within_limit is False and snap.roots == ()
    connect(db, user, inside)
    assert go(db, user, "read_file", {"path": str(inside / "x")}).kind == "allow"


def test_nested_folders_the_most_specific_decides_delete(env):
    tmp, db = env
    user = user_with(db, "skip")
    (tmp / "a" / "b").mkdir(parents=True)
    connect(db, user, tmp / "a", can_delete=True)
    connect(db, user, tmp / "a" / "b", can_delete=False)
    snap = access.snapshot(db, user)
    assert snap.folder_for(os.path.realpath(str(tmp / "a" / "b" / "f"))).can_delete is False
    assert snap.folder_for(os.path.realpath(str(tmp / "a" / "f"))).can_delete is True


def test_legacy_roots_become_folders_when_the_migration_runs(env, monkeypatch):
    tmp, db = env
    root = tmp / "root"
    root.mkdir()
    monkeypatch.setattr(settings, "device_allowed_roots", str(root), raising=False)
    user = user_with(db, "skip", migrated=False)  # not through pm02 yet
    assert access.snapshot(db, user).folders == (), "nothing is derived at read time any more"
    apply_legacy(db, user)
    snap = access.snapshot(db, user)
    real = os.path.realpath(str(root))
    assert [(f.path, f.path_key, f.can_delete, f.source) for f in snap.folders] == [
        (real, access.path_key(real), True, "legacy")
    ]
    row = db.scalars(select(DeviceFolder)).one()
    assert row.revoked_at is None and row.id == snap.folders[0].id
    row.revoked_at = utcnow()
    db.commit()
    assert access.snapshot(db, user).folders == ()


def test_blocklist_defaults_and_matching(env):
    _, db = env
    user = user_with(db)
    snap = access.snapshot(db, user)
    for app, hit in (("chase-bank-app", True), ("coinbase", True), ("1password", True), ("keychain-access", True),
                     ("passwords", True), ("keychain-access-helper", False), ("chrome", False), ("notes", False)):
        assert snap.blocked(app) is hit, app
