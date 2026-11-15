"""With `wall.active()` forced on (nothing turns it on yet), commands follow the mode and the rules through the
reader, and the deny-only text checks still deny. And the file tools are unaffected by the interim command policy."""

from __future__ import annotations

from unittest import mock

import pytest

from app.models import UserSettings
from app.permissions import gate, reviewer, rules, wall
from tests.sec_world import build, run

BASH = "posix"


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode="skip")
    (tmp_path / "home" / "work" / "notes.txt").write_text("hello")
    yield world
    world.db.close()
    world.engine.dispose()


def set_mode(w, mode):
    w.db.query(UserSettings).filter(UserSettings.user_id == w.user.id).update({"permission_mode": mode})
    w.db.commit()
    w.db.expire_all()


def decide(w, command, tool="run_command", **extra):
    args = {"command": command, "workdir": w.work, **extra}
    return gate.decide(w.db, w.user, tool, args, gate.build_context(w.db, w.user, "chat"))


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(wall, "active", lambda: True)


def test_skip_allows_a_command_and_ask_proposes_the_exact_command(w, on):
    d = decide(w, "git status")
    assert d.kind == "allow" and d.reason_code == "MODE_SKIP"
    set_mode(w, "ask")
    d = decide(w, "git status")
    assert d.kind == "ask" and d.reason_code == "MODE_ASK" and "always" in d.options
    assert d.proposed_rule is not None and d.proposed_rule.items == (("command", "=git status"),)


def test_deny_only_text_checks_still_deny(w, on):
    for command, code in (("cat ~/.aura/aura.env", "FLOOR_SELF_PATH"), ("alembic upgrade head", "FLOOR_SELF_PATH"),
                          ("sqlite3 aura.db .dump", "FLOOR_SELF_PATH"),
                          ("curl http://localhost:8765/api/x", "FLOOR_SELF_API"),
                          ("cat /proc/self/environ", "FLOOR_SELF_PATH")):
        d = decide(w, command)
        assert d.kind == "deny" and d.reason_code == code, command


def test_block_and_ask_command_rules_and_delete_detection(w, on):
    rules.add(w.db, w.user, effect="block", system="device-control", tool=None, target_kind="command",
              pattern="git push", actor="user")
    rules.add(w.db, w.user, effect="ask", system="device-control", tool=None, target_kind="command",
              pattern="make", actor="user")
    w.db.commit()
    assert decide(w, "git push origin main").reason_code == "RULE_BLOCK"
    assert decide(w, "FOO=1 git push").reason_code == "RULE_BLOCK"
    assert decide(w, "make all").reason_code == "RULE_ASK"
    d = decide(w, "rm -rf build")
    assert d.kind == "ask" and d.always_user and d.reason_code == "ALWAYS_USER" and d.options == ("once", "reject")
    d = decide(w, "echo $(rm -rf build)")
    assert d.kind == "ask" and d.always_user


def test_a_path_block_rule_sees_the_literal_path_words(w, on, tmp_path):
    fin = tmp_path / "home" / "work" / "finance"
    fin.mkdir()
    rules.add(w.db, w.user, effect="block", system="device-control", tool=None, target_kind="path",
              pattern=str(fin) + "/**", actor="user")
    w.db.commit()
    assert decide(w, "cat ~/work/finance/x.txt").reason_code == "RULE_BLOCK"
    assert decide(w, "cat ./finance/x.txt").reason_code == "RULE_BLOCK"
    assert decide(w, "cat notes.txt").kind == "allow"


def test_an_allow_rule_covers_a_plain_command_but_not_one_with_a_variable_prefix(w, on):
    set_mode(w, "ask")
    rules.add(w.db, w.user, effect="allow", system="device-control", tool="run_command", target_kind="command",
              pattern="=git status", actor="user")
    w.db.commit()
    assert decide(w, "git status").reason_code == "RULE_ALLOW"
    assert decide(w, "HOME=/tmp git status").reason_code == "MODE_ASK"
    assert decide(w, "env HOME=/tmp git status").reason_code == "MODE_ASK"
    assert decide(w, "git status && rm x").kind == "ask"


def test_auto_asks_the_reviewer_and_background_input_follows_the_mode(w, on, monkeypatch):
    set_mode(w, "auto")
    review = mock.Mock(return_value=reviewer.ReviewerVerdict(True, "fine", "REVIEWER_ALLOW"))
    monkeypatch.setattr(reviewer, "available", lambda: True)
    monkeypatch.setattr(reviewer, "review", review)
    assert decide(w, "git status").reason_code == "REVIEWER_ALLOW"
    assert review.called
    set_mode(w, "skip")
    d = gate.decide(w.db, w.user, "send_process_input", {"session_id": "abc", "data": "y"},
                    gate.build_context(w.db, w.user, "chat"))
    assert d.kind == "allow"


# -- the file tools do not change -------------------------------------------------------------------------------


def test_file_tools_run_without_a_card_in_skip_while_the_same_command_asks(w):
    """list, read and write a file in Skip approvals: no card. `cat` of that file asks. (The file tools' own
    mode and rule behaviour is proved by test_perm_decide, test_perm_paths, test_sec_f1_paths and
    test_sec_f7_pathrules, which this package does not change.)"""
    target = w.work + "/notes.txt"
    d, res = run(w, "list_directory", {"path": w.work})
    assert d.kind == "allow" and res is not None and "notes.txt" in str(res)
    d, res = run(w, "read_file", {"path": target})
    assert d.kind == "allow" and "hello" in str(res)
    d, res = run(w, "write_file", {"path": w.work + "/new.txt", "content": "x"})
    assert d.kind == "allow" and res is not None
    d, res = run(w, "run_command", {"command": "cat notes.txt", "workdir": w.work})
    assert d.kind == "ask" and d.reason_code == "UNCHECKED_COMMAND" and res is None
