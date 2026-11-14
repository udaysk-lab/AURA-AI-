"""NF2: a relative path whose first component is a wildcard is a path reference, so block and ask rules see it."""

from __future__ import annotations

import pytest

from app.permissions import gate, rules, shellparse
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


def test_a_relative_glob_is_a_path_reference():
    p = shellparse.parse("cat */salary.txt fin*/x", POSIX, "/tmp")
    assert {r.raw for r in p.paths} >= {"*/salary.txt", "fin*/x"}


@pytest.mark.parametrize("dialect,command", CASES)
def test_a_block_rule_stops_a_relative_glob(w, dialect, command):
    rule(w, "block")
    d = decide(w, command, dialect)
    assert d.kind == "deny" and d.reason_code == "RULE_BLOCK", (command, d.kind, d.reason_code)


@pytest.mark.parametrize("dialect,command", CASES)
def test_an_ask_rule_makes_a_relative_glob_ask(w, dialect, command):
    rule(w, "ask")
    d = decide(w, command, dialect)
    assert d.kind == "ask" and d.reason_code == "RULE_ASK", (command, d.kind, d.reason_code)


def test_a_glob_that_could_match_a_blocked_folder_that_does_not_exist_yet(w):
    rule(w, "block", tree=w.work + "/future")
    d = decide(w, "cat fut*/x.txt", POSIX)
    assert d.kind == "deny" and d.reason_code == "RULE_BLOCK"


@pytest.mark.parametrize("dialect,command", [
    (POSIX, "cat *.txt"), (POSIX, "cat o*/x"), (POSIX, "cat n*.txt"), (PWSH, r"Get-Content n*.txt"),
    (CMD, "type n*.txt"), (POSIX, "grep -h 'a.*b' notes.txt"),
])
def test_other_globs_are_not_affected(w, dialect, command):
    rule(w, "block")
    d = decide(w, command, dialect)
    assert d.kind == "allow", (command, d.reason_code)
