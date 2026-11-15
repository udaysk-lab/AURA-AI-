"""A shell command line, taken apart far enough to apply command rules.

This is not a shell and it does not decide what a command touches: reading text can never prove that. It is the
READER that approval cards, block and ask command rules and delete detection need (spec 10 section 5.2, and
spec 43 section 8):

* what are the separate commands in it (segments, with wrappers and `bash -c` bodies unwrapped)?
* does it look like it deletes files, or look dangerous?
* which plain words does it contain (for the literal path words of the text checks)?

Nothing here can make a command run without the owner. While computer protection is off (`wall.active()` is
False) the gate asks the owner about every shell action before it looks at any rule.

Pure functions; no I/O.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from app.device import policy as _policy

POSIX, PWSH, CMD = "posix", "pwsh", "cmd"
DIALECTS = (POSIX, PWSH, CMD)

MAX_DEPTH = 4  # nested `bash -c "bash -c ..."`; deeper than this is read as plain words


def dialect_for_shell_kind(kind: str) -> str:
    """Map `device.runtime.capabilities()["shell"]["kind"]` to a dialect."""
    k = (kind or "").strip().lower()
    if k in ("powershell", "pwsh", "powershell.exe", "pwsh.exe"):
        return PWSH
    if k in ("cmd", "cmd.exe"):
        return CMD
    return POSIX


@dataclass(frozen=True)
class Segment:
    """One simple command.

    `text` is the command after wrappers (env, nohup, timeout ...) are removed, tokens joined by single spaces
    with quotes removed; `outer` is the same before unwrapping. Block and ask rules look at both; allow rules
    at `text`, and only for a segment with no wrapper and no variable assignment in front of it.
    """

    text: str
    outer: str
    tokens: tuple[str, ...]
    program: str
    dangerous: bool = False
    deletes: bool = False
    wrappers: tuple[str, ...] = ()
    assigns: bool = False
    hides: bool = False  # the text runs something its words do not show ($(...), backticks, ${...})
    redirects: bool = False  # it writes to or reads from a file through a redirection
    inline: bool = False  # this segment only launches nested ones (bash -c ...)


@dataclass(frozen=True)
class Parsed:
    dialect: str
    raw: str
    segments: tuple[Segment, ...]
    words: tuple[str, ...] = ()
    deletes: bool = False
    dangerous: bool = False

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


@dataclass
class _Hides:
    """Marks text that runs something the words do not show: `$(...)`, backticks, `${...}`, process substitution."""


@dataclass
class _Redir:
    op: str
    fd: str = ""


_OPS = {
    POSIX: (";", "&&", "||", "|&", "|", "&", "\n"),
    PWSH: (";", "&&", "||", "|", "\n"),
    CMD: ("&&", "||", "&", "|", "\n"),
}
_FD = re.compile(r"^\d+$")
_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")


class _Scan:
    """Splits text into words, redirections and operators. Quotes are removed; nothing is expanded."""

    def __init__(self, text: str, dialect: str):
        self.text = text
        self.d = dialect
        self.items: list = []

    def run(self) -> None:
        t, d = self.text, self.d
        n = len(t)
        i = 0
        buf: list[str] = []
        start = 0
        quoted = False
        in_word = False

        def flush(end: int) -> None:
            nonlocal buf, in_word, quoted
            if in_word:
                self.items.append(_Word("".join(buf), start, end, quoted))
            buf, in_word, quoted = [], False, False

        def begin(at: int) -> None:
            nonlocal in_word, start
            if not in_word:
                in_word = True
                start = at

        while i < n:
            c = t[i]
            nxt = t[i + 1] if i + 1 < n else ""
            if c in " \t\r":
                flush(i)
                i += 1
                continue
            if c == "\n":
                flush(i)
                self.items.append("\n")
                i += 1
                continue
            if (d == POSIX and c == "\\") or (d == PWSH and c == "`") or (d == CMD and c == "^"):
                if nxt == "\n":
                    i += 2
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
            if c == "'" and d != CMD:
                begin(i)
                quoted = True
                i += 1
                while i < n:
                    if t[i] == "'":
                        if d == PWSH and t[i + 1 : i + 2] == "'":
                            buf.append("'")
                            i += 2
                            continue
                        i += 1
                        break
                    buf.append(t[i])
                    i += 1
                continue
            if c == '"':
                begin(i)
                quoted = True
                i += 1
                while i < n:
                    ch = t[i]
                    if ch == '"':
                        if d in (PWSH, CMD) and t[i + 1 : i + 2] == '"':
                            buf.append('"')
                            i += 2
                            continue
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
                    if d != CMD and (ch == "`" or (ch == "$" and t[i + 1 : i + 2] in ("(", "{"))):
                        self.items.append(_Hides())
                    buf.append(ch)
                    i += 1
                continue
            if (d != CMD and c == "$" and nxt in ("(", "{")) or (d == POSIX and c in "<>" and nxt == "(") \
                    or (d == PWSH and c == "@" and nxt == "(") or (d != CMD and c == "`" and d == POSIX):
                self.items.append(_Hides())
            if c in "()":
                flush(i)
                i += 1
                continue
            if c in "<>" or (d == POSIX and c == "&" and nxt == ">"):
                fd = ""
                if c in "<>" and in_word and not quoted and _FD.match("".join(buf)):
                    fd = "".join(buf)
                    buf, in_word = [], False
                else:
                    flush(i)
                if c == "&":
                    op = "&>>" if t[i : i + 3] == "&>>" else "&>"
                    i += len(op)
                elif c == "<":
                    for cand in ("<<<", "<<", "<&", "<>"):
                        if t.startswith(cand, i):
                            op, i = cand, i + len(cand)
                            break
                    else:
                        op, i = "<", i + 1
                else:
                    for cand in (">>", ">&", ">|"):
                        if t.startswith(cand, i):
                            op, i = cand, i + len(cand)
                            break
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
                self.items.append(matched)
                i += len(matched)
                continue
            if d == PWSH and c == "&":
                flush(i)  # the call operator: the command it calls follows
                i += 1
                continue
            begin(i)
            buf.append(c)
            i += 1
        flush(n)


# ---------------------------------------------------------------------------
# Segment analysis
# ---------------------------------------------------------------------------

_ELEVATORS = {"sudo", "doas", "runas", "su", "pkexec", "gsudo"}
_POSIX_SHELLS = {"bash", "sh", "zsh", "fish", "dash", "ksh", "ash", "csh", "tcsh"}
_PWSH_NAMES = {"powershell", "pwsh"}
_LOOSE_CONTROL = {
    "if", "then", "else", "elif", "fi", "while", "until", "do", "done", "for", "case", "esac", "select",
    "function", "{", "}", "!",
}
_DELETE_VERBS = {"rm", "rmdir", "unlink", "shred", "srm", "del", "erase", "rd", "remove-item", "ri"}
_DELETE_SCAN = re.compile(
    r"(?<![\w-])(rm|rmdir|unlink|shred|srm|del|erase|rd|remove-item|ri)(?![\w-])", re.IGNORECASE
)
_HIDES_CODE = re.compile(r"\$\(|`|\$\{|<\(|>\(|\beval\b|\biex\b|invoke-expression", re.IGNORECASE)
# Commands that set variables for the commands after them (`export`, `set`, `$env:X=...`, `Set-Item Env:X`).
_ENV_SETTERS = {"export", "declare", "typeset", "readonly", "local", "setenv", "unset", "unsetenv", "set", "setx"}
_PS_ENV_WRITERS = {"set-item", "si", "set-content", "sc", "new-item", "ni", "add-content", "ac", "clear-item", "cli",
                   "clear-content", "clc", "remove-item", "ri", "rm", "del", "erase", "rename-item", "rni",
                   "copy-item", "cpi", "move-item", "mi", "out-file", "set-variable", "sv"}
_EXE_SUFFIXES = (".exe", ".cmd", ".bat", ".ps1")
_SEP = re.compile(r"[\\/]")


def _base(program: str) -> str:
    """Program name for recognition: no directory, lower-case, no .exe/.cmd/..."""
    name = _SEP.split(program)[-1].lower()
    for suffix in _EXE_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


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


class _Ctx:
    """Accumulates the results of parsing one command line."""

    def __init__(self, dialect: str):
        self.dialect = dialect
        self.segments: list[Segment] = []
        self.words: list[str] = []


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


_NULL_TARGETS = {"/dev/null", "nul", "$null", "/dev/stdout", "/dev/stderr", "/dev/tty"}


def _parse_group(group: list, raw: str, ctx: _Ctx, depth: int) -> None:
    """Turn one operator-free group of items into segments."""
    words: list[_Word] = []
    redirects = False
    hides = any(isinstance(it, _Hides) for it in group)
    group = [it for it in group if not isinstance(it, _Hides)]
    idx = 0
    while idx < len(group):
        item = group[idx]
        if isinstance(item, _Redir):
            target = group[idx + 1] if idx + 1 < len(group) and isinstance(group[idx + 1], _Word) else None
            idx += 2 if target else 1
            if target is None:
                redirects = True
            elif item.op not in ("<<", "<<<"):
                ctx.words.append(target.value)
                dup = item.op in (">&", "<&") and (target.value.isdigit() or target.value == "-")
                if not dup and target.value.lower() not in _NULL_TARGETS:
                    redirects = True
            else:
                redirects = True
            continue
        words.append(item)
        idx += 1
    if not words:
        if redirects or hides:
            ctx.segments.append(Segment("", "", (), "", redirects=redirects, hides=hides))
        return
    tokens = [w.value for w in words]
    ctx.words.extend(tokens)
    before = len(ctx.segments)
    _analyse(tokens, words, raw, ctx, depth, hides)
    if redirects:
        for i in range(before, len(ctx.segments)):
            seg = ctx.segments[i]
            ctx.segments[i] = Segment(**{**seg.__dict__, "redirects": True})


def _analyse(tokens: list[str], words: list[_Word], raw: str, ctx: _Ctx, depth: int, hides: bool = False) -> None:
    d = ctx.dialect
    inline = False
    wrappers: list[str] = []
    outer_tokens = list(tokens)
    cur = list(tokens)
    cur_words = list(words)

    def drop(n: int) -> None:
        del cur[:n]
        del cur_words[:n]

    # leading shell keywords (if/then/do/{ ...): the real command follows
    while cur and d == POSIX and cur[0] in _LOOSE_CONTROL:
        drop(1)

    # wrappers
    guard = 0
    seen_assign = False
    while cur and guard < 8:
        guard += 1
        while wrappers and len(cur) > 1 and _ASSIGN.match(cur[0]):
            drop(1)  # `env X=1 nice cmd`: the assignment belongs to the wrapper chain
            seen_assign = True
        name = _base(cur[0])
        take = _unwrap(name, cur, d)
        if take is None:
            break
        wrappers.append(name)
        drop(take[0])
        if d == PWSH and name == "start-process":
            fp = _start_process(cur)
            if fp is not None:
                cur = fp
                cur_words = [_Word(t, 0, 0) for t in fp]
            break

    if not cur:
        if wrappers:
            ctx.segments.append(Segment(" ".join(outer_tokens), " ".join(outer_tokens), tuple(outer_tokens), "",
                                        wrappers=tuple(wrappers), hides=hides))
        return

    # variable assignments in front of the program change what it runs: the program is the word after them,
    # but the segment keeps them in its text and no allow rule covers it
    lead = 0
    while lead < len(cur) - 1 and (_ASSIGN.match(cur[lead]) or (
            d == PWSH and re.match(r"^\$[\w:]+=", cur[lead]))):
        lead += 1
    assigns = lead > 0 or seen_assign
    eff = cur[lead:]
    program = eff[0]
    base = _base(program)
    if base in _ENV_SETTERS or (d == PWSH and (
            re.match(r"^\$\{?env:", program, re.IGNORECASE)
            or (base in _PS_ENV_WRITERS and any(re.search(r"(^|[\s=,(:'\"-])\$?\{?env:", t, re.IGNORECASE)
                                                or t.lower().startswith("env:") for t in eff[1:]))
            or any("environment]::" in t.lower() or t.lower().startswith("[environment]") for t in eff))):
        assigns = True  # changes the variables every later command sees
    dangerous = False

    # elevation
    if base in _ELEVATORS:
        dangerous = True
        rest = _after_elevator(base, eff)
        if rest and depth < MAX_DEPTH:
            _analyse(list(rest), [_Word(t, 0, 0) for t in rest], " ".join(rest), ctx, depth + 1)
        if base == "su":
            c = _flag_arg(eff[1:], "c")
            if c:
                _recurse_text(c, POSIX, ctx, depth)

    nested: list[tuple[str, str]] = []  # (text, dialect)
    if base in _POSIX_SHELLS:
        found, body = _shell_dash_c(eff[1:])
        if found and body is not None:
            nested.append((body, POSIX))
            inline = True
    elif base == "cmd" and d in (CMD, PWSH, POSIX):
        found, body = _cmd_c(eff[1:], words, cur_words[lead:], raw)
        if found and body is not None:
            nested.append((body, CMD))
            inline = True
    elif base in _PWSH_NAMES:
        found, body = _pwsh_command(eff[1:], cur_words[lead + 1:], raw)
        if found is True and body is not None:
            nested.append((body, PWSH))
            inline = True

    norm_tokens = [_norm_program(eff[0], d)] + eff[1:]
    outer_norm = [_norm_program(outer_tokens[0], d)] + outer_tokens[1:]
    text = " ".join(norm_tokens)
    outer = " ".join(outer_norm)

    deletes = base in _DELETE_VERBS or (base == "find" and "-delete" in eff[1:]) or (
        base == "git" and len(eff) > 1 and eff[1] == "clean"
    )
    if not deletes and (hides or _HIDES_CODE.search(text) or base in _ELEVATORS) and _DELETE_SCAN.search(" ".join(eff)):
        deletes = True  # text that hides what it runs: an rm inside it counts

    dangerous = dangerous or _danger_text(text) or _danger_text(outer)

    ctx.segments.append(
        Segment(
            text=text, outer=outer, tokens=tuple(norm_tokens), program=_norm_program(program, d),
            dangerous=dangerous, deletes=deletes, wrappers=tuple(wrappers), assigns=assigns, hides=hides, inline=inline,
        )
    )
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


def _recurse_text(text: str, dialect: str, ctx: _Ctx, depth: int) -> None:
    if depth >= MAX_DEPTH:
        return
    sub = _Ctx(dialect)
    _scan_into(text, dialect, sub, depth + 1)
    ctx.segments.extend(sub.segments)
    ctx.words.extend(sub.words)


def _scan_into(text: str, dialect: str, ctx: _Ctx, depth: int) -> None:
    scan = _Scan(text, dialect)
    scan.run()
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


def parse(command: str, dialect: str = POSIX) -> Parsed:
    """Take a command line apart. Never raises."""
    if dialect not in DIALECTS:
        dialect = POSIX
    command = command or ""
    ctx = _Ctx(dialect)
    _scan_into(command, dialect, ctx, 0)
    segments = tuple(ctx.segments)
    words = tuple(dict.fromkeys(w for w in ctx.words if w))
    return Parsed(
        dialect=dialect,
        raw=command,
        segments=segments,
        words=words,
        deletes=any(s.deletes for s in segments),
        dangerous=any(s.dangerous for s in segments) or _danger_text(command),
    )


def normalise_pattern(text: str, dialect: str = POSIX) -> str:
    """The stored form of a command pattern (the part after any `=` prefix).

    Quotes removed, tokens joined by single spaces, program name folded like a segment's. Raises ValueError for
    text that is more than one plain command.
    """
    scan = _Scan(text or "", dialect)
    scan.run()
    if any(not isinstance(i, _Word) for i in scan.items):
        raise ValueError("a command pattern must be one command, without operators or redirections")
    tokens = [w.value for w in scan.items]
    if not tokens:
        raise ValueError("a command pattern cannot be empty")
    if re.search(r"\$\(|`|\$\{|<\(|>\(", text or ""):
        raise ValueError("a command pattern must be plain text, without substitutions or grouping")
    tokens[0] = _norm_program(tokens[0], dialect)
    return " ".join(tokens)


def card_pattern(segment: Segment) -> str:
    """The pattern an approval card proposes for one segment: the exact command."""
    return "=" + segment.text
