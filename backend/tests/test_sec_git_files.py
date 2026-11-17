"""File tools never change git's own files; reading them, and moving or deleting a whole project, stay allowed."""

from __future__ import annotations

import os

import pytest

from app.device import files
from tests.sec_world import build, run


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode="skip")
    world.repo = os.path.join(world.work, "repo")
    for sub in ("hooks", "objects", "refs"):
        os.makedirs(os.path.join(world.repo, ".git", sub))
    with open(os.path.join(world.repo, ".git", "HEAD"), "w") as fh:
        fh.write("ref: refs/heads/main\n")
    with open(os.path.join(world.repo, ".git", "config"), "w") as fh:
        fh.write("[core]\n")
    with open(os.path.join(world.repo, "README.md"), "w") as fh:
        fh.write("hello\n")
    yield world
    world.db.close()
    world.engine.dispose()


def _denied(w, tool, args):
    d, out = run(w, tool, args)
    return (d.kind, d.reason_code) == ("deny", "FLOOR_PROTECTED_PATH") and "git's own files" in d.reason


@pytest.mark.parametrize("rel", [".git/commondir", ".git/config", ".git/config.worktree", ".git/hooks/pre-commit",
                                 ".git/info/exclude", ".git/objects/pack/x", ".git/HEAD", ".git/new/deep/file"])
def test_nothing_is_written_inside_a_git_folder(w, rel):
    path = os.path.join(w.repo, *rel.split("/"))
    assert _denied(w, "write_file", {"path": path, "content": "x"})
    assert _denied(w, "edit_file", {"path": path, "find": "a", "replace": "b"})
    assert _denied(w, "make_directory", {"path": path})
    assert not os.path.exists(path) or rel in (".git/config", ".git/HEAD")


def test_moves_and_deletes_in_or_into_a_git_folder_are_refused(w):
    git = os.path.join(w.repo, ".git")
    assert _denied(w, "delete_file", {"path": os.path.join(git, "config")})
    assert _denied(w, "delete_file", {"path": git})
    assert _denied(w, "move_file", {"source": git, "destination": os.path.join(w.work, "elsewhere")})
    assert _denied(w, "move_file", {"source": os.path.join(w.repo, "README.md"),
                                    "destination": os.path.join(git, "README.md")})
    assert _denied(w, "move_file", {"source": os.path.join(git, "config"), "destination": os.path.join(w.work, "c")})
    assert os.path.exists(os.path.join(git, "config"))


def test_a_file_named_git_is_not_written_either(w):
    other = os.path.join(w.work, "sub")
    os.makedirs(other)
    assert _denied(w, "write_file", {"path": os.path.join(other, ".git"), "content": "gitdir: ../repo/.git\n"})
    assert not os.path.exists(os.path.join(other, ".git"))


def test_another_folder_that_looks_like_a_git_folder_is_covered(w):
    """The sequence that made a second git folder out of ordinary files: no order of calls completes one."""
    bare = os.path.join(w.work, "notes")
    steps = [
        ("make_directory", {"path": os.path.join(bare, "objects")}),
        ("make_directory", {"path": os.path.join(bare, "refs")}),
    ]
    for tool, args in steps:
        d, _ = run(w, tool, args)
        assert d.kind == "allow", (tool, d.reason_code)
    assert _denied(w, "write_file", {"path": os.path.join(bare, "HEAD"), "content": "ref: refs/heads/main\n"})
    assert not os.path.exists(os.path.join(bare, "HEAD"))
    # the other way round: the last piece to arrive is the objects folder
    bare2 = os.path.join(w.work, "notes2")
    os.makedirs(os.path.join(bare2, "refs"))
    with open(os.path.join(bare2, "HEAD"), "w") as fh:
        fh.write("x")
    assert _denied(w, "make_directory", {"path": os.path.join(bare2, "objects")})
    assert _denied(w, "write_file", {"path": os.path.join(bare2, "objects", "x"), "content": "x"})


def test_an_existing_git_like_folder_cannot_be_changed(w):
    bare = os.path.join(w.work, "bare.git")
    for sub in ("objects", "refs"):
        os.makedirs(os.path.join(bare, sub))
    with open(os.path.join(bare, "HEAD"), "w") as fh:
        fh.write("x")
    assert _denied(w, "write_file", {"path": os.path.join(bare, "config"), "content": "[core]\n"})
    assert _denied(w, "delete_file", {"path": bare})


def test_other_spellings_of_the_name(w):
    for name in (".GIT", ".git.", ".git ", "GIT~1"):
        assert files.git_internal(os.path.join(w.work, "p", name, "config")), name
    assert not files.git_internal(os.path.join(w.work, "p", ".gitignore"))
    assert not files.git_internal(os.path.join(w.work, "p", ".github", "workflows", "ci.yml"))
    assert not files.git_internal(os.path.join(w.work, "p", "legit.git"))


def test_reading_and_the_rest_of_the_project_stay_allowed(w):
    d, out = run(w, "read_file", {"path": os.path.join(w.repo, ".git", "config")})
    assert d.kind == "allow" and "error" not in out
    d, out = run(w, "list_directory", {"path": os.path.join(w.repo, ".git"), "show_hidden": True})
    assert d.kind == "allow"
    d, out = run(w, "write_file", {"path": os.path.join(w.repo, "src", "main.py"), "content": "print(1)\n"})
    assert d.kind == "allow" and "error" not in out
    d, out = run(w, "write_file", {"path": os.path.join(w.repo, ".gitignore"), "content": "*.pyc\n"})
    assert d.kind == "allow" and "error" not in out


def test_the_whole_project_can_still_be_moved(w):
    d, out = run(w, "move_file", {"source": w.repo, "destination": os.path.join(w.work, "renamed")})
    assert d.kind == "allow" and "error" not in out, (d.reason, out)
    assert os.path.isdir(os.path.join(w.work, "renamed", ".git", "objects"))


def test_the_handlers_refuse_too_without_the_gate(w):
    from app.permissions import access

    with access.use(access.Access(folders=(access.FolderInfo("1", w.work, "k", True, "user"),))):
        for call in (lambda: files.write_text(os.path.join(w.repo, ".git", "commondir"), "x"),
                     lambda: files.delete_path(os.path.join(w.repo, ".git", "config")),
                     lambda: files.make_directory(os.path.join(w.repo, ".git", "x")),
                     lambda: files.move_path(os.path.join(w.repo, ".git"), os.path.join(w.work, "g"))):
            with pytest.raises(files.PathNotAllowed, match="git's own files"):
                call()


def test_a_second_git_folder_cannot_be_made_to_take_over_a_repository(w):
    """Two ordinary folders and a redirect file: the config and hooks git would read come from a place AURA made."""
    other = os.path.join(w.work, "elsewhere")
    calls = [
        ("make_directory", {"path": os.path.join(other, "objects")}, "allow"),
        ("make_directory", {"path": os.path.join(other, "refs")}, "allow"),
        ("write_file", {"path": os.path.join(other, "HEAD"), "content": "ref: refs/heads/main\n"}, "deny"),
        ("write_file", {"path": os.path.join(other, "config"), "content": '[core]\n\tfsmonitor = "x"\n'}, "allow"),
        ("write_file", {"path": os.path.join(w.repo, ".git", "commondir"), "content": "../../elsewhere\n"}, "deny"),
    ]
    for tool, args, want in calls:
        d, _ = run(w, tool, args)
        assert d.kind == want, (tool, args, d.reason_code)
    assert not os.path.exists(os.path.join(w.repo, ".git", "commondir"))
