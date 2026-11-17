"""An answered card approves only the question it asked: every card kind against every always-owner question."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from app.device import programs, windows
from app.llm import ToolCall
from app.models import DeviceAppGrant
from app.permissions import access, approvals, gate, reviewer, wall
from tests.perm_helpers import connect
from tests.sec_world import build

OTHER_CARDS = [
    ("folder_access", "NEEDS_FOLDER"),
    ("delete_permission", "NEEDS_DELETE_PERMISSION"),
    ("app_grant", "NEEDS_APP_GRANT"),
    ("tool", "MODE_ASK"),
    ("tool", "RULE_ASK"),
    ("tool", "REVIEWER_FALLBACK"),
]


@pytest.fixture(params=["skip", "ask", "auto"])
def w(request, tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode=request.param)
    monkeypatch.setattr(reviewer, "available", lambda: True)
    monkeypatch.setattr(reviewer, "review", mock.Mock(return_value=reviewer.ReviewerVerdict(True, "fine", "")))
    monkeypatch.setattr(programs, "current_platform", lambda: programs.LINUX)
    from app.config import settings

    monkeypatch.setattr(settings, "device_screen_control", True, raising=False)
    access.note_presence(world.user.id)
    win = windows.Frontmost("notes", "A note", 999_999, "notes")
    monkeypatch.setattr(windows, "frontmost", lambda: win)
    monkeypatch.setattr(windows, "window_at", lambda x, y: win)
    monkeypatch.setattr(windows, "pointer_position", lambda: (10, 10))
    world.db.add(DeviceAppGrant(user_id=world.user.id, app_id="notes", display_name="notes", tier="full"))
    world.db.commit()
    script = os.path.join(world.work, "run.sh")
    with open(script, "w") as fh:
        fh.write("#!/bin/sh\necho hi\n")
    os.chmod(script, 0o755)
    bin_dir = os.path.join(world.work, "bin")
    os.makedirs(bin_dir)
    tool = os.path.join(bin_dir, "mytool")
    with open(tool, "w") as fh:
        fh.write("#!/bin/sh\n")
    os.chmod(tool, 0o755)
    monkeypatch.setenv("PATH", bin_dir)
    doomed = os.path.join(world.work, "old.txt")
    open(doomed, "w").close()
    world.questions = {
        "command": ("run_command", {"command": "echo hi", "workdir": world.work}, wall.UNCHECKED_CODE),
        "file": ("open_app", {"name": script}, "UNCHECKED_OPEN"),
        "program": ("open_app", {"name": "mytool"}, "UNCHECKED_OPEN"),
        "screen": ("type_text", {"text": "hello"}, "SCREEN_INPUT"),
        "delete": ("delete_file", {"path": doomed}, "ALWAYS_USER"),
    }
    yield world
    world.db.close()
    world.engine.dispose()


def _decide_approved(w, tool, args, card, code, payload):
    c = gate.build_context(w.db, w.user, "chat")
    native = gate.decide(w.db, w.user, tool, args, c)
    assert native.kind == "ask"
    action = approvals.queue_action(w.db, w.user, c, ToolCall(id="t1", name=tool, arguments=args), native)
    action.card, action.reason_code, action.card_payload = card, code, payload
    return native, gate.decide_approved(w.db, w.user, action)


@pytest.mark.parametrize("name", ["command", "file", "program", "screen", "delete"])
def test_each_question_is_asked_with_its_own_code(w, name):
    tool, args, code = w.questions[name]
    d = gate.decide(w.db, w.user, tool, args, gate.build_context(w.db, w.user, "chat"))
    assert (d.kind, d.reason_code, d.always_user) == ("ask", code, True)


@pytest.mark.parametrize("name", ["command", "file", "program", "screen", "delete"])
def test_only_its_own_card_approves_a_question(w, name):
    tool, args, code = w.questions[name]
    c = gate.build_context(w.db, w.user, "chat")
    native = gate.decide(w.db, w.user, tool, args, c)
    action = approvals.queue_action(w.db, w.user, c, ToolCall(id="t1", name=tool, arguments=args), native)
    assert gate.decide_approved(w.db, w.user, action).kind == "allow"  # its own card
    for card, other in OTHER_CARDS:
        _, d = _decide_approved(w, tool, args, card, other, {})
        assert (d.kind, d.reason_code) == ("deny", "NEEDS_OWN_QUESTION"), (name, card, other)
    for other_name, (_, _, other_code) in w.questions.items():
        if other_code == code:
            continue
        _, d = _decide_approved(w, tool, args, "tool", other_code, {})
        assert (d.kind, d.reason_code) == ("deny", "NEEDS_OWN_QUESTION"), (name, other_name)


def test_a_card_for_another_file_or_program_is_still_refused(w):
    tool, args, _ = w.questions["file"]
    _, d = _decide_approved(w, tool, args, "tool", "UNCHECKED_OPEN",
                            {"unchecked_open": {"path": os.path.join(w.work, "other.sh")}})
    assert (d.kind, d.reason_code) == ("deny", "OPEN_CHANGED")
    tool, args, _ = w.questions["program"]
    _, d = _decide_approved(w, tool, args, "tool", "UNCHECKED_OPEN",
                            {"unchecked_open": {"path": os.path.join(w.work, "bin", "elsewhere")}})
    assert (d.kind, d.reason_code) == ("deny", "OPEN_CHANGED")


def test_a_card_for_another_application_is_still_refused(w):
    tool, args, _ = w.questions["screen"]
    _, d = _decide_approved(w, tool, args, "tool", "SCREEN_INPUT", {"screen_input": {"app_id": "mail"}})
    assert (d.kind, d.reason_code) == ("deny", "SCREEN_APP_CHANGED")


def test_when_no_owner_question_is_needed_any_answered_card_approves(w):
    path = os.path.join(w.work, "plain.txt")
    for card, code in [("folder_access", "NEEDS_FOLDER"), ("tool", "MODE_ASK")]:
        c = gate.build_context(w.db, w.user, "chat")
        args = {"path": path, "content": "x"}
        action = approvals.queue_action(
            w.db, w.user, c, ToolCall(id="t", name="write_file", arguments=args),
            gate._make(kind="ask", source="mode", reason_code=code, reason="r", tool="write_file",
                       args_digest=gate.args_digest(args), card=card, options=("once", "reject"),
                       user_id=w.user.id))
        assert gate.decide_approved(w.db, w.user, action).kind == "allow"
