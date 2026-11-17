"""A move into an existing folder creates <folder>/<name of the source>. The gate and the file layer judge exactly
that path, so a move cannot complete a git folder or land on a protected path that a direct write may not touch."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from app.agent.coordinator import execute_tool
from app.device import files
from app.models import PendingAction
from app.permissions import gate, match, reviewer
from tests.perm_helpers import ctx
from tests.sec_world import build, run


@pytest.fixture(params=["skip", "auto"])
def w(request, tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode=request.param)
    monkeypatch.setattr(reviewer, "available", lambda: True)
    monkeypatch.setattr(reviewer, "review", mock.Mock(return_value=reviewer.ReviewerVerdict(True, "fine", "")))
    yield world
    world.db.close()
    world.engine.dispose()


def _allow(w, tool, args):
    d, out = run(w, tool, args)
    assert d.kind == "allow", (tool, d.reason_code, d.reason)
    assert not (isinstance(out, dict) and out.get("error")), (tool, out)
    return out


def test_the_landing_path_is_inside_an_existing_folder_and_the_destination_itself_otherwise(tmp_path):
    folder = tmp_path / "into"
    folder.mkdir()
    assert match.landing(str(tmp_path / "a" / "refs"), str(folder)) == str(folder / "refs")
    assert match.landing(str(tmp_path / "a" / "refs"), str(tmp_path / "new-name")) == str(tmp_path / "new-name")


def test_moving_the_last_marker_into_a_folder_cannot_complete_a_git_folder(w):
    src = os.path.join(w.work, "src")
    os.makedirs(src)
    _allow(w, "write_file", {"path": os.path.join(src, "HEAD"), "content": "ref: refs/heads/main\n"})
    _allow(w, "make_directory", {"path": os.path.join(src, "objects")})
    _allow(w, "write_file", {"path": os.path.join(src, "config"), "content": "[core]\n"})
    stage = os.path.join(w.work, "stage")
    _allow(w, "make_directory", {"path": os.path.join(stage, "refs")})
    d, out = run(w, "move_file", {"source": os.path.join(stage, "refs"), "destination": src})
    assert (d.kind, d.reason_code) == ("deny", "FLOOR_PROTECTED_PATH"), (d.kind, d.reason_code)
    assert not os.path.exists(os.path.join(src, "refs"))
    assert not files.git_internal(os.path.join(src, "config"))
    assert w.db.query(PendingAction).count() == 0


def test_moving_a_head_file_into_a_folder_with_objects_and_refs_is_refused(w):
    src = os.path.join(w.work, "repoish")
    os.makedirs(os.path.join(src, "objects"))
    os.makedirs(os.path.join(src, "refs"))
    stage = os.path.join(w.work, "stage2")
    os.makedirs(stage)
    _allow(w, "write_file", {"path": os.path.join(stage, "HEAD"), "content": "ref: refs/heads/main\n"})
    d, _ = run(w, "move_file", {"source": os.path.join(stage, "HEAD"), "destination": src})
    assert (d.kind, d.reason_code) == ("deny", "FLOOR_PROTECTED_PATH")
    assert not os.path.exists(os.path.join(src, "HEAD"))


def test_moving_a_plainly_named_file_into_the_home_folder_cannot_land_on_a_protected_path(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode="skip", connect_home=True)
    try:
        bashrc = os.path.join(world.home, ".bashrc")
        stage = os.path.join(world.home, "work", "stage3")
        os.makedirs(stage)
        with open(os.path.join(stage, ".bashrc"), "w") as fh:
            fh.write("echo planted\n")
        d, _ = run(world, "move_file", {"source": os.path.join(stage, ".bashrc"), "destination": world.home})
        assert (d.kind, d.reason_code) == ("deny", "FLOOR_PROTECTED_PATH")
        assert not os.path.exists(bashrc)
        assert world.db.query(PendingAction).count() == 0
    finally:
        world.db.close()
        world.engine.dispose()


def test_an_ordinary_move_into_a_folder_still_works_and_never_replaces_what_is_there(w):
    into = os.path.join(w.work, "docs")
    os.makedirs(into)
    _allow(w, "write_file", {"path": os.path.join(w.work, "notes.txt"), "content": "one\n"})
    out = _allow(w, "move_file", {"source": os.path.join(w.work, "notes.txt"), "destination": into})
    assert os.path.exists(os.path.join(into, "notes.txt")) and out["to"].endswith("notes.txt")
    _allow(w, "write_file", {"path": os.path.join(w.work, "notes.txt"), "content": "two\n"})
    d, out = run(w, "move_file", {"source": os.path.join(w.work, "notes.txt"), "destination": into})
    assert d.kind == "allow" and "already exists" in out.get("error", ""), out
    with open(os.path.join(into, "notes.txt")) as fh:
        assert fh.read() == "one\n"


def test_a_destination_that_became_a_folder_after_the_decision_is_refused(w):
    """The gate judged a rename to `target`; by the time the move runs `target` is a folder holding HEAD and
    objects, so the move would land at target/refs, a path nobody judged."""
    _allow(w, "make_directory", {"path": os.path.join(w.work, "stage", "refs")})
    target = os.path.join(w.work, "target")
    args = {"source": os.path.join(w.work, "stage", "refs"), "destination": target}
    d = gate.decide(w.db, w.user, "move_file", args, ctx(w.db, w.user))
    assert d.kind == "allow"
    os.makedirs(os.path.join(target, "objects"))
    with open(os.path.join(target, "HEAD"), "w") as fh:
        fh.write("ref: refs/heads/main\n")
    out = execute_tool(w.db, w.user, "move_file", args, decision=d)
    assert "changed since this was checked" in out.get("error", ""), out
    assert not os.path.exists(os.path.join(target, "refs"))


def test_the_file_layer_checks_the_landing_path_on_its_own(w, monkeypatch):
    """Without the gate (a direct call, as the file layer's own guard), a move into a folder still checks the path
    it creates."""
    src = os.path.join(w.work, "repo2")
    os.makedirs(os.path.join(src, "objects"))
    os.makedirs(os.path.join(src, "refs"))
    stage = os.path.join(w.work, "stage4")
    os.makedirs(stage)
    with open(os.path.join(stage, "HEAD"), "w") as fh:
        fh.write("ref: refs/heads/main\n")
    from app.permissions import access

    monkeypatch.setattr(access, "roots", lambda: [w.home])
    with pytest.raises(files.PathNotAllowed, match="git"):
        files.move_path(os.path.join(stage, "HEAD"), src)
    assert not os.path.exists(os.path.join(src, "HEAD"))
