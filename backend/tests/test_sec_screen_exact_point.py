"""A click or scroll acts on exactly the point the gate judged, and the window there is the one that was judged."""

from __future__ import annotations

import pytest

from app.agent.coordinator import execute_tool
from app.device import screen, windows
from app.permissions import gate
from tests.perm_helpers import ctx
from tests.test_perm_screen import Front, env, grant, here, open_window  # noqa: F401


class Gui:
    def __init__(self):
        self.calls = []

    def click(self, **kw):
        self.calls.append(("click", kw))

    def scroll(self, amount, **kw):
        self.calls.append(("scroll", amount, kw))


@pytest.fixture
def layout(env, monkeypatch):
    db, user = env
    here(user)
    grant(db, user, "notes", "full")
    open_window(db, user, "notes")  # the owner allowed 10 minutes in Notes only
    notes = Front("notes", "A note", 999_999, "notes")
    bank = Front("my-bank-app", "Transfer", 999_997, "My Bank")
    pointer = {"at": (10, 10)}
    monkeypatch.setattr(windows, "frontmost", lambda: notes)
    monkeypatch.setattr(windows, "window_at", lambda x, y: bank if x >= 400 else notes)
    monkeypatch.setattr(windows, "pointer_position", lambda: pointer["at"])
    gui = Gui()
    monkeypatch.setattr(screen, "_gui", lambda: gui)
    return db, user, gui, pointer


@pytest.mark.parametrize("tool,args", [
    ("mouse_click", {"x": "500", "y": "300"}),
    ("mouse_click", {"x": 500.0, "y": "300"}),
    ("mouse_click", {"x": 500.0, "y": 300.0}),
    ("mouse_click", {"x": True, "y": True}),
    ("mouse_click", {"x": 500}),
    ("mouse_click", {"y": 300}),
    ("mouse_click", {"x": None, "y": 300}),
    ("mouse_click", {"x": 5, "y": 5, "clicks": 4}),
    ("mouse_click", {"x": 5, "y": 5, "button": "back"}),
    ("scroll_screen", {"amount": -5, "x": "500", "y": "300"}),
    ("scroll_screen", {"amount": 500}),
    ("scroll_screen", {"amount": -101}),
    ("scroll_screen", {"amount": 2.0}),
    ("scroll_screen", {"amount": 3, "x": 4}),
])
def test_a_badly_formed_point_is_refused_before_anything_is_judged(layout, tool, args):
    db, user, gui, _ = layout
    d = gate.decide(db, user, tool, args, ctx(db, user))
    assert (d.kind, d.reason_code) == ("deny", "BAD_ARGS")
    assert gui.calls == []


def test_numbers_are_judged_where_they_land(layout):
    db, user, gui, _ = layout
    assert gate.decide(db, user, "mouse_click", {"x": 500, "y": 300}, ctx(db, user)).reason_code == "FLOOR_BLOCKED_APP"
    d = gate.decide(db, user, "mouse_click", {"x": 50, "y": 300}, ctx(db, user))
    assert (d.kind, d.reason_code) == ("allow", "SCREEN_WINDOW")
    args = {"x": 50, "y": 300, "clicks": 2}
    d = gate.decide(db, user, "mouse_click", args, ctx(db, user))
    out = execute_tool(db, user, "mouse_click", args, decision=d)
    assert "error" not in out, out
    assert gui.calls[-1] == ("click", {"x": 50, "y": 300, "button": "left", "clicks": 2})


def test_scroll_acts_on_the_judged_numbers(layout):
    db, user, gui, _ = layout
    args = {"amount": -5, "x": 60, "y": 70}
    d = gate.decide(db, user, "scroll_screen", args, ctx(db, user))
    execute_tool(db, user, "scroll_screen", args, decision=d)
    assert gui.calls[-1] == ("scroll", -5, {"x": 60, "y": 70})


def test_without_a_point_the_action_is_at_the_pointer_that_was_checked(layout):
    db, user, gui, pointer = layout
    d = gate.decide(db, user, "mouse_click", {}, ctx(db, user))
    assert d.kind == "allow"
    out = execute_tool(db, user, "mouse_click", {}, decision=d)
    assert "error" not in out, out
    assert gui.calls[-1][1]["x"] == 10 and gui.calls[-1][1]["y"] == 10


def test_a_pointer_that_moved_onto_another_window_stops_the_call(layout):
    db, user, gui, pointer = layout
    d = gate.decide(db, user, "mouse_click", {}, ctx(db, user))
    pointer["at"] = (500, 300)  # now over the blocked application
    out = execute_tool(db, user, "mouse_click", {}, decision=d)
    assert "error" in out and gui.calls == []


def test_the_screen_layer_refuses_what_is_not_a_whole_number(layout):
    from app.device.runtime import DeviceUnavailable

    for call in (lambda: screen.click("500", "300"), lambda: screen.click(True, True), lambda: screen.click(1, 1, "left", 9),
                 lambda: screen.scroll(5, "1", 2), lambda: screen.scroll(500, 1, 2)):
        with pytest.raises(DeviceUnavailable):
            call()
