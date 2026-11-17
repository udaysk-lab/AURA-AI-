"""Typing and key presses act on exactly what the gate judged: a string, in the window that was judged."""

from __future__ import annotations

import pytest

from app.agent import device_tools
from app.agent.coordinator import execute_tool
from app.device import screen, windows
from app.permissions import access, gate
from tests.perm_helpers import ctx
from tests.test_perm_screen import Front, env, grant, here, open_window  # noqa: F401


class Gui:
    def __init__(self, on_piece=None):
        self.typed = []
        self.keys = []
        self.on_piece = on_piece

    def typewrite(self, message, interval=0.0):
        self.typed.append(message)
        if self.on_piece:
            self.on_piece(len(self.typed))

    def press(self, key):
        self.keys.append((key,))

    def hotkey(self, *keys):
        self.keys.append(tuple(keys))


@pytest.fixture
def notes(env, monkeypatch):
    db, user = env
    here(user)
    grant(db, user, "notes", "full")
    open_window(db, user, "notes")
    front = Front("notes", "A note", 999_999, "notes")
    monkeypatch.setattr(windows, "frontmost", lambda: front)
    gui = Gui()
    monkeypatch.setattr(screen, "_gui", lambda: gui)
    return db, user, gui, front


def _run(db, user, tool, args):
    d = gate.decide(db, user, tool, args, ctx(db, user))
    return d, (execute_tool(db, user, tool, args, decision=d) if d.kind == "allow" else None)


LIST_ATTACK = ["win", "c", "m", "d", "enter"] + ["shift"] * 300 + list("calc") + ["enter"]


@pytest.mark.parametrize("tool,args", [
    ("type_text", {"text": LIST_ATTACK}),
    ("type_text", {"text": "a\x1bb"}),
    ("type_text", {"text": "a\rb"}),
    ("type_text", {"text": "a\x7fb"}),
    ("type_text", {"text": "x" * 2001}),
    ("press_keys", {"keys": LIST_ATTACK}),
    ("press_keys", {"keys": "ctrl+shift+alt+a+b"}),
    ("press_keys", {"keys": "ctrl+nosuchkey"}),
    ("press_keys", {"keys": "launchmail"}),
    ("press_keys", {"keys": "  "}),
])
def test_the_gate_refuses_what_the_handler_would_misread(notes, tool, args):
    db, user, gui, _ = notes
    d, out = _run(db, user, tool, args)
    assert (d.kind, d.reason_code) == ("deny", "BAD_ARGS")
    assert gui.typed == [] and gui.keys == []


def test_a_list_given_straight_to_the_handler_presses_nothing(notes):
    db, user, gui, _ = notes
    from app.agent.tools import ToolContext

    c = ToolContext(db=db, user=user, decision=gate.decide(db, user, "type_text", {"text": "x"}, ctx(db, user)))
    for fn, args in ((device_tools._type_text, {"text": LIST_ATTACK}), (device_tools._press_keys, {"keys": LIST_ATTACK})):
        out = fn(c, args)
        assert "error" in out
    assert gui.typed == [] and gui.keys == []


def test_typing_stops_after_the_first_enter_and_says_so(notes):
    db, user, gui, _ = notes
    d, out = _run(db, user, "type_text", {"text": "user\tname\npassword\n"})
    assert d.kind == "allow" and "error" not in out, out
    assert gui.typed == ["user\t", "name\n"]
    assert out["typed"] == 10 and out["length"] == 19
    assert "Enter ends the call" in out["message"] and "not typed" in out["message"]


def test_text_without_a_new_line_is_typed_whole_in_pieces_after_tabs(notes):
    db, user, gui, _ = notes
    d, out = _run(db, user, "type_text", {"text": "a\tb\tc"})
    assert gui.typed == ["a\t", "b\t", "c"]
    assert out["typed"] == 5 and "Enter ends" not in out["message"]


@pytest.mark.parametrize("later", [
    Front("mail", "Inbox", 999_998, "mail"),                 # another application
    Front("notes", "Inbox · AURA", 999_999, "notes"),        # AURA's page in the same application
    Front("my-bank-app", "Transfer", 999_997, "My Bank"),    # a blocked application
    Front("aura", "AURA", 999_996, "aura"),                  # AURA's own application
    None,                                                    # nothing can be told
])
def test_the_window_is_checked_again_before_each_piece(notes, monkeypatch, later):
    db, user, gui, front = notes
    state = {"win": front}
    monkeypatch.setattr(windows, "frontmost", lambda: state["win"])
    gui.on_piece = lambda n: state.update(win=later)
    d = gate.decide(db, user, "type_text", {"text": "one\ttwo\tthree"}, ctx(db, user))
    assert d.kind == "allow"
    out = execute_tool(db, user, "type_text", {"text": "one\ttwo\tthree"}, decision=d)
    assert gui.typed == ["one\t"]
    assert "error" in out and "Stopped after typing 4" in out["error"]


def test_aura_counts_as_typing_for_the_whole_call_and_the_cooldown_after(notes, monkeypatch):
    db, user, gui, _ = notes
    clock = {"t": access._now()}  # the real monotonic time, so the owner noted by the fixture is still here
    monkeypatch.setattr(access, "_now", lambda: clock["t"])
    seen = []

    def slow(n):
        clock["t"] += 60.0  # a very slow piece
        seen.append(access.recent_synthetic_input(user.id))

    gui.on_piece = slow
    d, out = _run(db, user, "type_text", {"text": "a\tb\tc\td"})
    assert seen == [True, True, True, True], (d.kind, d.reason_code, d.reason, out, gui.typed)
    assert access.recent_synthetic_input(user.id)  # the cooldown after the end
    clock["t"] += access.COOLDOWN_SECONDS + 1
    assert not access.recent_synthetic_input(user.id)


def test_the_busy_marker_is_released_when_a_call_fails(notes, monkeypatch):
    db, user, gui, _ = notes
    clock = {"t": access._now()}  # the real monotonic time, so the owner noted by the fixture is still here
    monkeypatch.setattr(access, "_now", lambda: clock["t"])

    def boom(n):
        raise RuntimeError("input failed")

    gui.on_piece = boom
    _run(db, user, "type_text", {"text": "x"})
    clock["t"] += access.COOLDOWN_SECONDS + 1
    assert not access.recent_synthetic_input(user.id)


def test_a_chord_of_known_keys_is_pressed(notes):
    db, user, gui, _ = notes
    for keys, want in (("ctrl+s", ("ctrl", "s")), ("Enter", ("enter",)), ("cmd+space", ("command", "space")),
                       ("alt tab", ("alt", "tab"))):
        d, out = _run(db, user, "press_keys", {"keys": keys})
        assert d.kind == "allow" and "error" not in out, (keys, out)
        assert gui.keys[-1] == want


def test_the_tool_descriptions_tell_the_model(notes):
    from app.agent.tools import REGISTRY

    assert "first Enter" in REGISTRY["type_text"].spec.description
    assert "whole number" in REGISTRY["mouse_click"].spec.description
    assert "at most 4 keys" in REGISTRY["press_keys"].spec.description
    assert "whole number" in REGISTRY["scroll_screen"].spec.description
