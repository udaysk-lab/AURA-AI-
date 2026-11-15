"""F8: "always" on a command proposes the exact command, never a prefix. (Nothing is proposed at all while computer
protection is off: see test_r2_unchecked.py.)"""

from __future__ import annotations

import pytest

from app.permissions import match, shellparse
from app.permissions.match import RuleSpec

POSIX, PWSH, CMD = "posix", "pwsh", "cmd"


@pytest.fixture(autouse=True)
def _protection_on(monkeypatch):
    from app.permissions import wall

    monkeypatch.setattr(wall, "active", lambda: True)


def pats(command, dialect=POSIX):
    return match.command_card_patterns(shellparse.parse(command, dialect))


def covered(command, patterns, dialect=POSIX):
    parsed = shellparse.parse(command, dialect)
    rs = [RuleSpec(str(i), "allow", "command", match.normalise_command_pattern(p, "allow", dialect))
          for i, p in enumerate(patterns)]
    return match._command_covered(parsed, rs, dialect) is not None


BROAD = [
    "git config user.name x", "git config --global core.editor vim", "pip install requests", "docker run alpine",
    "ssh host", "ssh host ls", "npm install left-pad", "npm run build", "git push origin main", "git commit -m x",
    "python -m pytest", "python script.py", "cargo build", "curl https://example.com", "cat /etc/hosts",
    "git status", "git status -s", "git log --oneline -5", "git diff HEAD~1", "ls", "ls -la src", "pwd", "whoami",
    "python --version",
]


@pytest.mark.parametrize("command", BROAD)
def test_always_is_the_exact_command(command):
    assert pats(command) == ["=" + " ".join(shellparse.parse(command).segments[0].tokens)]
    # exact: the same command is covered, a different one from the same family is not
    assert covered(command, pats(command))
    assert not covered(command + " --extra-flag-xyz", pats(command))


def test_dir_and_writing_flags_are_exact_too():
    assert pats("dir /b", CMD) == ["=dir /b"]
    assert pats("git diff --output=out.txt") == ["=git diff --output=out.txt"]
    assert not covered("git config user.name x", pats("git status"))
    assert not covered("pip install x", pats("pip --version"))
