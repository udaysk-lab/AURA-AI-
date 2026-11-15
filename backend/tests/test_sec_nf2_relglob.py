"""NF2: a relative path whose first component is a wildcard. Its expansion is not read from the text: while computer
protection is off the command asks the owner, whatever the rules say."""

from __future__ import annotations

import os

import pytest

from app.permissions import gate, match, rules, shellparse
from tests.sec_world import build

POSIX, PWSH, CMD = "posix", "pwsh", "cmd"

CASES = [
    (POSIX, "cat */salary.txt"), (POSIX, "cat fin*/salary.txt"), (POSIX, "cat f?nance/salary.txt"),
    (POSIX, "cat [f]inance/salary.txt"), (POSIX, "cat {finance,x}/salary.txt"), (POSIX, "cat fin*/sal*"),
    (POSIX, "cat ./fin*/salary.txt"), (POSIX, "cat fin*"), (POSIX, "cat ../work/fin*/salary.txt"),
    (POSIX, "grep salary */salary.txt"), (POSIX, "cat *"),
    (PWSH, r"Get-Content fin*\salary.txt"), (PWSH, r"gc f*\salary.txt"),
    (CMD, r"type fin*\salary.txt"), (CMD, "type f*/salary.txt"),
]


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode="skip")
    fin = tmp_path / "home" / "work" / "finance"
    fin.mkdir()
    (fin / "salary.txt").write_text("salary: 1000000")
    (tmp_path / "home" / "work" / "notes.txt").write_text("hello")
    (tmp_path / "home" / "work" / "other").mkdir()
    world.finance = str(fin)
    yield world
    world.db.close()
    world.engine.dispose()


def rule(w, effect, tree=None):
    rules.add(w.db, w.user, effect=effect, system="device-control", tool=None, target_kind="path",
              pattern=(tree or w.finance) + "/**", actor="user")
    w.db.commit()


def decide(w, command, dialect):
    gate._dialect = lambda p: dialect
    return gate.decide(w.db, w.user, "run_command", {"command": command, "workdir": w.work},
                       gate.build_context(w.db, w.user, "chat"))


def test_a_relative_glob_is_not_expanded_from_the_text():
    p = shellparse.parse("cat */salary.txt fin*/x ~/work/a.txt", POSIX)
    assert match.literal_path_words(p) == ["~/work/a.txt"]


@pytest.mark.parametrize("effect", ["block", "ask"])
@pytest.mark.parametrize("dialect,command", CASES)
def test_a_relative_glob_is_never_allowed(w, dialect, command, effect):
    rule(w, effect)
    d = decide(w, command, dialect)
    assert d.kind == "ask" and d.reason_code == "UNCHECKED_COMMAND" and d.options == ("once", "reject"), (
        command, d.kind, d.reason_code)


@pytest.mark.parametrize("dialect,command", [
    (POSIX, "cat *.txt"), (POSIX, "cat o*/x"), (POSIX, "cat n*.txt"), (PWSH, r"Get-Content n*.txt"),
    (CMD, "type n*.txt"), (POSIX, "grep -h 'a.*b' notes.txt"),
])
def test_other_globs_ask_like_every_command_and_follow_the_mode_once_protection_is_on(w, monkeypatch, dialect, command):
    rule(w, "block")
    assert decide(w, command, dialect).reason_code == "UNCHECKED_COMMAND"
    from app.permissions import wall

    monkeypatch.setattr(wall, "active", lambda: True)
    assert decide(w, command, dialect).kind == "allow", command
