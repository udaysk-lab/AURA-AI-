"""Screen floors, app grants and the blocklist (spec 10 sections 8.4 and 10.3)."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.agent.coordinator import execute_tool  # noqa: E402
from app.config import settings  # noqa: E402
from app.device import screen, windows  # noqa: E402
from app.device.runtime import reset_cache  # noqa: E402
from app.models import DeviceAppBlock, DeviceAppGrant, PermissionEvent  # noqa: E402
from app.permissions import access, gate  # noqa: E402
from tests.perm_helpers import ctx, make_session, make_user  # noqa: E402

Front = windows.Frontmost


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine, db = make_session(tmp_path)
    monkeypatch.setattr(settings, "device_control", "on", raising=False)
    monkeypatch.setattr(settings, "local_mode", True, raising=False)
    monkeypatch.setattr(settings, "device_screen_control", True, raising=False)
    monkeypatch.setattr(settings, "aura_home", str(tmp_path / "aura-home"), raising=False)
    monkeypatch.setattr(settings, "device_allowed_roots", "", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(gate, "_dialect", lambda policy: "posix")
    reset_cache()
    access.reset_state()
    user = make_user(db, migrated=True, mode="skip")
    yield db, user
    access.reset_state()
    db.close()
    engine.dispose()
    reset_cache()


def front(monkeypatch, app="notes", title="A note", pid=999_999, name=None):
    win = Front(app, title, pid, name or app)
    monkeypatch.setattr(windows, "frontmost", lambda: win)
    # F22: a click lands on the window under the pointer; here that is the front one unless a test says otherwise.
    monkeypatch.setattr(windows, "window_at", lambda x, y: win)
    monkeypatch.setattr(windows, "pointer_position", lambda: (10, 10))


def here(user):
    access.note_presence(user.id)


def grant(db, user, app, tier):
    db.add(DeviceAppGrant(user_id=user.id, app_id=app, display_name=app, tier=tier))
    db.commit()


def go(db, user, tool, args=None, trigger="chat", **kw):
    return gate.decide(db, user, tool, args or {}, ctx(db, user, trigger, **kw))


def code(d):
    return d.kind, d.reason_code


def test_not_present_denied(env, monkeypatch):
    db, user = env
    front(monkeypatch)
    grant(db, user, "notes", "full")
    assert code(go(db, user, "take_screenshot")) == ("deny", "NOT_PRESENT")
    here(user)
    assert go(db, user, "take_screenshot").kind == "allow"
    # presence lapses after 90 seconds
    monkeypatch.setattr(access, "_now", lambda: access._presence[user.id] + 91)
    assert code(go(db, user, "take_screenshot")) == ("deny", "NOT_PRESENT")


def test_frontmost_unknown_denied(env, monkeypatch):
    db, user = env
    monkeypatch.setattr(windows, "frontmost", lambda: None)
    here(user)
    assert code(go(db, user, "take_screenshot")) == ("deny", "FLOOR_FRONTMOST_UNKNOWN")
    assert code(go(db, user, "type_text", {"text": "x"})) == ("deny", "FLOOR_FRONTMOST_UNKNOWN")


@pytest.mark.parametrize("app,title,pid", [
    ("notes", "Inbox · AURA", 4242),       # the web app's page title
    ("aura", "AURA", 4242),                # the desktop launcher
    ("notes", "Anything", os.getpid()),    # AURA's own process
])
def test_aura_window_denied(env, monkeypatch, app, title, pid):
    db, user = env
    front(monkeypatch, app, title, pid)
    grant(db, user, app, "full")
    here(user)
    assert code(go(db, user, "mouse_click", {"x": 1, "y": 1})) == ("deny", "FLOOR_AURA_WINDOW")


def test_blocked_app_denied(env, monkeypatch):
    db, user = env
    front(monkeypatch, "my-bank-app")
    here(user)
    assert code(go(db, user, "take_screenshot")) == ("deny", "FLOOR_BLOCKED_APP")
    # a grant cannot override the blocklist
    grant(db, user, "my-bank-app", "full")
    assert code(go(db, user, "take_screenshot")) == ("deny", "FLOOR_BLOCKED_APP")
    # open/close/kill follow the same list
    assert code(go(db, user, "open_app", {"name": "1Password"})) == ("deny", "FLOOR_BLOCKED_APP")
    assert code(go(db, user, "close_app", {"name": "Keychain Access"})) == ("deny", "FLOOR_BLOCKED_APP")
    assert code(go(db, user, "open_app", {"name": "AURA"})) == ("deny", "FLOOR_AURA_WINDOW")


def test_default_blocklist_is_seeded_once_and_deleted_stays_deleted(env):
    db, user = env
    access.snapshot(db, user)
    rows = db.scalars(select(DeviceAppBlock).where(DeviceAppBlock.user_id == user.id)).all()
    ids = {r.app_id: r.match for r in rows}
    assert ids["bank"] == "keyword" and ids["keychain-access"] == "app" and "metamask" in ids and "bitwarden" in ids
    db.delete(next(r for r in rows if r.app_id == "bank"))
    db.commit()
    access.snapshot(db, user)
    assert "bank" not in {r.app_id for r in db.scalars(select(DeviceAppBlock).where(DeviceAppBlock.user_id == user.id))}


def test_terminal_type_denied(env, monkeypatch):
    db, user = env
    front(monkeypatch, "windows-terminal")
    here(user)
    grant(db, user, "windows-terminal", "click")
    assert code(go(db, user, "type_text", {"text": "rm -rf"})) == ("deny", "FLOOR_APP_TIER")
    assert code(go(db, user, "press_keys", {"keys": "enter"})) == ("deny", "FLOOR_APP_TIER")
    assert go(db, user, "scroll_screen", {"amount": 1}).kind in ("allow", "ask")
    # even with no grant at all, typing into an IDE is refused rather than offered as a card
    front(monkeypatch, "vscode")
    assert code(go(db, user, "type_text", {"text": "x"})) == ("deny", "FLOOR_APP_TIER")


def test_grant_card_in_chat(env, monkeypatch):
    db, user = env
    front(monkeypatch, "chrome", name="Google Chrome")
    here(user)
    d = go(db, user, "mouse_click", {"x": 1, "y": 1})
    assert code(d) == ("ask", "NEEDS_APP_GRANT") and d.card == "app_grant" and d.always_user
    assert d.card_payload == {"app_id": "chrome", "display_name": "Google Chrome", "tier": "click"}
    assert go(db, user, "take_screenshot").card_payload["tier"] == "view"
    assert go(db, user, "type_text", {"text": "x"}).card_payload["tier"] == "full"
    # a tier below the need is a deny, not a card
    grant(db, user, "chrome", "view")
    assert code(go(db, user, "mouse_click", {"x": 1, "y": 1})) == ("deny", "FLOOR_APP_TIER")
    assert go(db, user, "take_screenshot").kind == "allow"


def test_grant_changes_apply_without_restart(env, monkeypatch):
    db, user = env
    front(monkeypatch, "chrome")
    here(user)
    assert go(db, user, "mouse_click", {"x": 1, "y": 1}).kind == "ask"
    grant(db, user, "chrome", "click")
    assert go(db, user, "mouse_click", {"x": 1, "y": 1}).card is None
    row = db.scalars(select(DeviceAppGrant)).one()
    from app.models import utcnow

    row.revoked_at = utcnow()
    db.commit()
    assert go(db, user, "mouse_click", {"x": 1, "y": 1}).card == "app_grant"


def test_schedule_screen_denied(env, monkeypatch):
    db, user = env
    front(monkeypatch)
    here(user)
    grant(db, user, "notes", "full")
    for trigger in ("schedule", "automation", "inbound", "heartbeat", "user_ui"):
        d = go(db, user, "take_screenshot", trigger=trigger, origin_id="x", approval_mode="ask")
        assert d.kind == "deny", trigger


def test_the_users_switch_and_the_install_ceiling(env, monkeypatch):
    db, user = env
    front(monkeypatch)
    here(user)
    grant(db, user, "notes", "full")
    assert go(db, user, "take_screenshot").kind == "allow"
    access.set_screen_control_user(db, user, False)
    assert code(go(db, user, "take_screenshot")) == ("deny", "FLOOR_SCREEN_OFF")
    access.set_screen_control_user(db, user, True)
    monkeypatch.setattr(settings, "device_screen_control", False, raising=False)
    assert code(go(db, user, "take_screenshot")) == ("deny", "INSTALL_OFF")


def test_post_click_suspend(env, monkeypatch):
    """After input, if AURA's window came to the front, screen control pauses for 60 seconds."""
    db, user = env
    state = {"app": "notes", "title": "A note"}
    monkeypatch.setattr(windows, "frontmost", lambda: Front(state["app"], state["title"], 999_999, state["app"]))
    monkeypatch.setattr(windows, "window_at",
                        lambda x, y: Front(state["app"], state["title"], 999_999, state["app"]))
    here(user)
    grant(db, user, "notes", "full")

    def click(*a, **k):
        state.update(app="chrome", title="Approvals · AURA")  # the click landed on AURA's own card
        return {"message": "clicked"}

    monkeypatch.setattr(screen, "click", click)
    args = {"x": 5, "y": 5}
    d = go(db, user, "mouse_click", args)
    assert d.kind == "allow"
    execute_tool(db, user, "mouse_click", args, decision=d)
    assert access.screen_suspended(user.id)
    assert code(go(db, user, "take_screenshot")) == ("deny", "FLOOR_SCREEN_SUSPENDED")
    kinds = [e.kind for e in db.scalars(select(PermissionEvent).where(PermissionEvent.user_id == user.id))]
    assert "self_change_blocked" in kinds
    assert access.recent_synthetic_input(user.id)  # the cooldown for answering approvals starts


def test_the_window_must_not_change_between_decision_and_action(env, monkeypatch):
    db, user = env
    front(monkeypatch, "notes")
    here(user)
    grant(db, user, "notes", "full")
    d = go(db, user, "mouse_click", {"x": 1, "y": 1})
    front(monkeypatch, "chrome")
    called = []
    monkeypatch.setattr(screen, "click", lambda *a, **k: called.append(1) or {})
    out = execute_tool(db, user, "mouse_click", {"x": 1, "y": 1}, decision=d)
    assert "error" in out and not called
