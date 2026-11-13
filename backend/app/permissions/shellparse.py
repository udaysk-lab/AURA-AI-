"""A shell command line, taken apart far enough to apply permission rules.

This is not a shell. It reads a command the way bash/zsh/sh, PowerShell or cmd
would split it, and answers the questions the permission rules ask (spec
10-permissions.md section 5.2):

* what are the separate commands in it (segments)?
* is there anything in it whose effect cannot be read from the text (opaque)?
* does it delete files, or look dangerous?
* which paths does it name (so the floors can check them)?

The rule it follows everywhere: when in doubt, call it opaque. An opaque command
is never matched by an allow rule; it can still be asked about, blocked, or run
once with the user's say-so.

Pure functions: no I/O except the optional "does this relative path exist" probe,
and nothing here decides anything. `permissions/match.py` applies rules to the
result.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from app.device import policy as _policy
from app.permissions import pathglob as _pathglob

POSIX, PWSH, CMD = "posix", "pwsh", "cmd"
DIALECTS = (POSIX, PWSH, CMD)

MAX_DEPTH = 4  # nested `bash -c "bash -c ..."`; deeper than this is opaque


def dialect_for_shell_kind(kind: str) -> str:
    """Map `device.runtime.capabilities()["shell"]["kind"]` to a dialect."""
    k = (kind or "").strip().lower()
    if k in ("powershell", "pwsh", "powershell.exe", "pwsh.exe"):
        return PWSH
    if k in ("cmd", "cmd.exe"):
        return CMD
    return POSIX


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PathRef:
    """A path the command names. role is "read" or "write"; `raw` is as written."""

    raw: str
    role: str = "read"


@dataclass(frozen=True)
class Segment:
    """One simple command.

    `text` is the command after wrappers (env, nohup, timeout ...) are removed,
    tokens joined by single spaces with quotes removed; `outer` is the same
    before unwrapping. Block and ask rules look at both; allow rules at `text`.
    """

    text: str
    outer: str
    tokens: tuple[str, ...]
    program: str
    opaque: bool = False
    dangerous: bool = False
    deletes: bool = False
    wrappers: tuple[str, ...] = ()
    inline: bool = False  # this segment only launches nested ones (bash -c ...)


@dataclass(frozen=True)
class Parsed:
    dialect: str
    raw: str
    segments: tuple[Segment, ...]
    opaque: bool
    paths: tuple[PathRef, ...] = ()
    deletes: bool = False
    dangerous: bool = False
    reasons: tuple[str, ...] = field(default=(), compare=False)

    @property
    def offers_durations(self) -> bool:
        """False when an approval card may only offer once / reject."""
        return not (self.opaque or self.dangerous)

    @property
    def matchable_segments(self) -> tuple[Segment, ...]:
        """The segments an allow rule has to cover (launcher segments excluded)."""
        return tuple(s for s in self.segments if not s.inline)


# ---------------------------------------------------------------------------
# Tokeniser
# ---------------------------------------------------------------------------


@dataclass
class _Word:
    value: str
    start: int
    end: int
    quoted: bool = False
    var_start: bool = False


@dataclass
class _Redir:
    op: str
    fd: str = ""


_OPS = {
    POSIX: (";", "&&", "||", "|&", "|", "&", "\n"),
    PWSH: (";", "&&", "||", "|", "\n"),
    CMD: ("&&", "||", "&", "|", "\n"),
}

_CMD_VAR = re.compile(r"%[^%\s\"]+%|![^!\s\"]+!")
_FD = re.compile(r"^\d+$")


class _Scan:
    def __init__(self, text: str, dialect: str):
        self.text = text
        self.d = dialect
        self.items: list = []
        self.opaque = False
        self.reasons: list[str] = []

    def flag(self, why: str) -> None:
        self.opaque = True
        if why not in self.reasons:
            self.reasons.append(why)

    # -- helpers -----------------------------------------------------------

    def run(self) -> None:
        t, d = self.text, self.d
        n = len(t)
        i = 0
        buf: list[str] = []
        start = 0
        quoted = False
        in_word = False
        var_start = False

        def flush(end: int) -> None:
            nonlocal buf, in_word, quoted, var_start
            if in_word:
                self.items.append(_Word("".join(buf), start, end, quoted, var_start))
            buf, in_word, quoted, var_start = [], False, False, False

        def begin(at: int) -> None:
            nonlocal in_word, start
            if not in_word:
                in_word = True
                start = at

        if d == CMD and _CMD_VAR.search(t):
            self.flag("variable expansion")

        while i < n:
            c = t[i]
            nxt = t[i + 1] if i + 1 < n else ""

            # ---- whitespace and newlines
            if c in " \t\r":
                flush(i)
                i += 1
                continue
            if c == "\n":
                flush(i)
                self.items.append("\n")
                i += 1
                continue

            # ---- escapes
            if (d == POSIX and c == "\\") or (d == PWSH and c == "`") or (d == CMD and c == "^"):
                if nxt == "\n":
                    i += 2  # line continuation: joins nothing, starts nothing
                elif d == POSIX and nxt == "\r" and t[i + 2 : i + 3] == "\n":
                    i += 3
                elif i + 1 >= n:
                    begin(i)
                    buf.append(c)
                    i += 1
                else:
                    begin(i)
                    buf.append(nxt)
                    i += 2
                continue

            # ---- quotes
            if c == "'" and d != CMD:
                begin(i)
                quoted = True
                i += 1
                closed = False
                while i < n:
                    if t[i] == "'":
                        if d == PWSH and t[i + 1 : i + 2] == "'":
                            buf.append("'")
                            i += 2
                            continue
                        closed = True
                        i += 1
                        break
                    buf.append(t[i])
                    i += 1
                if not closed:
                    self.flag("unterminated quote")
                continue
            if c == '"':
                begin(i)
                quoted = True
                i += 1
                closed = False
                while i < n:
                    ch = t[i]
                    if ch == '"':
                        if d in (PWSH, CMD) and t[i + 1 : i + 2] == '"':
                            buf.append('"')
                            i += 2
                            continue
                        closed = True
                        i += 1
                        break
                    if d == POSIX and ch == "\\" and i + 1 < n:
                        if t[i + 1] in '$`"\\':
                            buf.append(t[i + 1])
                            i += 2
                            continue
                        if t[i + 1] == "\n":
                            i += 2
                            continue
                    if d == PWSH and ch == "`" and i + 1 < n:
                        buf.append(t[i + 1])
                        i += 2
                        continue
                    if d in (POSIX, PWSH) and (ch == "`" or (ch == "$" and t[i + 1 : i + 2] in ("(", "{"))):
                        self.flag("substitution")
                    buf.append(ch)
                    i += 1
                if not closed:
                    self.flag("unterminated quote")
                continue

            # ---- expansions that run or hide code
            if d in (POSIX, PWSH) and c == "$" and nxt == "(":
                self.flag("substitution")
            if d == PWSH and c == "@" and nxt == "(":
                self.flag("substitution")
            if d == POSIX and c == "$" and nxt in ("'", '"'):
                self.flag("quoted escape")
            if d == POSIX and c == "`":
                self.flag("substitution")
                begin(i)
                buf.append(c)
                i += 1
                continue
            if d == POSIX and c == "$" and nxt == "{":
                self.flag("parameter expansion")
            if d == POSIX and c in "<>" and nxt == "(":
                self.flag("process substitution")
            if d == PWSH and c == "{":
                self.flag("script block")
            if c in "()":
                flush(i)
                self.flag("grouping")
                i += 1
                continue

            # ---- operators and redirections
            if c in "<>" or (d == POSIX and c == "&" and nxt == ">") or (d == PWSH and c == "*" and nxt == ">" and not in_word):
                # an all-digit word right before `>` is a file descriptor
                fd = ""
                if c in "<>" and in_word and not quoted and _FD.match("".join(buf)):
                    fd = "".join(buf)
                    buf, in_word = [], False
                elif d == PWSH and c == "*":
                    i += 1
                    c = ">"
                    fd = "*"
                    nxt = t[i + 1] if i + 1 < n else ""
                else:
                    flush(i)
                if c == "&":  # &> and &>>
                    op = "&>>" if t[i : i + 3] == "&>>" else "&>"
                    i += len(op)
                elif c == "<":
                    if t[i : i + 3] == "<<<":
                        self.flag("here-string")
                        op, i = "<<<", i + 3
                    elif t[i : i + 2] == "<<":
                        self.flag("here-document")
                        op, i = "<<", i + 2
                    elif t[i : i + 2] == "<&":
                        op, i = "<&", i + 2
                    elif t[i : i + 2] == "<>":
                        op, i = "<>", i + 2
                    else:
                        op, i = "<", i + 1
                else:
                    if t[i : i + 2] == ">>":
                        op, i = ">>", i + 2
                    elif t[i : i + 2] == ">&":
                        op, i = ">&", i + 2
                    elif t[i : i + 2] == ">|":
                        op, i = ">|", i + 2
                    else:
                        op, i = ">", i + 1
                self.items.append(_Redir(op, fd))
                continue

            matched = ""
            for op in _OPS[d]:
                if t.startswith(op, i):
                    matched = op
                    break
            if matched:
                flush(i)
                if d == PWSH and matched == "&":
                    pass  # not reachable: "&" is not a PowerShell operator
                self.items.append(matched)
                i += len(matched)
                continue
            if d == PWSH and c == "&":
                # the call operator: `& $x args` runs whatever $x holds
                flush(i)
                self.flag("call operator")
                self.items.append(_Word("&", i, i + 1, False, False))
                i += 1
                continue

            # ---- ordinary character
            begin(i)
            if not buf and not quoted and ((d in (POSIX, PWSH) and c == "$") or (d == CMD and c == "%")):
                var_start = True
            buf.append(c)
            i += 1

        flush(n)


# ---------------------------------------------------------------------------
# Segment analysis
# ---------------------------------------------------------------------------

_DANGEROUS_ENV = re.compile(
    r"^(PATH|LD_[A-Z_]*|DYLD_[A-Z_]*|PYTHONPATH|PYTHONHOME|PYTHONSTARTUP|NODE_OPTIONS|NODE_PATH|BASH_ENV|ENV|"
    r"IFS|SHELLOPTS|BASHOPTS|PS4|PROMPT_COMMAND|PERL5OPT|PERL5LIB|RUBYOPT|RUBYLIB|GIT_[A-Z_]*|PATHEXT|COMSPEC)$",
    re.IGNORECASE,
)
_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")

# F6: any NAME=value ahead of a command can change what the command runs (HOME= or XDG_CONFIG_HOME= point
# git at an attacker's config, JAVA_TOOL_OPTIONS= loads an agent ...), so a command that sets a variable is
# never covered by an allow rule, except for these few that only change how output looks or reads.
_SAFE_ENV = re.compile(r"^(LANG|LC_[A-Z_]+|TZ|NO_COLOR|TERM|CI|FORCE_COLOR)$")
_ENV_BUILTINS = {"export", "declare", "typeset", "readonly", "local", "setenv", "unset", "unsetenv"}
_PS_ENV_ASSIGN = re.compile(r"^\$\{?env:\s*([A-Za-z_]\w*)\}?\s*([-+*/%.]?=)", re.IGNORECASE)
_PS_ENV_WRITERS = {"set-item", "si", "set-content", "sc", "new-item", "ni", "add-content", "ac", "clear-item", "cli",
                   "clear-content", "clc", "remove-item", "ri", "rm", "del", "erase", "rename-item", "rni", "copy-item",
                   "cpi", "move-item", "mi", "out-file", "set-variable", "sv"}
_PS_ENV_PATH = re.compile(r"(^|[\s=,(:'\"-])\$?\{?env:", re.IGNORECASE)


def _safe_env(name: str, dialect: str) -> bool:
    """A variable whose value cannot change what a command runs."""
    return bool(_SAFE_ENV.match(name if dialect == POSIX else name.upper()))

_ELEVATORS = {"sudo", "doas", "runas", "su", "pkexec", "gsudo"}
_POSIX_SHELLS = {"bash", "sh", "zsh", "fish", "dash", "ksh", "ash", "csh", "tcsh"}
_PWSH_NAMES = {"powershell", "pwsh"}
_INLINE_CODE = {  # interpreter -> single-dash letters that mean "run this string"
    "node": set("ep"), "nodejs": set("ep"), "perl": set("eE"), "ruby": set("e"), "osascript": set("e"),
    "php": set("r"), "lua": set("e"), "rscript": set("e"), "deno": set(),
}
_CODE_LAUNCHERS = {"xargs", "parallel", "watch", "eval", "source", ".", "exec-command"}
_LOOSE_CONTROL = {
    "if", "then", "else", "elif", "fi", "while", "until", "do", "done", "for", "case", "esac", "select",
    "function", "{", "}", "!",
}
_DELETE_VERBS = {"rm", "rmdir", "unlink", "shred", "srm", "del", "erase", "rd", "remove-item", "ri"}
_DELETE_SCAN = re.compile(
    r"(?<![\w-])(rm|rmdir|unlink|shred|srm|del|erase|rd|remove-item|ri)(?![\w-])", re.IGNORECASE
)
_EXE_SUFFIXES = (".exe", ".cmd", ".bat", ".ps1")
_PY = re.compile(r"^(python|pypy)[\d.]*$|^py$")
_SEP = re.compile(r"[\\/]")
_WIN_ABS = re.compile(r"^[A-Za-z]:[\\/]")
_VAR_PATH = re.compile(r"^(\$\{?[A-Za-z_]\w*\}?|\$env:\w+|%[^%\s]+%)([\\/]|$)", re.IGNORECASE)


def _base(program: str) -> str:
    """Program name for recognition: no directory, lower-case, no .exe/.cmd/..."""
    name = _SEP.split(program)[-1].lower()
    for suffix in _EXE_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def _has_dir(program: str) -> bool:
    return bool(_SEP.search(program))


def _norm_program(program: str, dialect: str) -> str:
    """How the program token reads inside segment text and rule patterns."""
    out = program
    if dialect in (PWSH, CMD):
        out = out.lower()
    if dialect in (PWSH, CMD) or os.name == "nt":
        low = out.lower()
        for suffix in _EXE_SUFFIXES:
            if low.endswith(suffix) and len(out) > len(suffix):
                out = out[: -len(suffix)]
                break
    return out


def _inline_flag(tokens: list[str], letters: set[str]) -> bool:
    for tok in tokens:
        if tok == "--":
            break
        if tok.startswith("--"):
            if tok.lower() in ("--eval", "--print", "--command", "--exec"):
                return True
            continue
        if tok.startswith("-") and len(tok) > 1 and letters & set(tok[1:]):
            return True
    return False


class _Ctx:
    """Accumulates the results of parsing one command line."""

    def __init__(self, dialect: str, workdir: str | None):
        self.dialect = dialect
        self.workdir = workdir
        self.segments: list[Segment] = []
        self.paths: list[PathRef] = []
        self.opaque = False
        self.reasons: list[str] = []

    def flag(self, why: str) -> None:
        self.opaque = True
        if why not in self.reasons:
            self.reasons.append(why)


def _danger_text(text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern, _why in _policy._DANGER)


def _split(items: list) -> list[list]:
    groups: list[list] = [[]]
    for it in items:
        if isinstance(it, str):
            groups.append([])
        else:
            groups[-1].append(it)
    return [g for g in groups if g]


def _is_path_like(tok: str) -> bool:
    if not tok:
        return False
    if tok.startswith(("/", "\\", "~")) or _WIN_ABS.match(tok) or _VAR_PATH.match(tok):
        return True
    return False


def _glob_in_path_position(tok: str) -> bool:
    """A pattern where a path goes: it holds wildcard / brace / extended-glob characters and reads
    as a path (a separator, a leading ~ or ., a drive or a variable), also after `--option=`."""
    cand = [tok]
    if "=" in tok and not tok.startswith("="):
        cand.append(tok.split("=", 1)[1])
    for c in cand:
        if _pathglob.has_glob(c) and (_is_path_like(c) or _SEP.search(c) or c.startswith(".")):
            return True
    return False


def _collect_paths(words: list[str], ctx: _Ctx) -> None:
    for tok in words:
        cand = [tok]
        if tok.startswith("-") and "=" in tok:
            cand.append(tok.split("=", 1)[1])
        elif _ASSIGN.match(tok):
            cand.append(tok.split("=", 1)[1])
        for c in cand:
            if _is_path_like(c):
                ctx.paths.append(PathRef(c, "read"))
            elif ctx.workdir and c and c not in (".", "..") and not c.startswith("-") and len(c) < 400:
                # a relative name that exists where the command runs (with or without a separator):
                # the rules that block or ask about a path need to see it (F7)
                try:
                    if os.path.exists(os.path.join(ctx.workdir, c)):
                        ctx.paths.append(PathRef(c, "read"))
                except (OSError, ValueError):
                    pass


def _is_null_target(value: str) -> bool:
    return value.lower() in ("/dev/null", "nul", "$null", "/dev/stdout", "/dev/stderr", "/dev/tty")


def _parse_group(group: list, raw: str, ctx: _Ctx, depth: int) -> None:
    """Turn one operator-free group of items into segments."""
    words: list[_Word] = []
    write_redirect = False
    it = iter(range(len(group)))
    idx = 0
    while idx < len(group):
        item = group[idx]
        if isinstance(item, _Redir):
            target = group[idx + 1] if idx + 1 < len(group) and isinstance(group[idx + 1], _Word) else None
            idx += 2 if target else 1
            if target is None:
                ctx.flag("redirect without a target")
                continue
            if item.op in ("<<", "<<<"):
                continue
            value = target.value
            dup = item.op in (">&", "<&") and (value.isdigit() or value == "-")
            if dup:
                continue
            if _pathglob.has_glob(value):
                ctx.flag("pattern in a path")
            if item.op.startswith("<") and item.op != "<>":
                ctx.paths.append(PathRef(value, "read"))
            else:
                if not _is_null_target(value):
                    ctx.paths.append(PathRef(value, "write"))
                    write_redirect = True
            continue
        words.append(item)
        idx += 1
    del it
    if not words:
        if write_redirect:
            ctx.flag("redirect")
        return

    tokens = [w.value for w in words]
    _collect_paths(tokens, ctx)
    _analyse(tokens, words, raw, ctx, depth, write_redirect)


def _analyse(tokens: list[str], words: list[_Word], raw: str, ctx: _Ctx, depth: int, write_redirect: bool) -> None:
    d = ctx.dialect
    opaque = write_redirect
    dangerous = False
    inline = False
    wrappers: list[str] = []
    outer_tokens = list(tokens)
    cur = list(tokens)
    cur_words = list(words)

    def drop(n: int) -> None:
        del cur[:n]
        del cur_words[:n]

    # a. leading NAME=value (posix); a dangerous variable makes the segment opaque
    if d == POSIX:
        while cur and _ASSIGN.match(cur[0]):
            if not _safe_env(_ASSIGN.match(cur[0]).group(1), d):
                opaque = True
            drop(1)
        if not cur and opaque:
            # only assignments (`HOME=/x; git status`): the rest of the line runs under them
            ctx.flag("sets a variable")
            ctx.segments.append(Segment(" ".join(outer_tokens), " ".join(outer_tokens), tuple(outer_tokens), "",
                                        opaque=True))
            return

    # PowerShell: `$env:NAME = value` and the cmdlets that write the env: drive; cmd: `set NAME=value`
    if d == PWSH and cur:
        m = _PS_ENV_ASSIGN.match(" ".join(cur[:2]))
        if m:
            harmless = _safe_env(m.group(1), d) and m.group(2) == "="
            ctx.segments.append(Segment(" ".join(outer_tokens), " ".join(outer_tokens), tuple(outer_tokens),
                                        cur[0], opaque=not harmless, inline=harmless))
            if not harmless:
                ctx.flag("sets a variable")
            return
        low0 = _base(cur[0])
        if (low0 in _PS_ENV_WRITERS and any(_PS_ENV_PATH.search(t) or t.lower().startswith("env:") for t in cur[1:])) \
                or any("::" in t and "environment" in t.lower() for t in cur):
            opaque = True
            ctx.flag("sets a variable")
    if d == CMD and cur and _base(cur[0]) in ("set", "setx"):
        word = _base(cur[0])
        arg = cur[1] if len(cur) > 1 else ""
        if word == "setx" or arg.lower() in ("/a", "/p"):
            opaque = True
            ctx.flag("sets a variable")
        elif "=" in arg:
            name = arg.split("=", 1)[0].strip('"')
            harmless = _safe_env(name, d) and len(cur) == 2
            ctx.segments.append(Segment(" ".join(outer_tokens), " ".join(outer_tokens), tuple(outer_tokens),
                                        cur[0], opaque=not harmless, inline=harmless))
            if not harmless:
                ctx.flag("sets a variable")
            return

    # leading shell keywords (if/then/do/{ ...): the real command follows
    stripped_control = False
    while cur and d == POSIX and cur[0] in _LOOSE_CONTROL:
        stripped_control = True
        drop(1)
        while cur and _ASSIGN.match(cur[0]):
            drop(1)
    if stripped_control:
        opaque = True
    if cur and d == CMD and _base(cur[0]) in ("if", "for", "else", "goto", "call"):
        opaque = True
    if cur and d == PWSH and _base(cur[0]) in ("if", "foreach", "for", "while", "do", "switch", "function", "filter", "try", "trap", "param", "begin", "process", "end"):
        opaque = True

    # b. wrappers
    guard = 0
    while cur and guard < 8:
        guard += 1
        name = _base(cur[0])
        directory = _has_dir(cur[0])
        take = _unwrap(name, cur, d)
        if take is None:
            break
        n, bad = take
        if bad:
            opaque = True
        if directory:
            opaque = True  # a wrapper at an explicit path may not be the real one
        wrappers.append(name)
        drop(n)
        if name == "env":
            while cur and _ASSIGN.match(cur[0]):
                if not _safe_env(_ASSIGN.match(cur[0]).group(1), d):
                    opaque = True
                drop(1)
        if d == PWSH and name == "start-process":
            fp = _start_process(cur)
            if fp is None:
                opaque = True
                cur, cur_words = [], []
            else:
                cur = fp
                cur_words = [_Word(t, 0, 0) for t in fp]
            break
        if d == POSIX:
            while cur and _ASSIGN.match(cur[0]) and name in ("nohup", "time", "nice", "timeout", "command", "exec", "stdbuf", "caffeinate"):
                if not _safe_env(_ASSIGN.match(cur[0]).group(1), d):
                    opaque = True
                drop(1)

    if not cur:
        if wrappers:
            opaque = True
            ctx.segments.append(Segment(" ".join(outer_tokens), " ".join(outer_tokens), tuple(outer_tokens), "", opaque=True, wrappers=tuple(wrappers)))
        return

    program = cur[0]
    base = _base(program)
    directory = _has_dir(program)

    # export / declare / unset ...: they change the variables every later command sees
    if d == POSIX and base in _ENV_BUILTINS:
        calm = len(cur) > 1 and all(_ASSIGN.match(t) and _safe_env(_ASSIGN.match(t).group(1), d) for t in cur[1:])
        if not calm:
            opaque = True
            ctx.flag("sets a variable")

    # variable as the program: `$X args`, `%X% args`
    if words and cur_words and getattr(cur_words[0], "var_start", False):
        opaque = True
        ctx.flag("program is a variable")

    # c. elevation
    if base in _ELEVATORS:
        opaque = dangerous = True
        rest = _after_elevator(base, cur)
        if rest:
            _recurse_tokens(rest, ctx, depth)
        if base == "su":
            c = _flag_arg(cur[1:], "c")
            if c:
                _recurse_text(c, POSIX, ctx, depth)

    # a pattern (wildcard, brace list, extended glob) in a path position: the shell turns it into
    # paths nobody can read from the text, so no allow rule covers the command (F4)
    if any(_glob_in_path_position(t) for t in cur[1:]):
        opaque = True
        ctx.flag("pattern in a path")

    # d. interpreters with inline code, launchers
    nested: list[tuple[str, str]] = []  # (text, dialect)
    if base in _POSIX_SHELLS:
        found, body = _shell_dash_c(cur[1:])
        if found:
            if body is None:
                opaque = True
            else:
                nested.append((body, POSIX))
                inline = True
        elif len(cur) > 1 and not all(t.startswith("-") for t in cur[1:]):
            opaque = True  # runs a script file
        if directory and nested:
            opaque = True
    elif base == "cmd" and d in (CMD, PWSH, POSIX):
        found, body = _cmd_c(cur[1:], words, cur_words, raw)
        if found:
            if body is None:
                opaque = True
            else:
                nested.append((body, CMD))
                inline = True
        elif len(cur) > 1:
            opaque = True
        if directory and nested:
            opaque = True
    elif base in _PWSH_NAMES:
        found, body = _pwsh_command(cur[1:], cur_words[1:], raw)
        if found == "encoded":
            opaque = True
        elif found:
            if body is None:
                opaque = True
            else:
                nested.append((body, PWSH))
                inline = True
        elif any(t.startswith("-") and t.lower().lstrip("-") in ("file", "f") for t in cur[1:]) or (len(cur) > 1 and not all(t.startswith("-") for t in cur[1:])):
            opaque = True
        if directory and nested:
            opaque = True
    elif _PY.match(base):
        if _inline_flag(cur[1:], set("c")):
            opaque = True
    elif base in _INLINE_CODE:
        if _inline_flag(cur[1:], _INLINE_CODE[base]) or base == "deno" and "eval" in cur[1:2]:
            opaque = True
    elif base in _CODE_LAUNCHERS:
        opaque = True
    elif base == "find" and any(t in ("-exec", "-execdir", "-ok", "-okdir") for t in cur[1:]):
        opaque = True

    # pwsh code-execution words anywhere
    if d == PWSH or base in _PWSH_NAMES:
        for t in cur:
            low = t.lower()
            if low in ("iex", "invoke-expression") or low in ("-encodedcommand", "-enc", "-ec", "-e", "-encodedcomman"):
                opaque = True
                dangerous = dangerous or low in ("iex", "invoke-expression")
    elif any(t.lower() in ("iex", "invoke-expression") for t in cur):
        pass

    # f. normalised text
    norm_tokens = [_norm_program(cur[0], d)] + cur[1:]
    outer_norm = [_norm_program(outer_tokens[0], d)] + outer_tokens[1:] if outer_tokens else []
    text = " ".join(norm_tokens)
    outer = " ".join(outer_norm)

    # g. delete verbs
    deletes = base in _DELETE_VERBS or (base == "find" and "-delete" in cur[1:]) or (
        base == "git" and len(cur) > 1 and cur[1] == "clean"
    )
    if opaque and not deletes and _DELETE_SCAN.search(" ".join(cur)):
        deletes = True
    if base in _ELEVATORS and any(_DELETE_SCAN.search(t) for t in cur[1:]):
        deletes = True

    dangerous = dangerous or _danger_text(text) or _danger_text(outer)

    ctx.segments.append(
        Segment(
            text=text, outer=outer, tokens=tuple(norm_tokens), program=_norm_program(program, d),
            opaque=opaque, dangerous=dangerous, deletes=deletes, wrappers=tuple(wrappers), inline=inline,
        )
    )
    if opaque:
        ctx.opaque = True

    for body, nd in nested:
        _recurse_text(body, nd, ctx, depth)


def _after_elevator(name: str, cur: list[str]) -> list[str]:
    """Best effort: the command that follows `sudo -u bob ...`, for block/ask rules."""
    if name in ("su", "runas"):
        return [cur[-1]] if name == "runas" and len(cur) > 1 else []
    takes_value = {"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-R", "-T", "-a"}
    i = 1
    while i < len(cur) and cur[i].startswith("-"):
        i += 2 if cur[i] in takes_value else 1
    return cur[i:]


def _recurse_tokens(tokens: list[str], ctx: _Ctx, depth: int) -> None:
    if not tokens:
        return
    words = [_Word(t, 0, 0) for t in tokens]
    if depth >= MAX_DEPTH:
        ctx.flag("nested too deeply")
        return
    _analyse(list(tokens), words, " ".join(tokens), ctx, depth + 1, False)


def _recurse_text(text: str, dialect: str, ctx: _Ctx, depth: int) -> None:
    if depth >= MAX_DEPTH:
        ctx.flag("nested too deeply")
        return
    sub = _Ctx(dialect, ctx.workdir)
    _scan_into(text, dialect, sub, depth + 1)
    ctx.segments.extend(sub.segments)
    ctx.paths.extend(sub.paths)
    if sub.opaque:
        ctx.opaque = True
        for r in sub.reasons:
            ctx.flag(r)


def _scan_into(text: str, dialect: str, ctx: _Ctx, depth: int) -> None:
    scan = _Scan(text, dialect)
    scan.run()
    if scan.opaque:
        ctx.opaque = True
        for r in scan.reasons:
            ctx.flag(r)
    for group in _split(scan.items):
        _parse_group(group, text, ctx, depth)


# -- wrapper grammar --------------------------------------------------------


def _unwrap(name: str, cur: list[str], d: str) -> tuple[int, bool] | None:
    """If cur[0] is a transparent wrapper, (tokens to drop, unsure). None otherwise."""
    rest = cur[1:]
    if name == "env":
        i, bad = 1, False
        while i < len(cur):
            t = cur[i]
            if t in ("-i", "-0", "--ignore-environment", "-v"):
                i += 1
            elif t in ("-u", "-C", "--unset", "--chdir"):
                i += 2
            elif t == "-S" or t.startswith("--split-string") or (t.startswith("-") and t != "-" and not _ASSIGN.match(t)):
                bad = True
                i += 1
            else:
                break
        return min(i, len(cur)), bad
    if name == "nohup":
        return 1, False
    if name in ("builtin", "command", "exec"):
        if name == "command" and rest[:1] and rest[0].startswith("-"):
            return (2, rest[0] not in ("-p", "--")) if rest else (1, True)
        if name == "exec" and rest[:1] and rest[0].startswith("-"):
            return 2, True
        return 1, False
    if name == "time" and d == POSIX:
        if rest[:1] == ["-p"]:
            return 2, False
        if rest[:1] and rest[0].startswith("-"):
            return 2, True
        return 1, False
    if name == "nice":
        if rest[:1] == ["-n"] and len(rest) >= 2:
            return 3, False
        if rest[:1] and re.match(r"^-\d+$", rest[0]):
            return 2, False
        if rest[:1] and rest[0].startswith("-"):
            return 2, True
        return 1, False
    if name == "timeout":
        i, bad = 1, False
        while i < len(cur) and cur[i].startswith("-"):
            t = cur[i]
            if t in ("-s", "-k", "--signal", "--kill-after"):
                i += 2
            elif t in ("--foreground", "--preserve-status", "-v", "--verbose"):
                i += 1
            else:
                bad = True
                i += 1
        i += 1  # the duration
        return min(i, len(cur)), bad
    if name == "stdbuf":
        i = 1
        bad = False
        while i < len(cur) and cur[i].startswith("-"):
            t = cur[i]
            if t in ("-i", "-o", "-e"):
                i += 2
            elif re.match(r"^-[ioe].+", t) or re.match(r"^--(input|output|error)=", t):
                i += 1
            else:
                bad = True
                i += 1
        return min(i, len(cur)), bad
    if name == "caffeinate":
        i, bad = 1, False
        while i < len(cur) and cur[i].startswith("-"):
            t = cur[i]
            if t in ("-t", "-w"):
                i += 2
            elif re.match(r"^-[dimsu]+$", t):
                i += 1
            else:
                bad = True
                i += 1
        return min(i, len(cur)), bad
    if name == "start" and d == CMD:
        i, bad = 1, False
        flags_plain = {"/b", "/min", "/max", "/wait", "/low", "/normal", "/high", "/realtime", "/abovenormal", "/belownormal", "/i", "/separate", "/shared", "/w"}
        while i < len(cur) and cur[i].startswith("/"):
            low = cur[i].lower()
            if low == "/d":
                i += 2
            elif low in flags_plain:
                i += 1
            else:
                bad = True
                i += 1
        return min(i, len(cur)), bad
    if name == "start-process" and d == PWSH:
        return 1, False
    return None


def _start_process(rest: list[str]) -> list[str] | None:
    """Start-Process -FilePath X -ArgumentList Y  ->  [X, *Y]. None if unsure."""
    file_path: str | None = None
    args: list[str] = []
    positional: list[str] = []
    i = 0
    switches = {"-wait", "-passthru", "-nonewwindow", "-usenewenvironment", "-loaduserprofile", "-nonewwindow"}
    while i < len(rest):
        t = rest[i]
        low = t.lower()
        if low.startswith("-"):
            name = low.rstrip(":")
            if "-filepath".startswith(name) and len(name) >= 3 and "-filepath".startswith(name):
                if i + 1 >= len(rest):
                    return None
                file_path = rest[i + 1]
                i += 2
            elif "-argumentlist".startswith(name) and len(name) >= 3:
                if i + 1 >= len(rest):
                    return None
                val = rest[i + 1]
                if "," in val:
                    return None
                args = val.split()
                i += 2
            elif name in switches:
                i += 1
            else:
                return None  # -Verb RunAs, -WorkingDirectory, -Credential, ...
        else:
            positional.append(t)
            i += 1
    if file_path is None and positional:
        file_path = positional.pop(0)
    if file_path is None:
        return None
    if positional and not args:
        if "," in positional[0]:
            return None
        args = positional[0].split()
    return [file_path, *args]


def _flag_arg(args: list[str], letter: str) -> str | None:
    for i, a in enumerate(args):
        if a == f"-{letter}" and i + 1 < len(args):
            return args[i + 1]
    return None


def _shell_dash_c(args: list[str]) -> tuple[bool, str | None]:
    """For `bash [opts] -c S`: (True, S). (True, None) if -c has no string."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-o", "+o", "-O", "+O"):
            i += 2
            continue
        if a == "--":
            return False, None
        if a.startswith("--"):
            i += 1
            continue
        if a.startswith("-") and len(a) > 1:
            if "c" in a[1:]:
                return True, (args[i + 1] if i + 1 < len(args) else None)
            i += 1
            continue
        return False, None
    return False, None


def _rest_text(words: list[_Word], first: int, raw: str, ctx_raw_ok: bool) -> str | None:
    """The command string after /c or -Command: the original text, quotes peeled if it is one token."""
    rest = words[first:]
    if not rest:
        return None
    if len(rest) == 1:
        return rest[0].value
    if ctx_raw_ok and rest[0].end > rest[0].start and raw:
        return raw[rest[0].start : rest[-1].end]
    return " ".join(w.value for w in rest)


def _cmd_c(args: list[str], words: list[_Word], cur_words: list[_Word], raw: str) -> tuple[bool, str | None]:
    for i, a in enumerate(args):
        if a.lower() in ("/c", "/k", "/r"):
            rest = cur_words[1 + i + 1 :]
            ok = bool(rest) and all(w.end > 0 for w in rest) and raw is not None
            return True, _rest_text(rest, 0, raw, ok)
        if not a.startswith("/"):
            return False, None
    return False, None


def _pwsh_command(args: list[str], arg_words: list[_Word], raw: str) -> tuple[object, str | None]:
    for i, a in enumerate(args):
        low = a.lower()
        if low.startswith("-") and len(low) > 1:
            name = low.lstrip("-").rstrip(":")
            if name in ("e", "ec", "enc", "encodedcommand", "encodedcomman", "en") or ("encodedcommand".startswith(name) and len(name) >= 2):
                return "encoded", None
            if name and "command".startswith(name) and name[0] == "c":
                rest = arg_words[i + 1 :]
                ok = bool(rest) and all(w.end > 0 for w in rest)
                return True, _rest_text(rest, 0, raw, ok)
            if name in ("executionpolicy", "ep", "windowstyle", "w", "workingdirectory", "wd", "configurationname", "config", "inputformat", "outputformat", "version", "psconsolefile"):
                continue
        elif i > 0 and args[i - 1].lower().lstrip("-") in ("executionpolicy", "ep", "windowstyle", "w", "workingdirectory", "wd", "configurationname", "config", "inputformat", "outputformat", "version"):
            continue
        else:
            return False, None
    return False, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse(command: str, dialect: str = POSIX, workdir: str | None = None) -> Parsed:
    """Take a command line apart. Never raises; unreadable input is opaque."""
    if dialect not in DIALECTS:
        dialect = POSIX
    command = command or ""
    ctx = _Ctx(dialect, workdir)
    _scan_into(command, dialect, ctx, 0)
    if not ctx.segments and command.strip():
        ctx.flag("nothing recognisable")
    segments = tuple(ctx.segments)
    paths: list[PathRef] = []
    seen: set[tuple[str, str]] = set()
    for p in ctx.paths:
        key = (p.raw, p.role)
        if key not in seen:
            seen.add(key)
            paths.append(p)
    opaque = ctx.opaque or any(s.opaque for s in segments)
    return Parsed(
        dialect=dialect,
        raw=command,
        segments=segments,
        opaque=opaque,
        paths=tuple(paths),
        deletes=any(s.deletes for s in segments),
        dangerous=any(s.dangerous for s in segments) or _danger_text(command),
        reasons=tuple(ctx.reasons),
    )


def normalise_pattern(text: str, dialect: str = POSIX) -> str:
    """The stored form of a command pattern (the part after any `=` prefix).

    Quotes removed, tokens joined by single spaces, program name folded like a
    segment's. Raises ValueError for text that is more than one plain command.
    """
    scan = _Scan(text or "", dialect)
    scan.run()
    if scan.opaque:
        raise ValueError("a command pattern must be plain text, without substitutions or grouping")
    if any(not isinstance(i, _Word) for i in scan.items):
        raise ValueError("a command pattern must be one command, without operators or redirections")
    tokens = [w.value for w in scan.items]
    if not tokens:
        raise ValueError("a command pattern cannot be empty")
    tokens[0] = _norm_program(tokens[0], dialect)
    return " ".join(tokens)


_SECOND_TOKEN = re.compile(r"^[A-Za-z][\w:.-]*$")


def card_pattern(segment: Segment) -> str:
    """The pattern an approval card proposes for one segment (spec 5.2, last rule)."""
    toks = segment.tokens
    if len(toks) >= 2 and _SECOND_TOKEN.match(toks[1]):
        return f"{toks[0]} {toks[1]}"
    return "=" + segment.text
