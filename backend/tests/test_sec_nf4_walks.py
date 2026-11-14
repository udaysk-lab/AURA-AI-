"""NF4: a recursive command run from a parent of a block-ruled (or ask-ruled) tree walks into it."""

from __future__ import annotations

import os

import pytest

from app.permissions import gate, rules
from tests.sec_world import build

POSIX, PWSH, CMD = "posix", "pwsh", "cmd"

WALKS = [
    (POSIX, "grep -r salary ."), (POSIX, "grep -R salary"), (POSIX, "grep -rn salary ."), (POSIX, "egrep -ri salary ."),
    (POSIX, "grep --recursive salary ."), (POSIX, "find . -exec cat {} +"), (POSIX, "find . -name '*.txt'"),
    (POSIX, "find ."), (POSIX, "ls -R"), (POSIX, "ls -lR ."), (POSIX, "rg salary"), (POSIX, "rg salary ."),
    (POSIX, "tree"), (POSIX, "tar czf /tmp/aura-nf4.tgz ."), (POSIX, "zip -r /tmp/aura-nf4.zip ."),
    (POSIX, "cp -r . /tmp/aura-nf4-copy"), (POSIX, "cp -a . /tmp/aura-nf4-copy"), (POSIX, "rsync -a . /tmp/aura-nf4-copy"),
    (POSIX, "du ."), (POSIX, "grep -r salary ~/work"), (POSIX, "find ~ -name '*.txt'"), (POSIX, "ls -R ~"),
    (PWSH, "Get-ChildItem -Recurse"), (PWSH, "gci -r -Filter *.txt"), (PWSH, "Get-ChildItem . -Recurse | Select-String salary"),
    (PWSH, "Copy-Item . C:\\tmp\\x -Recurse"), (PWSH, "dir -Recurse"),
    (CMD, "dir /s"), (CMD, "dir /s /b ."), (CMD, "robocopy . C:\\tmp\\x /e"), (CMD, "xcopy /s . C:\\tmp\\x"),
]
CONTROLS = [
    (POSIX, "grep -r salary other", None), (POSIX, "ls -R", "other"), (POSIX, "grep salary notes.txt", None),
    (POSIX, "grep -r salary notes.txt", None), (POSIX, "find other -name '*.txt'", None), (POSIX, "ls", None),
    (POSIX, "ls -R other", None), (POSIX, "tar xf archive.tar", None), (POSIX, "cp notes.txt /tmp/aura-nf4-n.txt", None),
    (PWSH, "Get-ChildItem other -Recurse", None), (CMD, "dir /s other", None), (POSIX, "git status", None),
]


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode="skip")
    fin = tmp_path / "home" / "work" / "finance"
    fin.mkdir()
    (fin / "salary.txt").write_text("salary: 1000000")
    (tmp_path / "home" / "work" / "notes.txt").write_text("hello")
    (tmp_path / "home" / "work" / "archive.tar").write_text("x")
    (tmp_path / "home" / "work" / "other").mkdir()
    (tmp_path / "home" / "work" / "other" / "o.txt").write_text("o")
    world.finance = str(fin)
    yield world
    world.db.close()
    world.engine.dispose()


def rule(w, effect):
    rules.add(w.db, w.user, effect=effect, system="device-control", tool=None, target_kind="path",
              pattern=w.finance + "/**", actor="user")
    w.db.commit()


def decide(w, command, dialect, rel=None):
    gate._dialect = lambda p: dialect
    wd = os.path.join(w.work, rel) if rel else w.work
    return gate.decide(w.db, w.user, "run_command", {"command": command, "workdir": wd},
                       gate.build_context(w.db, w.user, "chat"))


@pytest.mark.parametrize("dialect,command", WALKS)
def test_a_walk_over_a_blocked_tree_is_refused_naming_the_tree(w, dialect, command):
    rule(w, "block")
    d = decide(w, command, dialect)
    assert d.kind == "deny" and d.reason_code == "RULE_BLOCK", (command, d.kind, d.reason_code)
    assert "finance" in d.reason, d.reason


@pytest.mark.parametrize("dialect,command", WALKS)
def test_a_walk_over_an_ask_tree_asks(w, dialect, command):
    rule(w, "ask")
    d = decide(w, command, dialect)
    # (a walk from home also passes AURA's own folder: it asks for that reason in any case)
    assert d.kind == "ask" and d.reason_code in ("RULE_ASK", "ALWAYS_USER"), (command, d.kind, d.reason_code)


@pytest.mark.parametrize("effect", ["block", "ask"])
@pytest.mark.parametrize("dialect,command,rel", CONTROLS)
def test_commands_that_stay_out_of_the_tree_are_unchanged(w, effect, dialect, command, rel):
    rule(w, effect)
    d = decide(w, command, dialect, rel)
    assert d.kind == "allow", (command, d.kind, d.reason_code)


def test_without_rules_a_walk_is_unchanged(w):
    for dialect, command in WALKS[:6]:
        if "tar" in command or "cp" in command:
            continue
        assert decide(w, command, dialect).kind == "allow", command
