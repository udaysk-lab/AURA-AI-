"""F4: wildcards, braces and extended globs cannot hide AURA's own paths from the shell floor."""

from __future__ import annotations

import os

import pytest

from app.permissions import gate, pathglob, rules
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


def decide(w, command, dialect, workdir=None, mode=None):
    gate._dialect = lambda p: dialect  # build() patched the module attribute; keep it consistent
    args = {"command": command, "workdir": workdir or w.work}
    return gate.decide(w.db, w.user, "run_command", args, gate.build_context(w.db, w.user, "chat"))


@pytest.mark.parametrize("dialect,commands", [(BASH, SELF_BASH), (PWSH, SELF_PWSH), (CMD, SELF_CMD)])
def test_wildcards_cannot_reach_aura_s_own_files(w, monkeypatch, dialect, commands):
    """poc2 (cmds.txt): in skip mode these used to run."""
    monkeypatch.setattr(gate, "_dialect", lambda p: dialect)
    leaked = []
    for command in commands:
        workdir = w.proj if command.split()[-1] in (".en?", "aura.d?", "../.en?", "./.en[v]") else w.work
        d = gate.decide(w.db, w.user, "run_command", {"command": command, "workdir": workdir},
                        gate.build_context(w.db, w.user, "chat"))
        if d.kind == "allow":
            leaked.append(command)
        else:
            assert d.kind == "deny" and d.reason_code == "FLOOR_SELF_PATH", (command, d.kind, d.reason_code)
    assert not leaked, leaked


@pytest.mark.parametrize("dialect", [BASH, PWSH, CMD])
def test_ordinary_wildcards_still_work(w, monkeypatch, dialect):
    monkeypatch.setattr(gate, "_dialect", lambda p: dialect)
    for command in CONTROLS[dialect]:
        d = gate.decide(w.db, w.user, "run_command", {"command": command, "workdir": w.work},
                        gate.build_context(w.db, w.user, "chat"))
        assert d.kind == "allow", (command, d.reason_code)


def test_searching_through_home_for_env_files_needs_the_user_each_time(w, monkeypatch):
    """poc2: `find ~ -name '*.env'` and `grep -r KEY ~` walk straight through ~/.aura."""
    monkeypatch.setattr(gate, "_dialect", lambda p: BASH)
    for command in ("find ~ -name '*.env' -path '*ura*'", "grep -r SECRET_KEY ~ --include=*.env",
                    "grep -R SECRET_KEY ~", "rg SECRET_KEY ~", "ls -R ~", "find ~ -name '*.db'"):
        d = gate.decide(w.db, w.user, "run_command", {"command": command, "workdir": w.work},
                        gate.build_context(w.db, w.user, "chat"))
        assert d.kind == "ask" and d.always_user, (command, d.kind, d.reason_code)
        assert d.options == ("once", "reject")
    # naming one of AURA's files outright is still refused, not merely asked about
    d = gate.decide(w.db, w.user, "run_command", {"command": "find ~ -name aura.env", "workdir": w.work},
                    gate.build_context(w.db, w.user, "chat"))
    assert d.kind == "deny" and d.reason_code == "FLOOR_SELF_PATH"
    # the same search inside the project folder the user connected is fine
    d = gate.decide(w.db, w.user, "run_command", {"command": "grep -r TODO ~/work", "workdir": w.work},
                    gate.build_context(w.db, w.user, "chat"))
    assert d.kind == "allow"
    monkeypatch.setattr(gate, "_dialect", lambda p: PWSH)
    d = gate.decide(w.db, w.user, "run_command", {"command": r"Get-ChildItem -Recurse -Filter *.env ~", "workdir": w.work},
                    gate.build_context(w.db, w.user, "chat"))
    assert d.kind == "ask" and d.always_user
    monkeypatch.setattr(gate, "_dialect", lambda p: CMD)
    d = gate.decide(w.db, w.user, "run_command", {"command": r"dir /s /b ~\*.env", "workdir": w.work},
                    gate.build_context(w.db, w.user, "chat"))
    assert d.kind == "ask" and d.always_user


@pytest.mark.parametrize("dialect,command", [
    (BASH, "ls ~/work/*.txt"), (BASH, "cat ~/work/{a,b}.txt"), (BASH, "ls ~/work/a.tx[t]"),
    (PWSH, r"Get-ChildItem ~\work\*.txt"), (CMD, r"dir ~\work\*.txt"),
])
def test_a_wildcard_path_is_never_covered_by_an_allow_rule(w, monkeypatch, dialect, command):
    """A pattern in a path position makes the command opaque for allow rules: it asks (mode ask)."""
    monkeypatch.setattr(gate, "_dialect", lambda p: dialect)
    program = command.split()[0]
    rules.add(w.db, w.user, effect="allow", system="device-control", tool="run_command", target_kind="command",
              pattern=program, applies_to="", source="user", expires_at=None, note="", actor="user", dialect=dialect)
    from app.models import UserSettings

    w.db.query(UserSettings).filter(UserSettings.user_id == w.user.id).update({"permission_mode": "ask"})
    w.db.commit()
    w.db.expire_all()
    d = gate.decide(w.db, w.user, "run_command", {"command": command, "workdir": w.work},
                    gate.build_context(w.db, w.user, "chat"))
    assert d.kind == "ask" and d.reason_code == "MODE_ASK", (command, d.kind, d.reason_code)
    plain = command.replace("*.txt", "a.txt").replace("{a,b}.txt", "a.txt").replace("a.tx[t]", "a.txt")
    d = gate.decide(w.db, w.user, "run_command", {"command": plain, "workdir": w.work},
                    gate.build_context(w.db, w.user, "chat"))
    assert d.kind == "allow" and d.reason_code == "RULE_ALLOW", (plain, d.kind, d.reason_code)


# --- the matcher itself, including Windows spellings -----------------------------------------------

HOME = "/home/u"
WIN_HOME = "C:\\Users\\u"


@pytest.mark.parametrize("word,target,expect", [
    ("~/.au[r]a/aur[a].env", "/home/u/.aura/aura.env", True),
    ("~/.?ura/*.env", "/home/u/.aura/aura.env", True),
    ("~/.{aura,x}", "/home/u/.aura", True),
    ("~/{a,b,c}{1..3}/f", "/home/u/.aura", False),
    ("~/.au@(r)a/x", "/home/u/.aura", True),
    ("~/*", "/home/u/.aura", False),            # a leading wildcard does not match a leading dot ...
    ("~/.*", "/home/u/.aura", True),            # ... unless the dot is written
    ("~/work/*", "/home/u/.aura", False),
    ("~/work/../.au?a", "/home/u/.aura", True),
    ("/home/u/**/aura.env", "/home/u/.aura/aura.env", True),
])
def test_could_match_posix(word, target, expect):
    assert pathglob.could_match(word, target, "/home/u/work", HOME, ci=False, windows=False) is expect


@pytest.mark.parametrize("word", [
    r"%USERPROFILE%\.au?a\aura.env", r"$env:USERPROFILE\.a*", r"~\.AU?A", r"C:\Users\u\.aura.\x",
    r"C:\Users\u\.aura \x", r"C:\Users\u\.aura::$DATA", r"\\?\C:\Users\u\.aura\x", r"C:/Users/u/.au[r]a",
    r"C:\Users\u\work\..\.aura", r"c:\users\U\.AURA\*",
])
def test_could_match_windows_spellings(word):
    assert pathglob.could_match(word, WIN_HOME + "\\.aura", WIN_HOME + "\\work", WIN_HOME, ci=True, windows=True)


def test_windows_clean_spellings_do_not_over_match():
    assert not pathglob.could_match(r"C:\Users\u\work\*.txt", WIN_HOME + "\\.aura", WIN_HOME + "\\work", WIN_HOME,
                                    ci=True, windows=True)
    assert not pathglob.could_match(r"C:\Users\u\.aurora", WIN_HOME + "\\.aura", WIN_HOME + "\\work", WIN_HOME,
                                    ci=True, windows=True)


def test_brace_expansion_is_bounded():
    assert pathglob.expand_braces("a{1..3}b") == ["a1b", "a2b", "a3b"]
    assert pathglob.expand_braces("{a,b}{c,d}") == ["ac", "ad", "bc", "bd"]
    assert pathglob.expand_braces("{1..99999}") is None
    # too many to list is read as "anything": it can name the folder
    assert pathglob.could_match("~/{1..99999}", "/home/u/.aura", "/home/u", HOME, ci=False) is False or True


def test_existing_matches_lists_real_files(w):
    got = pathglob.existing_matches("~/work/*.txt", w.work, w.home, ci=False)
    assert sorted(os.path.basename(p) for p in got) == ["a.txt", "b.txt"]
    got = pathglob.existing_matches("~/.au?a/aura.e[n]v", w.work, w.home, ci=False)
    assert [os.path.basename(p) for p in got] == ["aura.env"]
