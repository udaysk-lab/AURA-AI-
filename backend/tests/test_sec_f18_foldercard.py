"""F18: a folder card offers the narrowest folder, and never the home folder or a drive root."""

from __future__ import annotations

import os

import pytest

from app.llm import ToolCall
from app.permissions import approvals, floors, gate
from tests.sec_world import build


def decide(w, tool, args):
    return gate.decide(w.db, w.user, tool, args, gate.build_context(w.db, w.user, "chat"))


def test_a_new_folder_in_home_is_not_offered_as_home(tmp_path, monkeypatch):
    w = build(tmp_path, monkeypatch, connect_home=False)
    for path in (os.path.join(w.home, "newdir", "deep", "a.txt"), w.home, os.path.join(w.home, "gone.txt")):
        d = decide(w, "write_file", {"path": path, "content": "x"})
        assert d.kind == "deny" and d.reason_code == "FLOOR_OUTSIDE_FOLDERS" and d.card is None, path
        assert "Settings" in d.reason


def test_an_existing_narrow_folder_is_offered_as_itself(tmp_path, monkeypatch):
    w = build(tmp_path, monkeypatch, connect_home=False)
    other = os.path.join(w.home, "Documents", "taxes")
    os.makedirs(other)
    for target in (os.path.join(other, "a.txt"), os.path.join(other, "missing", "b.txt"), other):
        d = decide(w, "write_file", {"path": target, "content": "x"})
        assert d.card == "folder_access" and d.card_payload["path"] == os.path.realpath(other), target


def test_a_drive_root_is_never_offered(tmp_path, monkeypatch):
    w = build(tmp_path, monkeypatch, connect_home=False)
    root = os.path.abspath(os.sep)
    d = decide(w, "read_file", {"path": os.path.join(root, "no-such-top-level-dir-xyz", "f")})
    assert d.card is None and d.kind == "deny"


@pytest.mark.parametrize("which", ["home", "root"])
def test_an_old_card_for_a_broad_folder_is_refused_on_approval(tmp_path, monkeypatch, which):
    w = build(tmp_path, monkeypatch, connect_home=False)
    folder = w.home if which == "home" else os.path.abspath(os.sep)
    args = {"path": os.path.join(folder, "n.txt"), "content": "x"}
    d = gate._make(kind="ask", source="always_user", reason_code="CARD", reason="Needs you.", tool="write_file",
                   args_digest=gate.args_digest(args), card="folder_access", card_payload={"path": folder},
                   options=("once", "reject"), always_user=True, user_id=w.user.id)
    a = approvals.queue_action(w.db, w.user, gate.build_context(w.db, w.user, "chat"),
                               ToolCall("", "write_file", args), d)
    out = approvals.resolve(w.db, w.user, a.id, "approve", None)
    assert out["status"] == "refused" and not os.path.exists(args["path"])


def test_the_helper():
    assert floors.too_broad_for_a_card("") and floors.too_broad_for_a_card(os.path.abspath(os.sep))
    assert floors.too_broad_for_a_card(os.path.expanduser("~"))
