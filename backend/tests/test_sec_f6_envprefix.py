"""F6: an allow rule never covers a command that sets environment variables (harmless ones included), and tool
configuration files are changed only with the user's say-so. Both hold while computer protection is off (every
command asks) and once it is on."""

from __future__ import annotations

import os

import pytest

from app.permissions import gate, match, rules, shellparse
from app.permissions.catalogue import CATALOGUE
from app.permissions.match import RuleSpec
from tests.sec_world import build

POSIX, PWSH, CMD = "posix", "pwsh", "cmd"


def covered(command, patterns, dialect=POSIX):
    parsed = shellparse.parse(command, dialect)
    rs = [RuleSpec(str(i), "allow", "command", match.normalise_command_pattern(p, "allow", dialect))
          for i, p in enumerate(patterns)]
    return match._command_covered(parsed, rs, dialect) is not None


# poc5.py / poc_shell.py: each of these ran an attacker's program under a plain allow rule.
POSIX_BAD = [
    ("HOME=/home/u/work/x git status", ["git status"]),
    ("XDG_CONFIG_HOME=/home/u/work/x git status", ["git status"]),
    ("JAVA_TOOL_OPTIONS=-javaagent:/home/u/work/a.jar mvn test", ["mvn test"]),
    ("RUSTC_WRAPPER=/home/u/work/w cargo build", ["cargo build"]),
    ("npm_config_script_shell=/home/u/work/w npm test", ["npm test"]),
    ("EDITOR=/home/u/work/w git commit", ["git commit"]),
    ("env HOME=/home/u/work/x git status", ["git status"]),
    ("env -i HOME=/x git status", ["git status"]),
    ("LANG=C HOME=/x git status", ["git status"]),
    ("HOME=/x LANG=C git status", ["git status"]),
    ("nohup HOME=/x git status", ["git status"]),
    ("time HOME=/x git status", ["git status"]),
    ("FOO=1 git status", ["git status"]),
    ("export HOME=/x; git status", ["git status", "export"]),
    ("declare -x HOME=/x; git status", ["git status", "declare"]),
    ("HOME=/x; git status", ["git status"]),
    ("bash -c 'HOME=/x git status'", ["git status", "bash"]),
    ("true && HOME=/x git status", ["git status", "true"]),
]
PWSH_BAD = [
    (r"$env:HOME='C:\x'; git status", ["git status"]),
    (r"$env:HOME = 'C:\x'; git status", ["git status"]),
    (r"$Env:GIT_CONFIG_GLOBAL='x'; git status", ["git status"]),
    (r"$env:XDG_CONFIG_HOME='C:\x'; git status", ["git status", "$env:XDG_CONFIG_HOME='C:\\x'"]),
    (r"Set-Item Env:HOME C:\x; git status", ["git status", "Set-Item"]),
    (r"Set-Content Env:HOME C:\x; git status", ["git status", "Set-Content"]),
    (r"New-Item -Path Env:HOME -Value C:\x; git status", ["git status", "New-Item"]),
    (r"[Environment]::SetEnvironmentVariable('HOME','C:\x'); git status", ["git status"]),
    (r"$env:PATH += ';C:\x'; git status", ["git status"]),
    (r"$env:HOME='C:\x' && git status", ["git status"]),
    (r"sc env:HOME C:\x; git status", ["git status", "sc"]),
]
CMD_BAD = [
    (r"set HOME=C:\x && git status", ["git status", "set"]),
    (r'set "HOME=C:\x" && git status', ["git status", "set"]),
    (r"set HOME=C:\x& git status", ["git status", "set"]),
    (r"set USERPROFILE=C:\x && git status", ["git status", "set"]),
    (r"setx HOME C:\x && git status", ["git status", "setx"]),
    (r"set /a X=1 && git status", ["git status", "set"]),
    (r'cmd /c "set HOME=C:\x&& git status"', ["git status", "cmd"]),
    (r"SET Home=C:\x && git status", ["git status", "set"]),
]

# No variable is "harmless" any more: nothing derived from a command's text decides what may run unasked.
ASSIGNING = [
    ("LANG=C git status", ["git status"], POSIX), ("LC_ALL=C TZ=UTC git status", ["git status"], POSIX),
    ("NO_COLOR=1 git status", ["git status"], POSIX), ("env LANG=C git status", ["git status"], POSIX),
    (r"$env:NO_COLOR=1; git status", ["git status"], PWSH), ("set NO_COLOR=1 && git status", ["git status"], CMD),
]


@pytest.fixture(autouse=True)
def _protection_on(monkeypatch):
    from app.permissions import wall

    monkeypatch.setattr(wall, "active", lambda: True)  # the matcher only decides once the wall is on


@pytest.mark.parametrize("command,patterns", POSIX_BAD)
def test_posix_environment_prefix_is_not_covered(command, patterns):
    assert not covered(command, patterns, POSIX), command


@pytest.mark.parametrize("command,patterns", PWSH_BAD)
def test_powershell_environment_assignment_is_not_covered(command, patterns):
    assert not covered(command, patterns, PWSH), command


@pytest.mark.parametrize("command,patterns", CMD_BAD)
def test_cmd_environment_assignment_is_not_covered(command, patterns):
    assert not covered(command, patterns, CMD), command


@pytest.mark.parametrize("command,patterns,dialect", ASSIGNING)
def test_no_variable_is_harmless_enough_to_be_covered(command, patterns, dialect):
    assert not covered(command, patterns, dialect), command
    assert covered("git status", ["git status"], dialect) or dialect == CMD


@pytest.mark.parametrize("dialect,command", [
    (POSIX, "HOME=/x git status"), (POSIX, "env HOME=/x git status"),
    (PWSH, r"$env:HOME='C:\x'; git status"), (CMD, r"set HOME=C:\x && git status"),
])
def test_the_gate_asks_instead_of_applying_the_allow_rule(tmp_path, monkeypatch, dialect, command):
    w = build(tmp_path, monkeypatch, mode="ask")
    monkeypatch.setattr(gate, "_dialect", lambda p: dialect)
    rules.add(w.db, w.user, effect="allow", system="device-control", tool="run_command", target_kind="command",
              pattern="git status", applies_to="", source="user", expires_at=None, note="", actor="user", dialect=dialect)
    d = gate.decide(w.db, w.user, "run_command", {"command": "git status", "workdir": w.work},
                    gate.build_context(w.db, w.user, "chat"))
    assert d.kind == "allow" and d.reason_code == "RULE_ALLOW"
    d = gate.decide(w.db, w.user, "run_command", {"command": command, "workdir": w.work},
                    gate.build_context(w.db, w.user, "chat"))
    assert d.kind == "ask" and d.reason_code == "MODE_ASK"
    assert d.proposed_rule is None or not d.proposed_rule.items  # no "always" pattern to remember from it
    # and with computer protection off, nothing is covered at all
    from app.permissions import wall

    monkeypatch.setattr(wall, "active", lambda: False)
    d = gate.decide(w.db, w.user, "run_command", {"command": "git status", "workdir": w.work},
                    gate.build_context(w.db, w.user, "chat"))
    assert d.kind == "ask" and d.reason_code == "UNCHECKED_COMMAND"
    w.db.close()


# --- tool configuration files ---------------------------------------------------------------------

CONFIG_FILES = [
    "work/proj/.npmrc", "work/proj/.gitconfig", "work/proj/.yarnrc", "work/proj/.yarnrc.yml", "work/pip.conf",
    "work/pip.ini", "work/proj/.cargo/config", "work/proj/.cargo/config.toml", "work/.m2/settings.xml",
    "work/proj/.NPMRC", "work/proj/.Npmrc.", "work/proj/.npmrc ", "work/proj/.npmrc::$DATA", "work/PIP.INI",
    "~/work/proj/.npmrc", "work/proj/sub/../.npmrc",
]


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode="skip")
    (tmp_path / "home" / "work" / "proj" / "sub").mkdir(parents=True)
    (tmp_path / "home" / "work" / ".m2").mkdir(parents=True)
    (tmp_path / "home" / "work" / "proj" / ".cargo").mkdir(parents=True)
    yield world
    world.db.close()
    world.engine.dispose()


@pytest.mark.parametrize("rel", CONFIG_FILES)
@pytest.mark.parametrize("tool", ["write_file", "edit_file", "move_file"])
def test_file_tools_need_the_user_for_tool_configuration(w, rel, tool):
    path = rel if rel.startswith("~") else os.path.join(w.home, rel)
    probe = os.path.join(w.home, "work", "seed.txt")
    with open(probe, "w") as fh:
        fh.write("x")
    args = {"write_file": {"path": path, "content": "registry=https://evil.example\n"},
            "edit_file": {"path": path, "find": "a", "replace": "b"},
            "move_file": {"source": probe, "destination": path}}[tool]
    if tool == "edit_file":
        os.makedirs(os.path.dirname(os.path.expanduser(path)), exist_ok=True)
        with open(os.path.expanduser(path), "w") as fh:
            fh.write("a")
    d = gate.decide(w.db, w.user, tool, args, gate.build_context(w.db, w.user, "chat"))
    assert d.kind == "ask" and d.always_user and d.options == ("once", "reject"), (rel, tool, d.kind, d.reason_code)


def test_ordinary_files_are_still_written_without_asking_in_skip_mode(w):
    d = gate.decide(w.db, w.user, "write_file", {"path": os.path.join(w.home, "work", "notes.txt"), "content": "x"},
                    gate.build_context(w.db, w.user, "chat"))
    assert d.kind == "allow" and d.reason_code == "MODE_SKIP"
    d = gate.decide(w.db, w.user, "write_file", {"path": os.path.join(w.home, "work", "npmrc.txt"), "content": "x"},
                    gate.build_context(w.db, w.user, "chat"))
    assert d.kind == "allow"


@pytest.mark.parametrize("dialect,command", [
    (POSIX, "echo registry=x > .npmrc"), (POSIX, "echo x >> proj/.gitconfig"), (POSIX, "printf x > pip.conf"),
    (POSIX, "echo x > ~/work/.m2/settings.xml"), (POSIX, "cat a > proj/.cargo/config.toml"),
    (PWSH, "echo x > .npmrc"), (PWSH, r"echo x > proj\.yarnrc.yml"), (PWSH, "echo x >> pip.ini"),
    (CMD, "echo x > .npmrc"), (CMD, r"echo x > proj\.gitconfig"), (CMD, "echo x>pip.ini"),
    (POSIX, "git config --global user.name x"), (POSIX, "git config core.fsmonitor 'sh -c id'"),
    (POSIX, "npm config set registry https://evil.example"), (POSIX, "pip config set global.index-url http://x"),
    (POSIX, "yarn config set npmRegistryServer http://x"),
    (CMD, "git config --global alias.st status"), (PWSH, "git config --global core.pager id"),
])
def test_shell_writes_to_tool_configuration_need_the_user_each_time(w, wall_on, monkeypatch, dialect, command):
    monkeypatch.setattr(gate, "_dialect", lambda p: dialect)
    d = gate.decide(w.db, w.user, "run_command", {"command": command, "workdir": os.path.join(w.home, "work")},
                    gate.build_context(w.db, w.user, "chat"))
    assert d.kind == "ask" and d.always_user and d.options == ("once", "reject"), (command, d.kind, d.reason_code)


@pytest.mark.parametrize("dialect,command", [
    (POSIX, "git config --get user.name"), (POSIX, "git config --list"), (POSIX, "npm config get registry"),
    (POSIX, "pip config list"), (POSIX, "echo x > notes.txt"),
])
def test_reading_tool_configuration_is_not_held_up(w, wall_on, monkeypatch, dialect, command):
    monkeypatch.setattr(gate, "_dialect", lambda p: dialect)
    d = gate.decide(w.db, w.user, "run_command", {"command": command, "workdir": os.path.join(w.home, "work")},
                    gate.build_context(w.db, w.user, "chat"))
    assert d.kind == "allow", (command, d.kind, d.reason_code)


def test_the_catalogue_still_knows_the_tool():
    assert CATALOGUE["run_command"].dyn_always_user
