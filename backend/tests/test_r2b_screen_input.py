"""R2b B: screen input (pointer, clicks, scrolling, typing, keys) always needs the owner, in every mode. The only
allowance is "for 10 minutes in this application", bound to the application the owner was asked about."""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from sqlalchemy import select

from app.config import settings
from app.device import screen, windows
from app.llm import ToolCall
from app.models import PermissionRule, utcnow
from app.permissions import approvals, asks, catalogue, gate, reviewer, rules
from tests.perm_helpers import ctx, make_session, make_user
from tests.test_perm_screen import Front, code, env, front, go, grant, here, open_window  # noqa: F401

INPUT = {
    "mouse_click": {"x": 5, "y": 5},
    "scroll_screen": {"amount": -3, "x": 5, "y": 5},
    "type_text": {"text": "hello"},
    "press_keys": {"keys": "enter"},
}
MODES = ("skip", "auto", "ask")


def set_mode(db, user, mode):
    user.settings.permission_mode = mode
    db.commit()


@pytest.fixture
def reviewing(monkeypatch):
    mocked = mock.Mock(return_value=reviewer.ReviewerVerdict(True, "fine", ""))
    monkeypatch.setattr(reviewer, "available", lambda: True)
    monkeypatch.setattr(reviewer, "review", mocked)
    return mocked


def ready(db, user, monkeypatch, app="notes", tier="full", name=None):
    front(monkeypatch, app, name=name)
    here(user)
    grant(db, user, app, tier)


def park(db, user, tool, args):
    c = ctx(db, user, "chat")
    d = gate.decide(db, user, tool, args, c)
    assert d.kind == "ask" and d.reason_code == "SCREEN_INPUT", (d.kind, d.reason_code)
    return approvals.queue_action(db, user, c, ToolCall(id="c1", name=tool, arguments=args), d), d


def test_the_catalogue_lists_exactly_the_screen_input_tools():
    found = sorted(p.name for p in catalogue.CATALOGUE.values() if p.kind == "screen_input")
    assert found == sorted(asks.SCREEN_TOOLS) == sorted(INPUT)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", sorted(INPUT))
def test_screen_input_asks_in_every_mode(env, monkeypatch, reviewing, mode, tool):
    db, user = env
    ready(db, user, monkeypatch, name="Notes")
    set_mode(db, user, mode)
    d = go(db, user, tool, INPUT[tool])
    assert d.kind == "ask" and d.reason_code == "SCREEN_INPUT" and d.source == "always_user" and d.always_user
    assert tuple(d.options) == ("once", "window", "reject") and "always" not in d.options
    assert d.proposed_rule is None
    assert "in Notes" in d.reason and "starting programs" in d.reason and "Watch" in d.reason
    assert d.card_payload["screen_input"]["app_id"] == "notes" and d.card_payload["screen_input"]["window_minutes"] == 10
    reviewing.assert_not_called()


@pytest.mark.parametrize("mode", MODES)
def test_a_screenshot_still_follows_mode_and_rules(env, monkeypatch, reviewing, mode):
    db, user = env
    ready(db, user, monkeypatch)
    set_mode(db, user, mode)
    assert code(go(db, user, "take_screenshot")) == ("allow", "DEFAULT_READ")
    rules.add(db, user, effect="block", system="device-control", tool="take_screenshot", target_kind="app",
              pattern="notes", actor="user")
    db.commit()
    assert code(go(db, user, "take_screenshot")) == ("deny", "RULE_BLOCK")


@pytest.mark.parametrize("tool", sorted(INPUT))
def test_no_stored_rule_or_window_of_another_kind_covers_screen_input(env, monkeypatch, reviewing, tool):
    db, user = env
    ready(db, user, monkeypatch)
    set_mode(db, user, "skip")
    soon = utcnow() + timedelta(minutes=10)
    for kw in (
        dict(tool=tool, target_kind="", pattern=""), dict(tool=None, target_kind="", pattern=""),
        dict(tool=tool, target_kind="app", pattern="notes"), dict(tool=None, target_kind="app", pattern="notes"),
        dict(tool=tool, target_kind="app", pattern="notes", expires_at=soon, source="card"),
        dict(tool=None, target_kind="", pattern="", expires_at=soon, source="card"),
        dict(tool=None, target_kind="app", pattern="notes", expires_at=soon, source="legacy"),
    ):
        rules.add(db, user, effect="allow", system="device-control", actor="user", **kw)
        db.commit()
        assert code(go(db, user, tool, INPUT[tool])) == ("ask", "SCREEN_INPUT"), kw
    reviewing.assert_not_called()


def test_a_block_rule_and_the_floors_stay_in_front(env, monkeypatch):
    db, user = env
    ready(db, user, monkeypatch)
    open_window(db, user, "notes")
    assert go(db, user, "type_text", INPUT["type_text"]).kind == "allow"
    rules.add(db, user, effect="block", system="device-control", tool="type_text", actor="user")
    db.commit()
    assert code(go(db, user, "type_text", INPUT["type_text"])) == ("deny", "RULE_BLOCK")


@pytest.mark.parametrize("tool", sorted(INPUT))
def test_the_existing_floors_stay_in_front_of_a_window(env, monkeypatch, tool):
    db, user = env
    here(user)
    # AURA's own window
    front(monkeypatch, "notes", "Inbox · AURA", 4242)
    grant(db, user, "notes", "full")
    open_window(db, user, "notes")
    assert code(go(db, user, tool, INPUT[tool])) == ("deny", "FLOOR_AURA_WINDOW")
    # a blocked application
    front(monkeypatch, "my-bank-app")
    grant(db, user, "my-bank-app", "full")
    open_window(db, user, "my-bank-app")
    assert code(go(db, user, tool, INPUT[tool])) == ("deny", "FLOOR_BLOCKED_APP")
    # an application nobody granted
    front(monkeypatch, "figma")
    open_window(db, user, "figma")
    assert go(db, user, tool, INPUT[tool]).card == "app_grant"


@pytest.mark.parametrize("app", ["terminal", "vscode"])
def test_terminals_and_editors_stay_click_and_scroll_only(env, monkeypatch, app):
    db, user = env
    ready(db, user, monkeypatch, app=app, tier="full")
    open_window(db, user, app)
    assert code(go(db, user, "type_text", INPUT["type_text"])) == ("deny", "FLOOR_APP_TIER")
    assert code(go(db, user, "press_keys", INPUT["press_keys"])) == ("deny", "FLOOR_APP_TIER")
    assert go(db, user, "mouse_click", INPUT["mouse_click"]).kind == "allow"  # still behind the window
    assert go(db, user, "scroll_screen", INPUT["scroll_screen"]).kind == "allow"


def test_not_present_and_unknown_front_window_still_deny(env, monkeypatch):
    db, user = env
    front(monkeypatch, "notes")
    grant(db, user, "notes", "full")
    open_window(db, user, "notes")
    assert code(go(db, user, "type_text", INPUT["type_text"])) == ("deny", "NOT_PRESENT")


@pytest.mark.parametrize("trigger", ["schedule", "automation", "inbound", "heartbeat"])
@pytest.mark.parametrize("tool", sorted(INPUT))
def test_unattended_triggers_never_get_screen_input(env, monkeypatch, trigger, tool):
    db, user = env
    ready(db, user, monkeypatch)
    open_window(db, user, "notes")
    d = go(db, user, tool, INPUT[tool], trigger=trigger, origin_id="x", approval_mode="auto")
    assert d.kind == "deny", (trigger, d.reason_code)


# -- the answers --------------------------------------------------------------------------------------------------


def test_always_cannot_be_given_and_no_rule_is_saved(env, monkeypatch):
    db, user = env
    ready(db, user, monkeypatch)
    action, _ = park(db, user, "type_text", INPUT["type_text"])
    assert list(action.options) == ["once", "window", "reject"]
    with pytest.raises(approvals.ApprovalError):
        approvals.resolve(db, user, action.id, "always")
    assert db.scalars(select(PermissionRule)).all() == []


def test_once_runs_this_call_and_asks_again_next_time(env, monkeypatch):
    db, user = env
    ready(db, user, monkeypatch)
    typed = []
    monkeypatch.setattr(screen, "type_text", lambda text, *a, **k: typed.append(text) or {"message": "typed"})
    action, _ = park(db, user, "type_text", INPUT["type_text"])
    out = approvals.resolve(db, user, action.id, "once")
    assert out["status"] == "approved" and typed == ["hello"]
    assert db.scalars(select(PermissionRule)).all() == []
    assert code(go(db, user, "type_text", INPUT["type_text"])) == ("ask", "SCREEN_INPUT")


def test_the_ten_minute_answer_covers_the_same_application_and_not_another(env, monkeypatch):
    db, user = env
    ready(db, user, monkeypatch, name="Notes")
    grant(db, user, "figma", "full")
    clicked = []
    monkeypatch.setattr(screen, "click", lambda *a, **k: clicked.append(a) or {"message": "clicked"})
    action, _ = park(db, user, "mouse_click", INPUT["mouse_click"])
    out = approvals.resolve(db, user, action.id, "window")
    assert out["status"] == "approved" and len(clicked) == 1
    row = db.scalars(select(PermissionRule)).one()
    assert row.source == "screen" and row.target_kind == "app" and row.pattern == "notes" and row.tool is None
    assert timedelta(minutes=9) < row.expires_at.replace(tzinfo=None) - utcnow().replace(tzinfo=None) <= timedelta(minutes=10)

    # every screen input tool in the same application is covered ...
    for tool, args in INPUT.items():
        d = go(db, user, tool, args)
        assert (d.kind, d.reason_code) == ("allow", "SCREEN_WINDOW"), tool
    # ... and a screenshot is unaffected
    assert go(db, user, "take_screenshot").reason_code == "DEFAULT_READ"

    # input aimed at another application asks again, however it gets there
    front(monkeypatch, "figma")
    for tool, args in INPUT.items():
        d = go(db, user, tool, args)
        assert (d.kind, d.reason_code) == ("ask", "SCREEN_INPUT"), tool
        assert d.card_payload["screen_input"]["app_id"] == "figma"

    # a click that lands on another application while the front one is covered
    front(monkeypatch, "notes")
    other = Front("figma", "A page", 999_998, "figma")
    monkeypatch.setattr(windows, "window_at", lambda x, y: other)
    assert code(go(db, user, "mouse_click", INPUT["mouse_click"])) == ("ask", "SCREEN_INPUT")
    assert go(db, user, "type_text", INPUT["type_text"]).kind == "allow"


def test_the_window_expires_and_can_be_revoked(env, monkeypatch):
    db, user = env
    ready(db, user, monkeypatch)
    row = rules.add_screen_window(db, user, "notes", "Notes", 10)
    db.commit()
    assert go(db, user, "press_keys", INPUT["press_keys"]).kind == "allow"
    row.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert code(go(db, user, "press_keys", INPUT["press_keys"])) == ("ask", "SCREEN_INPUT")
    row2 = rules.add_screen_window(db, user, "notes", "Notes", 10)
    db.commit()
    assert go(db, user, "press_keys", INPUT["press_keys"]).kind == "allow"
    assert rules.revoke(db, user, row2.id)
    db.commit()
    assert code(go(db, user, "press_keys", INPUT["press_keys"])) == ("ask", "SCREEN_INPUT")


def test_a_window_does_not_cover_other_tools_of_that_application(env, monkeypatch, reviewing):
    db, user = env
    ready(db, user, monkeypatch)
    set_mode(db, user, "ask")
    rules.add_screen_window(db, user, "notes", "Notes", 10)
    db.commit()
    # the window is not a rule that open_app, close_app or the others can see
    for tool, args in (("open_app", {"name": "Notes"}), ("close_app", {"name": "Notes"})):
        d = go(db, user, tool, args)
        assert d.reason_code != "RULE_ALLOW", tool


def test_the_pointer_moved_to_another_application_after_the_card_refuses_the_answer(env, monkeypatch):
    db, user = env
    ready(db, user, monkeypatch)
    grant(db, user, "figma", "full")
    clicked = []
    monkeypatch.setattr(screen, "click", lambda *a, **k: clicked.append(a) or {})
    action, _ = park(db, user, "mouse_click", INPUT["mouse_click"])
    other = Front("figma", "A page", 999_998, "figma")
    monkeypatch.setattr(windows, "window_at", lambda x, y: other)
    out = approvals.resolve(db, user, action.id, "window")
    assert out["status"] == "refused" and not clicked
    assert db.scalars(select(PermissionRule)).all() == []


def test_a_phone_cannot_approve_screen_input(env, monkeypatch):
    db, user = env
    ready(db, user, monkeypatch)
    action, _ = park(db, user, "press_keys", INPUT["press_keys"])
    with pytest.raises(approvals.ApprovalError) as err:
        approvals.resolve(db, user, action.id, "once", approvals.Caller(kind="remote", device_id="d1"))
    assert err.value.status_code == 403


def test_the_window_rule_is_not_offered_to_the_settings_rules_for_other_tools(env):
    db, user = env
    rules.add_screen_window(db, user, "notes", "Notes", 10)
    db.commit()
    for name in ("open_app", "run_command", "write_file"):
        assert rules.stored(db, user.id, catalogue.CATALOGUE[name], ctx(db, user)) == []
    assert rules.stored(db, user.id, catalogue.CATALOGUE["type_text"], ctx(db, user)) == []


def test_the_catalogue_says_what_these_tools_do_by_default():
    rows = {r["tool"]: r for r in catalogue.describe()}
    for tool in INPUT:
        assert "every time" in rows[tool]["note"] and "10 minutes" in rows[tool]["note"]
    assert "every time" in rows["open_app"]["note"]
    assert rows["take_screenshot"]["note"] == "Follows your mode and rules."
    assert rows["read_file"]["note"] == ""
