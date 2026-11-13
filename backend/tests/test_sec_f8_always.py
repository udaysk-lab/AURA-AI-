"""F8: "always" on a command proposes the exact command; a prefix only for a curated read-only list."""

from __future__ import annotations

import pytest

from app.permissions import match, shellparse
from app.permissions.match import RuleSpec

POSIX, PWSH, CMD = "posix", "pwsh", "cmd"


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
]
READ_ONLY = [
    ("git status", "git status"), ("git status -s", "git status"), ("git log --oneline -5", "git log"),
    ("git diff HEAD~1", "git diff"), ("ls", "ls"), ("ls -la src", "ls"), ("pwd", "pwd"), ("whoami", "whoami"),
    ("python --version", "python --version"),
]


@pytest.mark.parametrize("command", BROAD)
def test_always_is_the_exact_command(command):
    assert pats(command) == ["=" + " ".join(shellparse.parse(command).segments[0].tokens)]
    # exact: the same command is covered, a different one from the same family is not
    assert covered(command, pats(command))
    assert not covered(command + " --extra-flag-xyz", pats(command))


@pytest.mark.parametrize("command,prefix", READ_ONLY)
def test_prefix_only_for_read_only_commands(command, prefix):
    assert pats(command) == [prefix]
    assert covered(command, pats(command))


def test_dir_is_a_prefix_on_windows_and_writing_flags_are_exact():
    assert pats("dir /b", CMD) == ["dir"]
    assert pats("git diff --output=out.txt") == ["=git diff --output=out.txt"]
    assert not covered("git config user.name x", pats("git status"))
    assert not covered("pip install x", pats("pip --version"))
