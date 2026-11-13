"""F7: path block and ask rules apply to the paths a shell command names, its working directory, and to
searches and listings."""

from __future__ import annotations

import os

import pytest

from app.permissions import gate, rules
from tests.sec_world import build, run

POSIX, PWSH, CMD = "posix", "pwsh", "cmd"


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode="skip")
    fin = tmp_path / "home" / "work" / "finance"
    fin.mkdir()
    (fin / "salary.txt").write_text("salary: 1000000")
    (fin / "sub").mkdir()
    (fin / "sub" / "deep.txt").write_text("salary deep")
    (tmp_path / "home" / "work" / "notes.txt").write_text("salary talk with the team")
    (tmp_path / "home" / "work" / "other").mkdir()
    world.finance = str(fin)
    yield world
    world.db.close()
    world.engine.dispose()


def hits_of(result):
    return result["results"] if isinstance(result, dict) else result


def block(w, effect="block", tree=None, tool=None):
    pattern = (tree or w.finance) + "/**"
    rules.add(w.db, w.user, effect=effect, system="device-control", tool=tool, target_kind="path",
              pattern=pattern, actor="user")
    w.db.commit()


def decide(w, tool, args, dialect=POSIX):
    gate._dialect = lambda p: dialect
    return gate.decide(w.db, w.user, tool, args, gate.build_context(w.db, w.user, "chat"))


SHELL_CASES = [
    # (dialect, command, workdir relative to home/work)
    (POSIX, "cat ~/work/finance/salary.txt", ""),                       # poc4
    (POSIX, "cat salary.txt", "finance"),                               # poc4: the working directory
    (POSIX, "ls", "finance"),
    (POSIX, "cat finance/salary.txt", ""),
    (POSIX, "cat ../finance/salary.txt", "other"),
    (POSIX, "cat ./finance/../finance/salary.txt", ""),
    (POSIX, "cat ~/work/fin*/sal*", ""),
    (POSIX, "cat ~/work/{finance,nothing}/salary.txt", ""),
    (POSIX, "echo x > ~/work/finance/new.txt", ""),
    (POSIX, "cat < ~/work/finance/salary.txt", ""),
    (POSIX, "cp ~/work/finance/salary.txt ~/work/copy.txt", ""),
    (POSIX, "grep salary ~/work/finance/sub/deep.txt", ""),
    (POSIX, "cat $HOME/work/finance/salary.txt", ""),
    (POSIX, "cd ~/work/finance && cat salary.txt", ""),
    (POSIX, "bash -c 'cat ~/work/finance/salary.txt'", ""),
    (PWSH, "Get-Content ~/work/finance/salary.txt", ""),
    (PWSH, "gc salary.txt", "finance"),
    (PWSH, "Get-Content -Path ~/work/fin*/salary.txt", ""),
    (PWSH, "echo x > ~/work/finance/new.txt", ""),
    (CMD, "type ~/work/finance/salary.txt", ""),
    (CMD, "type salary.txt", "finance"),
    (CMD, "type ~/work/fin*/salary.txt", ""),
]


@pytest.mark.parametrize("dialect,command,rel", SHELL_CASES)
def test_a_block_rule_stops_a_command_that_names_the_path(w, dialect, command, rel):
    block(w)
    workdir = os.path.join(w.work, rel) if rel else w.work
    d = decide(w, "run_command", {"command": command, "workdir": workdir}, dialect)
    assert d.kind == "deny" and d.reason_code == "RULE_BLOCK", (command, rel, d.kind, d.reason_code)


@pytest.mark.parametrize("dialect,command,rel", SHELL_CASES)
def test_an_ask_rule_makes_the_same_commands_ask(w, dialect, command, rel):
    block(w, effect="ask")
    workdir = os.path.join(w.work, rel) if rel else w.work
    d = decide(w, "run_command", {"command": command, "workdir": workdir}, dialect)
    assert d.kind == "ask" and d.reason_code == "RULE_ASK", (command, rel, d.kind, d.reason_code)


@pytest.mark.parametrize("dialect,command", [
    (POSIX, "cat ~/work/notes.txt"), (POSIX, "ls ~/work/other"), (POSIX, "cat ~/work/finance-old/x.txt"),
    (POSIX, "echo hello"), (PWSH, "Get-Content ~/work/notes.txt"), (CMD, "type ~/work/notes.txt"),
])
def test_other_paths_are_not_affected(w, dialect, command):
    block(w)
    d = decide(w, "run_command", {"command": command, "workdir": w.work}, dialect)
    assert d.kind == "allow", (command, d.reason_code)


def test_a_rule_for_one_tool_still_applies_to_that_tool_only(w):
    block(w, tool="read_file")
    d = decide(w, "run_command", {"command": "cat ~/work/finance/salary.txt", "workdir": w.work})
    assert d.kind == "allow"
    d = decide(w, "read_file", {"path": "~/work/finance/salary.txt"})
    assert d.kind == "deny" and d.reason_code == "RULE_BLOCK"


def test_a_path_rule_does_not_make_a_command_allowed_by_a_path_allow(w):
    """Allow rules need the command itself covered; a path in it is not enough (unchanged)."""
    rules.add(w.db, w.user, effect="allow", system="device-control", tool=None, target_kind="path",
              pattern=w.work + "/**", actor="user")
    w.db.commit()
    from app.models import UserSettings

    w.db.query(UserSettings).filter(UserSettings.user_id == w.user.id).update({"permission_mode": "ask"})
    w.db.commit()
    w.db.expire_all()
    d = decide(w, "run_command", {"command": "cat ~/work/notes.txt", "workdir": w.work})
    assert d.kind == "ask" and d.reason_code == "MODE_ASK"


# --- searches and listings ------------------------------------------------------------------------


def test_search_skips_a_blocked_tree_and_says_so(w):
    """poc4: search_files over the parent returned the blocked file's contents."""
    block(w)
    d, result = run(w, "search_files", {"query": "salary", "root": "~/work", "in_content": True})
    assert d.kind == "allow"
    paths = [h["path"] for h in hits_of(result)]
    assert any(p.endswith("notes.txt") for p in paths)
    assert not any("finance" in p for p in paths)
    assert "salary: 1000000" not in repr(result) and "salary deep" not in repr(result)
    assert result["skipped"] >= 1 and "blocked" in result["message"].lower()
    # by name too
    _d, result = run(w, "search_files", {"query": "salary", "root": "~/work"})
    assert not any("finance" in h["path"] for h in hits_of(result)) and result["skipped"] >= 1


def test_search_without_blocks_is_unchanged(w):
    d, result = run(w, "search_files", {"query": "salary", "root": "~/work", "in_content": True})
    assert d.kind == "allow"
    assert isinstance(result, list)  # nothing left out: the plain list it always was
    assert any(h["path"].endswith("salary.txt") for h in result)


def test_search_inside_a_blocked_tree_is_refused(w):
    block(w)
    d = decide(w, "search_files", {"query": "salary", "root": "~/work/finance"})
    assert d.kind == "deny" and d.reason_code == "RULE_BLOCK"


def test_a_search_over_an_ask_tree_needs_approval(w):
    block(w, effect="ask")
    d = decide(w, "search_files", {"query": "salary", "root": "~/work", "in_content": True})
    assert d.kind == "ask" and d.reason_code == "RULE_ASK"
    d = decide(w, "search_files", {"query": "salary", "root": "~/work/other"})
    assert d.kind == "allow"
    d = decide(w, "search_files", {"query": "salary", "root": "~"})
    assert d.kind == "ask"          # the home folder contains it too


def test_listing_hides_a_blocked_folder_and_says_so(w):
    block(w)
    d, result = run(w, "list_directory", {"path": "~/work"})
    assert d.kind == "allow"
    names = [e["name"] for e in result["entries"]]
    assert "notes.txt" in names and "finance" not in names
    assert result["skipped"] == 1 and "blocked" in result["message"].lower()
    d = decide(w, "list_directory", {"path": "~/work/finance"})
    assert d.kind == "deny" and d.reason_code == "RULE_BLOCK"
    d = decide(w, "list_directory", {"path": "~/work/finance/sub"})
    assert d.kind == "deny"


def test_listing_an_ask_tree_asks(w):
    block(w, effect="ask")
    d = decide(w, "list_directory", {"path": "~/work/finance"})
    assert d.kind == "ask" and d.reason_code == "RULE_ASK"
    d = decide(w, "list_directory", {"path": "~/work"})
    assert d.kind == "allow"


def test_a_skip_list_follows_the_decision_not_a_later_rule_change(w):
    """The handler uses the blocks the gate saw; a rule added afterwards cannot make a decision skip less."""
    block(w)
    d, result = run(w, "list_directory", {"path": "~/work"})
    assert result["skipped"] == 1
    assert d.skip_paths and d.skip_paths[0].endswith("finance" + os.sep + "**")
