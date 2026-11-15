"""F4: wildcards, braces and extended globs. Reading a command's text cannot tell what a pattern expands to, so
while computer protection is off every such command is refused by a floor or asks the owner (never `allow`)."""

from __future__ import annotations

import os

import pytest

from app.permissions import canon, gate, rules
from tests.sec_world import build

BASH = "posix"
PWSH = "pwsh"
CMD = "cmd"

SELF_BASH = [
    "cat ~/.au[r]a/aur[a].env",
    "cat ~/.?ura/*.env",
    "cat ~/.a*/aura.env",
    "cat ~/.au?a/aura.e?v",
    "sqlite3 ~/.au?a/aura.d? .dump",
    "cd ~/.au?a && cat *.env",
    "cat ~/.{aura,none}/aura.env",
    "cat ~/{.aura,none}/aura.env",
    "cat ~/.au@(r)a/aura.env",
    "cat ~/.au{r,x}a/aura.{env,db}",
    "cat ~/.a[u-v]ra/*",
    "cat \"$HOME\"/.au?a/aura.env",
    "cat ${HOME}/.au?a/aura.env",
    "tar -cf /tmp/x.tar -C ~ .au?a",
    "cat ~/.a\\ura/aura.env",
    "cat ~/.au''ra/aur''a.env",
    "cp ~/.a*/aura.env /tmp/copy",
    "cat < ~/.au?a/aura.env",
    "echo x > ~/.au?a/new.txt",
    "less ~/.?ura/local-login.ke?",
    "cat .en?",          # run in the server's folder: the env file
    "cat aura.d?",
    "cat ../.en?",
    "cat ./.en[v]",
]
SELF_PWSH = [
    r"Get-Content ~\.au?a\aura.env",
    r"gc ~/.a*/aura.e*",
    r"cat $HOME\.aur[a]\aura.env",
    r'Get-Content "$env:USERPROFILE\.a*\*.env"',
    r"Get-Content -Path $env:USERPROFILE\.a*\aura.env",
    r"Copy-Item ~\.au?a\aura.env C:\temp\x",
    r"Get-Content ~\.aura.\aura.env",       # a trailing dot names the same folder on Windows
    r"Get-Content '~\.aura \aura.env'",     # a trailing space too
    r"Get-Content .en?",
]
SELF_CMD = [
    r"type %USERPROFILE%\.au?a\aura.env",
    r"type %USERPROFILE%\.a*\aura.e*",
    r'type "%USERPROFILE%\.au?a\*.env"',
    r"copy ~\.au?a\aura.env x",
    r"type ~\.au?a\aura.env",
    r"type %HOME%\.aura.\aura.env",
    r"type .en?",
]

CONTROLS = {
    BASH: ["ls ~/work/*.txt", "cat ~/work/{a,b}.txt", "echo hello", "git status", "ls ~/work/*"],
    PWSH: [r"Get-ChildItem ~\work\*.txt", "Get-Date"],
    CMD: [r"dir ~\work\*.txt", "echo hello"],
}


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode="skip")
    (tmp_path / "home" / "work" / "a.txt").write_text("a")
    (tmp_path / "home" / "work" / "b.txt").write_text("b")
    yield world
    world.db.close()
    world.engine.dispose()


def decide(w, command, dialect, workdir=None):
    gate._dialect = lambda p: dialect  # build() patched the module attribute; keep it consistent
    args = {"command": command, "workdir": workdir or w.work}
    return gate.decide(w.db, w.user, "run_command", args, gate.build_context(w.db, w.user, "chat"))


@pytest.mark.parametrize("dialect,commands", [(BASH, SELF_BASH), (PWSH, SELF_PWSH), (CMD, SELF_CMD)])
def test_wildcards_cannot_reach_aura_s_own_files(w, dialect, commands):
    for command in commands:
        workdir = w.proj if command.split()[-1] in (".en?", "aura.d?", "../.en?", "./.en[v]") else w.work
        d = decide(w, command, dialect, workdir)
        assert d.kind in ("deny", "ask"), command
        if d.kind == "ask":
            assert d.reason_code == "UNCHECKED_COMMAND" and d.options == ("once", "reject"), command


@pytest.mark.parametrize("dialect", [BASH, PWSH, CMD])
def test_ordinary_wildcard_commands_ask_every_time(w, dialect):
    for command in CONTROLS[dialect]:
        d = decide(w, command, dialect)
        assert d.kind == "ask" and d.reason_code == "UNCHECKED_COMMAND", (command, d.kind, d.reason_code)


def test_searching_through_home_asks_each_time_or_is_refused(w):
    for command in ("find ~ -name '*.env' -path '*ura*'", "grep -r SECRET_KEY ~ --include=*.env",
                    "grep -R SECRET_KEY ~", "rg SECRET_KEY ~", "ls -R ~", "find ~ -name '*.db'"):
        d = decide(w, command, BASH)
        assert d.kind in ("deny", "ask"), command
        if d.kind == "ask":
            assert d.always_user and d.reason_code == "UNCHECKED_COMMAND" and d.options == ("once", "reject")
    # naming one of AURA's files outright is refused, not merely asked about
    d = decide(w, "find ~ -name aura.env", BASH)
    assert d.kind == "deny" and d.reason_code == "FLOOR_SELF_PATH"
    for dialect, command in ((PWSH, r"Get-ChildItem -Recurse -Filter *.env ~"), (CMD, r"dir /s /b ~\*.env")):
        d = decide(w, command, dialect)
        assert d.kind == "ask" and d.always_user and d.reason_code == "UNCHECKED_COMMAND"


@pytest.mark.parametrize("dialect,command", [
    (BASH, "ls ~/work/*.txt"), (BASH, "cat ~/work/{a,b}.txt"), (BASH, "ls ~/work/a.tx[t]"), (BASH, "ls ~/work/a.txt"),
    (PWSH, r"Get-ChildItem ~\work\*.txt"), (CMD, r"dir ~\work\*.txt"),
])
def test_an_allow_rule_never_covers_a_command(w, dialect, command):
    program = command.split()[0]
    for pattern in (program, "=" + command):
        rules.add(w.db, w.user, effect="allow", system="device-control", tool="run_command", target_kind="command",
                  pattern=pattern, applies_to="", source="user", expires_at=None, note="", actor="user", dialect=dialect)
    w.db.commit()
    for mode in ("ask", "skip"):
        from app.models import UserSettings

        w.db.query(UserSettings).filter(UserSettings.user_id == w.user.id).update({"permission_mode": mode})
        w.db.commit()
        w.db.expire_all()
        d = decide(w, command, dialect)
        assert d.kind == "ask" and d.reason_code == "UNCHECKED_COMMAND", (command, mode, d.kind, d.reason_code)


def test_brace_expansion_is_bounded():
    assert canon.expand_braces("a{1..3}b") == ["a1b", "a2b", "a3b"]
    assert canon.expand_braces("{a,b}{c,d}") == ["ac", "ad", "bc", "bd"]
    assert canon.expand_braces("{1..99999}") is None
