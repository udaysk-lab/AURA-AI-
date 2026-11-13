"""F22: a click or scroll is judged against the window under the pointer, and the synthetic-input
cool-down covers every state-changing route."""

from __future__ import annotations

import os
import types

import pytest
from fastapi import HTTPException

from app import deps, models, security
from app.agent import device_tools
from app.agent.coordinator import execute_tool
from app.config import settings
from app.device import screen, windows
from app.permissions import access, gate
from tests.perm_helpers import ctx, make_session, make_user
from tests.test_perm_screen import Front, code, env, go, grant, here  # noqa: F401


def setup(monkeypatch, db, user, front_app="notes", under=None, grant_to=("notes",), tier="click"):
    f = Front(front_app, "A note", 999_999, front_app)
    monkeypatch.setattr(windows, "frontmost", lambda: f)
    state = {"under": under}
    monkeypatch.setattr(windows, "window_at", lambda x, y: state["under"])
    monkeypatch.setattr(windows, "pointer_position", lambda: (7, 7))
    here(user)
    for app in grant_to:
        grant(db, user, app, tier)
    return state


def win(app, title="A page", pid=999_998):
    return Front(app, title, pid, app)


@pytest.mark.parametrize("tool,args", [
    ("mouse_click", {"x": 300, "y": 300}), ("mouse_click", {}), ("scroll_screen", {"amount": -3, "x": 300, "y": 300}),
    ("scroll_screen", {"amount": -3}),
])
def test_the_window_under_the_pointer_is_what_counts(env, monkeypatch, tool, args):
    db, user = env
    state = setup(monkeypatch, db, user, under=win("notes"))
    assert go(db, user, tool, args).kind == "allow", "the control: it lands on the granted front app"

    # front app is granted, but the click lands on another app that is not
    state["under"] = win("figma")
    d = go(db, user, tool, args)
    assert d.card == "app_grant" and d.card_payload["app_id"] == "figma", code(d)

    # ... which is on the blocked list
    db.add(models.DeviceAppBlock(user_id=user.id, app_id="figma"))
    db.commit()
    assert code(go(db, user, tool, args)) == ("deny", "FLOOR_BLOCKED_APP")

    # ... or is AURA's own window
    state["under"] = win("notes", title="Inbox · AURA")
    assert code(go(db, user, tool, args)) == ("deny", "FLOOR_AURA_WINDOW")
    state["under"] = win("notes", pid=os.getpid())
    assert code(go(db, user, tool, args)) == ("deny", "FLOOR_AURA_WINDOW")

    # ... or cannot be told
    state["under"] = None
    assert code(go(db, user, tool, args)) == ("deny", "FLOOR_POINT_UNKNOWN")


def test_the_tier_of_the_window_under_the_pointer_is_used(env, monkeypatch):
    db, user = env
    state = setup(monkeypatch, db, user, under=win("term"), grant_to=("notes",))
    grant(db, user, "term", "view")
    d = go(db, user, "mouse_click", {"x": 1, "y": 1})
    assert code(d) == ("deny", "FLOOR_APP_TIER")


def test_keyboard_and_screenshots_still_follow_the_front_window(env, monkeypatch):
    db, user = env
    state = setup(monkeypatch, db, user, under=win("figma"), grant_to=("notes",), tier="full")
    assert go(db, user, "type_text", {"text": "x"}).kind == "allow"
    assert go(db, user, "take_screenshot").kind == "allow"


def test_the_window_under_the_pointer_is_checked_again_when_the_click_happens(env, monkeypatch):
    db, user = env
    state = setup(monkeypatch, db, user, under=win("notes"))
    clicked = []
    monkeypatch.setattr(screen, "click", lambda *a, **k: clicked.append(a) or {"message": "clicked"})
    args = {"x": 10, "y": 10}
    d = go(db, user, "mouse_click", args)
    assert d.kind == "allow"
    state["under"] = win("figma")  # a window moved over the spot between the decision and the click
    out = execute_tool(db, user, "mouse_click", args, decision=d)
    assert "error" in out and clicked == []
    state["under"] = win("notes")
    assert "error" not in execute_tool(db, user, "mouse_click", args, decision=d) and len(clicked) == 1


# --- the cool-down ------------------------------------------------------------------------------------------


@pytest.fixture
def owner(tmp_path, monkeypatch):
    engine, db = make_session(tmp_path)
    monkeypatch.setattr(settings, "local_mode", True, raising=False)
    monkeypatch.setattr(settings, "aura_home", str(tmp_path / "h"), raising=False)
    access.reset_state()
    user = make_user(db, email=deps.local_owner_email())
    token = security.create_access_token(user.id, {"amr": "local-key"})
    yield db, user, f"Bearer {token}"
    access.reset_state()
    db.close()
    engine.dispose()


def req(method, path):
    return types.SimpleNamespace(
        state=types.SimpleNamespace(), method=method, url=types.SimpleNamespace(path=path),
        client=types.SimpleNamespace(host="127.0.0.1", port=50001),
    )


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/chat"), ("PUT", "/api/vault"), ("DELETE", "/api/documents/x"), ("PATCH", "/api/tasks/x"),
    ("POST", "/api/tasks"), ("POST", "/api/automations/x/run"),
])
def test_every_state_changing_route_waits_after_synthetic_input(owner, method, path):
    db, user, auth = owner
    assert deps.get_current_user(req(method, path), db, auth).id == user.id
    access.note_synthetic_input(user.id)
    with pytest.raises(HTTPException) as err:
        deps.get_current_user(req(method, path), db, auth)
    assert err.value.status_code == 409


def test_reads_and_stopping_are_never_held_back(owner):
    db, user, auth = owner
    access.note_synthetic_input(user.id)
    for method, path in (("GET", "/api/tasks"), ("HEAD", "/api/tasks"), ("POST", "/api/chat/stop"),
                         ("POST", "/api/device/background/abc/stop")):
        assert deps.get_current_user(req(method, path), db, auth).id == user.id


def test_it_ends_after_the_cool_down(owner, monkeypatch):
    db, user, auth = owner
    access.note_synthetic_input(user.id)
    now = access._now()
    monkeypatch.setattr(access, "_now", lambda: now + access.COOLDOWN_SECONDS + 1)
    assert deps.get_current_user(req("POST", "/api/tasks"), db, auth).id == user.id
