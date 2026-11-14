"""NF1: a command AURA cannot read in full always needs the user, in every mode; the home variables are
resolved and judged like literals; /proc/<anything>/cwd|root|environ|fd is AURA's own."""

from __future__ import annotations

import pytest

from app.models import UserSettings
from app.permissions import gate, rules, shellparse
from tests.sec_world import build

BASH, PWSH, CMD = "posix", "pwsh", "cmd"
MODES = ("skip", "auto", "ask")

NF1_BASH = [
    "v=v; cat ~/code/aura/backend/.en$v",
    "cat ~/code/aura/backend/$'\\x2e'env",
    "x=ur; cat ~/.a${x}a/a${x}a.env",
    "x=ur; cat ~/.a${x}a/a${x}a.e\"\"nv",
    "cat ~/.a$x/aura.env",
    'cat "~/.a$x/aura.env"',
    "cat ~/$d/aura.env",
    "cat /proc/$PPID/cwd/.env",
    "cat $(echo ~/.aura/aura.env)",
    "cat `echo ~/.aura/aura.env`",
    'cat "$(printf %s ~/.aura/aura.env)"',
    "cat <(cat ~/.aura/aura.env)",
    "eval 'cat ~/.au''ra/au''ra.env'",
    "echo aura.env | xargs cat",
    "bash -c 'cat $f'",
    "for f in a b; do cat $f; done",
    "cat $HOME/$x",
    "cat /tmp/$HOME",
    "$X args",
    "cat $1",
]
NF1_PWSH = [
    r"Get-Content ~\.aura\$x",
    r'$p = ".aura"; Get-Content ~\$p\aura.env',
    r"Get-Content $(Join-Path ~ .aura)\aura.env",
    r"& $cmd ~\.aura\aura.env",
    r"& ('Get-' + 'Content') x",
    r'iex "Get-Content ~/.aura/aura.env"',
    r'Invoke-Expression "Get-Content x"',
    r"Get-Content $env:APPDATA\x",
    r"Get-Content ${env:APPDATA}\x",
]
NF1_CMD = [
    r"type %APPDATA%\x",
    r"type %X%\aura.env",
    r"set p=.aura& type ~\%p%\aura.env",
    r"type !p!",
    r"for %i in (aura.env) do type ~\.aura\%i",
    r"type %USERPROFILE%\x%Y%",
]
PROC = [
    "cat /proc/self/cwd/.env", "cat /proc/self/environ", "cat /proc/1/fd/3", "cat /proc/12345/root/etc/hosts",
    "cat /proc/thread-self/cwd/.env", "cat /proc/*/environ", "cat /proc/self/c*/.env", "cat /proc//self//cwd/.env",
    "cat /proc/{self,1}/{cwd,x}/.env", "cat '/pro''c/self/cwd/.env'",
]
EVERYDAY = [
    (BASH, "git status"), (BASH, "ls ~/work"), (BASH, "python script.py"), (BASH, "cd $HOME/work && ls"),
    (BASH, "ls ${HOME}/work"), (BASH, 'cat "$HOME"/work/a.txt'), (BASH, "ls $PWD"), (BASH, "echo $HOME"),
    (BASH, "grep 'foo$' a.txt"), (BASH, 'grep "foo$" a.txt'), (BASH, "cd $HOME/work && python script.py"),
    (PWSH, r"Get-ChildItem $env:USERPROFILE\work"), (PWSH, r"Get-ChildItem $HOME\work"),
    (PWSH, r"Get-ChildItem ${env:USERPROFILE}\work"), (PWSH, "Get-Date"),
    (CMD, r"dir %USERPROFILE%\work"), (CMD, "echo hello"),
]


@pytest.fixture(params=MODES)
def w(request, tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode=request.param)
    world.mode = request.param
    (tmp_path / "home" / "work" / "a.txt").write_text("a")
    (tmp_path / "home" / "work" / "script.py").write_text("print(1)\n")
    yield world
    world.db.close()
    world.engine.dispose()


def decide(w, command, dialect, workdir=None):
    gate._dialect = lambda p: dialect
    return gate.decide(w.db, w.user, "run_command", {"command": command, "workdir": workdir or w.work},
                       gate.build_context(w.db, w.user, "chat"))


def needs_the_user(d):
    return d.kind == "deny" or (d.kind == "ask" and d.always_user and tuple(d.options) == ("once", "reject"))


@pytest.mark.parametrize("dialect,commands", [(BASH, NF1_BASH), (PWSH, NF1_PWSH), (CMD, NF1_CMD)])
def test_a_command_aura_cannot_read_in_full_always_needs_the_user(w, dialect, commands):
    bad = []
    for command in commands:
        d = decide(w, command, dialect)
        if not needs_the_user(d):
            bad.append((command, d.kind, d.reason_code))
    assert not bad, bad


def test_the_proof_commands_from_the_retest_never_return_aura_s_files(w):
    for command in NF1_BASH[:4] + ["cat /proc/$PPID/cwd/.env"]:
        d = decide(w, command, BASH)
        assert d.kind != "allow", command


@pytest.mark.parametrize("command", PROC)
def test_proc_cwd_root_environ_fd_are_refused_whatever_the_pid(w, command):
    d = decide(w, command, BASH)
    assert d.kind == "deny" and d.reason_code == "FLOOR_SELF_PATH", (command, d.kind, d.reason_code)


def test_the_unreadable_reasons_are_reported_by_the_parser():
    assert shellparse.parse("cat ~/a$x", BASH).unreadable
    assert shellparse.parse("cat $(ls)", BASH).unreadable
    assert shellparse.parse("cat $'a'", BASH).unreadable
    assert not shellparse.parse("cat ~/work/*.txt", BASH, "/tmp").unreadable
    assert not shellparse.parse("git status", BASH).unreadable
    assert not shellparse.parse("echo 'a $x'", BASH).unreadable
    assert shellparse.parse("iex $x", PWSH).unreadable
    assert shellparse.parse("type %X%", CMD).unreadable


@pytest.mark.parametrize("dialect,command", EVERYDAY)
def test_everyday_commands_are_still_allowed_in_skip_approvals(tmp_path, monkeypatch, dialect, command):
    world = build(tmp_path, monkeypatch, mode="skip")
    (tmp_path / "home" / "work" / "script.py").write_text("print(1)\n")
    (tmp_path / "home" / "work" / "a.txt").write_text("a")
    try:
        d = decide(world, command, dialect)
        assert d.kind == "allow", (command, d.kind, d.reason_code)
    finally:
        world.db.close()
        world.engine.dispose()


def test_a_resolved_home_variable_is_judged_like_the_literal(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode="skip")
    try:
        d = decide(world, "cat $HOME/.aura/aura.env", BASH)
        assert d.kind == "deny" and d.reason_code == "FLOOR_SELF_PATH"
        d = decide(world, 'cat "${HOME}"/.aura/aura.env', BASH)
        assert d.kind == "deny" and d.reason_code == "FLOOR_SELF_PATH"
        fin = tmp_path / "home" / "work" / "finance"
        fin.mkdir()
        (fin / "s.txt").write_text("x")
        rules.add(world.db, world.user, effect="block", system="device-control", tool=None, target_kind="path",
                  pattern=str(fin) + "/**", actor="user")
        world.db.commit()
        d = decide(world, "cat $HOME/work/finance/s.txt", BASH)
        assert d.kind == "deny" and d.reason_code == "RULE_BLOCK"
        d = decide(world, "cd $PWD/finance && cat s.txt", BASH)
        assert d.kind == "deny" and d.reason_code == "RULE_BLOCK"
    finally:
        world.db.close()
        world.engine.dispose()
