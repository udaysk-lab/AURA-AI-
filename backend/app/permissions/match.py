"""Rule matching: paths, shell commands, recipients, URLs and app names.

Spec 10-permissions.md section 5. This module only answers "does this rule's
pattern cover this target?" and "what are this call's targets?". It decides
nothing: ordering (block, then ask, then allow) and the default per kind belong
to the gate.

Targets come from `extract(policy, args)`. Rules are anything with `id`,
`effect`, `target_kind` and `pattern` (a `RuleSpec` or a database row).

Safe by construction: every parser failure raises `BadArgs` (the gate denies);
every pattern that cannot be stored raises `PatternError` (the API answers 422).
"""

from __future__ import annotations

import fnmatch
import ipaddress
import os
import re
import sys
from dataclasses import dataclass
from email.utils import getaddresses
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import urlsplit

from app.device import appnames
from app.permissions import shellparse
from app.permissions.catalogue import RULE_KIND_FOR_TARGET, ToolPolicy


class BadArgs(ValueError):
    """A tool argument that names a target could not be read (decide: BAD_ARGS)."""


class PatternError(ValueError):
    """A rule pattern that is not valid for its target kind (API: 422)."""


# ---------------------------------------------------------------------------
# Paths (5.1)
# ---------------------------------------------------------------------------


def _case_insensitive() -> bool:
    """Same test as device/files.py `_CASE_INSENSITIVE`; a function so tests can flip it."""
    return os.name == "nt" or sys.platform == "darwin"


@dataclass(frozen=True)
class PathOps:
    """The path module and case rule to use. `PathOps(ntpath, True)` is Windows on any OS."""

    pm: Any = os.path
    case_insensitive: bool | None = None

    @property
    def ci(self) -> bool:
        return _case_insensitive() if self.case_insensitive is None else self.case_insensitive


_GLOB_CHARS = set("*?[")
_SHELL_HOME = ("$HOME", "${HOME}", "%USERPROFILE%", "%HOME%", "$env:USERPROFILE", "$env:HOME")


def expand(raw: str, ops: PathOps | None = None) -> str:
    """~, $HOME, ${HOME}, %USERPROFILE%, $env:USERPROFILE and other environment variables."""
    ops = ops or PathOps()
    text = raw
    for form in _SHELL_HOME:
        if text.lower().startswith(form.lower()) and (len(text) == len(form) or text[len(form)] in "/\\"):
            text = "~" + text[len(form):]
            break
    return ops.pm.expanduser(ops.pm.expandvars(text))


def fold(path: str, ops: PathOps | None = None, *, always: bool = False) -> str:
    """Comparison form: normcase+casefold on Windows/macOS, and everywhere when `always` (floors)."""
    ops = ops or PathOps()
    if ops.ci or always:
        return ops.pm.normcase(path).casefold()
    return path


def _windows_form(text: str, base: str | None, ops: PathOps) -> str:
    """Windows spellings that name something else than they seem to (second review): UNC administrative shares
    on this computer and `\\\\?\\` forms become drive paths; a drive-relative path on another drive and a
    short name that cannot be resolved here are refused (BadArgs: the call is denied, never allowed)."""
    from app.permissions import pathglob

    local = pathglob.local_unc_to_drive(text)
    if local is not None:
        text = local
    pm = ops.pm
    drive = pm.splitdrive(text)[0]
    if drive and not drive.startswith("\\\\") and not pm.isabs(text):
        bdrive = pm.splitdrive(base or pm.expanduser("~"))[0]
        if drive.casefold() != bdrive.casefold():
            raise BadArgs("a drive-relative path on another drive cannot be read")
    if os.name != "nt" and any(pathglob._SHORT_NAME.search(c) for c in pm.normpath(text).replace("\\", "/").split("/")):
        raise BadArgs("a short (8.3) file name cannot be read")
    return text


def normalize_path(raw: str, base: str | None = None, ops: PathOps | None = None) -> str:
    """Absolute, symlink-resolved, `..`-free path. A non-existent tail is kept lexically."""
    ops = ops or PathOps()
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        raise BadArgs("path is empty or invalid")
    text = expand(raw.strip(), ops)
    if getattr(ops.pm, "__name__", "") == "ntpath":
        text = _windows_form(text, base, ops)
    if not ops.pm.isabs(text):
        text = ops.pm.join(base or ops.pm.expanduser("~"), text)
    return ops.pm.realpath(text)


def _split_pattern(pattern: str, ops: PathOps) -> tuple[str, str, str]:
    """("exact"|"tree"|"glob", directory-or-path, glob)."""
    pm = ops.pm
    if pattern.endswith(("/**", "\\**")):
        return "tree", pattern[:-3] or pm.sep, ""
    parent, name = pm.split(pattern)
    if "**" in pattern:
        return "bad", "", ""
    if _GLOB_CHARS & set(name):
        return "glob", parent, name
    return "exact", pattern, ""


def normalise_path_pattern(raw: str, ops: PathOps | None = None, base: str | None = None) -> str:
    """The stored form of a path pattern. Raises PatternError.

    Forms: an exact path; `<dir>/**`; `<dir>/<glob>` with the glob in the last
    segment only. No `**` anywhere else.
    """
    ops = ops or PathOps()
    pm = ops.pm
    text = (raw or "").strip()
    if not text:
        raise PatternError("a path pattern cannot be empty")
    kind, head, glob = _split_pattern(text, ops)
    if kind == "bad":
        raise PatternError("'**' is only allowed as the last segment (folder/**)")
    try:
        if kind == "tree":
            if _GLOB_CHARS & set(head):
                raise PatternError("wildcards are only allowed in the last segment")
            return pm.join(normalize_path(head, base, ops), "**")
        if kind == "glob":
            if _GLOB_CHARS & set(head):
                raise PatternError("wildcards are only allowed in the last segment")
            return pm.join(normalize_path(head or ".", base, ops), glob)
        if _GLOB_CHARS & set(head):
            raise PatternError("wildcards are only allowed in the last segment")
        return normalize_path(head, base, ops)
    except BadArgs as exc:
        raise PatternError(str(exc)) from exc


def path_matches(pattern: str, target: str, ops: PathOps | None = None) -> bool:
    """Does a stored path pattern cover a normalised target path?"""
    ops = ops or PathOps()
    pm = ops.pm
    kind, head, glob = _split_pattern(pattern, ops)
    t = fold(target, ops)
    if kind == "exact":
        return t == fold(head, ops)
    if kind == "tree":
        d = fold(head, ops)
        if t == d:
            return True
        try:
            return pm.commonpath([t, d]) == d
        except ValueError:  # different drives, or one relative
            return False
    if kind == "glob":
        if pm.dirname(t) != fold(head, ops):
            return False
        return fnmatch.fnmatchcase(pm.basename(t), fold(glob, ops))
    return False


# ---------------------------------------------------------------------------
# Recipients (5.3)
# ---------------------------------------------------------------------------


def _idna(domain: str) -> str:
    try:
        return domain.strip().strip(".").lower().encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"invalid domain {domain!r}") from exc


def normalise_address(addr: str) -> str:
    local, sep, domain = (addr or "").strip().rpartition("@")
    if not sep or not local or not domain or any(c.isspace() for c in addr) or "," in addr:
        raise ValueError(f"not an address: {addr!r}")
    return f"{local.strip().lower()}@{_idna(domain)}"


def parse_recipients(value: Any) -> list[str]:
    """Addresses from "a@x.com, B <b@y.org>" or a list of such strings. Raises BadArgs."""
    if value is None:
        return []
    chunks = [value] if isinstance(value, str) else list(value) if isinstance(value, (list, tuple)) else None
    if chunks is None or any(not isinstance(c, str) for c in chunks):
        raise BadArgs("recipients must be text or a list of text")
    out: list[str] = []
    for c in chunks:
        if not c.strip():
            continue
        pairs = getaddresses([c])
        if not pairs:
            raise BadArgs(f"no address in {c!r}")
        for _name, addr in pairs:
            try:
                out.append(normalise_address(addr))
            except ValueError as exc:
                raise BadArgs(str(exc)) from exc
    return out


def normalise_recipient_pattern(raw: str) -> str:
    text = (raw or "").strip()
    try:
        if text.startswith("@"):
            return "@" + _idna(text[1:]) if text[1:].strip() and "@" not in text[1:] and " " not in text else _bad(text)
        return normalise_address(text)
    except ValueError as exc:
        raise PatternError(str(exc)) from exc


def _bad(text: str):
    raise ValueError(f"invalid recipient pattern {text!r}")


def recipient_matches(pattern: str, address: str) -> bool:
    """`alice@x.com` exact; `@x.com` that exact domain only, never its subdomains."""
    if pattern.startswith("@"):
        return address.rpartition("@")[2] == pattern[1:]
    return address == pattern


# ---------------------------------------------------------------------------
# URLs (5.4)
# ---------------------------------------------------------------------------


def parse_url(url: Any) -> tuple[str, str]:
    """(scheme, host) of an http(s) URL; host lower-case, IDNA, no trailing dot, no port."""
    if not isinstance(url, str) or not url.strip():
        raise BadArgs("url is empty")
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
        _ = parts.port  # raises on a bad port
    except ValueError as exc:
        raise BadArgs(f"unreadable url: {exc}") from exc
    if parts.scheme.lower() not in ("http", "https") or not host:
        raise BadArgs("only http and https urls with a host are allowed")
    try:
        host = _idna(host)
    except ValueError as exc:
        raise BadArgs(str(exc)) from exc
    return parts.scheme.lower(), host


def _is_ip(host: str) -> bool:
    if host and all(c in "0123456789." for c in host):
        return True  # numeric-looking: never suffix-matched, valid address or not
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def normalise_domain_pattern(raw: str) -> str:
    text = (raw or "").strip()
    if "*" in text:
        raise PatternError("wildcards are not needed: 'example.com' already covers its subdomains")
    try:
        if "://" in text:
            scheme, host = parse_url(text)
            return f"{scheme}://{host}"
        if "/" in text or ":" in text and not _is_ip(text):
            raise PatternError("give a host name such as example.com")
        host = _idna(text.strip("[]")) if not _is_ip(text) else text.strip("[]")
    except BadArgs as exc:
        raise PatternError(str(exc)) from exc
    except ValueError as exc:
        raise PatternError(str(exc)) from exc
    if not host:
        raise PatternError("a domain pattern cannot be empty")
    return host


def domain_matches(pattern: str, scheme: str, host: str) -> bool:
    """`example.com` covers example.com and *.example.com; `https://example.com` also needs https."""
    if "://" in pattern:
        want_scheme, _, pattern = pattern.partition("://")
        if scheme != want_scheme:
            return False
    pattern = pattern.strip("[]")
    host = host.strip("[]")
    if _is_ip(pattern) or _is_ip(host):
        return pattern == host
    return host == pattern or host.endswith("." + pattern)


# ---------------------------------------------------------------------------
# Commands (5.2)
# ---------------------------------------------------------------------------


def normalise_command_pattern(raw: str, effect: str, dialect: str = shellparse.POSIX) -> str:
    """`git status` (prefix) | `=git status` (exact) | `~needle` (raw substring; block/ask only)."""
    text = (raw or "").strip()
    if not text:
        raise PatternError("a command pattern cannot be empty")
    if text.startswith("~"):
        if effect == "allow":
            raise PatternError("a '~' substring pattern can only block or ask, never allow")
        if not text[1:].strip():
            raise PatternError("a '~' pattern needs some text")
        return "~" + text[1:].strip()
    prefix = "=" if text.startswith("=") else ""
    try:
        return prefix + shellparse.normalise_pattern(text[len(prefix):], dialect)
    except ValueError as exc:
        raise PatternError(str(exc)) from exc


def _cf(text: str, dialect: str) -> str:
    return text if dialect == shellparse.POSIX else text.casefold()


def segment_matches(pattern: str, segment_text: str, dialect: str = shellparse.POSIX) -> bool:
    """Prefix on a token boundary, or exact with `=`. `~` patterns never match a segment."""
    if pattern.startswith("~"):
        return False
    exact = pattern.startswith("=")
    p = _cf(pattern[1:] if exact else pattern, dialect)
    s = _cf(segment_text, dialect)
    if not p:
        return False
    return s == p if exact else (s == p or s.startswith(p + " "))


def raw_contains(pattern: str, raw: str) -> bool:
    return pattern.startswith("~") and pattern[1:].casefold() in raw.casefold()


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    kind: str  # path | command | recipient | url | app | pid
    value: str  # normalised form (path: realpath; recipient: address; url: host; command: raw text)
    role: str = ""
    raw: str = ""
    scheme: str = ""
    parsed: shellparse.Parsed | None = None
    matchable: bool = True  # False: not what an allow rule must cover (workdir, pid, paths named in a command)
    tree: bool = False  # stands for everything beneath it (a search root): an ask rule anywhere inside it applies
    named: bool = False  # a path a shell command names or runs in: block and ask rules see it, allow rules do not need it
    glob: str = ""  # a pattern word this target stands for (value is its literal folder): it could match more than exists now
    base: str = ""  # where a relative `glob` is read from
    opened: bool = False  # a path a tool opens (open_app): the floors check it like a file argument
    walk: bool = False  # a command walks the whole tree under this folder (grep -r, find, ls -R, tar ...)


def extract(
    policy: ToolPolicy,
    args: dict,
    *,
    dialect: str = shellparse.POSIX,
    home: str | None = None,
    frontmost: str | None = None,
    pid_app: Callable[[int], str | None] | None = None,
    ops: PathOps | None = None,
) -> tuple[Target, ...]:
    """The targets of one call. Raises BadArgs when an argument cannot be read.

    `frontmost` is the canonical id of the front window's app (screen tools);
    `pid_app` maps a pid to a canonical app id. Both come from the device layer.
    """
    if not isinstance(args, dict):
        raise BadArgs("arguments must be an object")
    ops = ops or PathOps()
    base = home or ops.pm.expanduser("~")
    out: list[Target] = []
    for spec in policy.targets:
        value = args.get(spec.arg) if spec.arg else None
        if spec.kind == "path":
            if value in (None, ""):
                if spec.default is not None:
                    value = spec.default
                elif spec.role == "exec":
                    continue
                else:
                    raise BadArgs(f"{spec.arg} is required")
            if not isinstance(value, str):
                raise BadArgs(f"{spec.arg} must be text")
            workdir = None
            out.append(Target("path", normalize_path(value, base, ops), spec.role, value, matchable=spec.role != "exec",
                              named=spec.role == "exec", tree=policy.name == "search_files"))
        elif spec.kind == "command":
            if not isinstance(value, str) or not value.strip():
                raise BadArgs(f"{spec.arg} is required")
            wd = args.get("workdir")
            wd_real = None
            if isinstance(wd, str) and wd.strip():
                try:
                    wd_real = normalize_path(wd, base, ops)
                except BadArgs:
                    wd_real = None
            parsed = shellparse.parse(value, dialect, wd_real)
            out.append(Target("command", value, "", value, parsed=parsed))
            out.extend(named_paths(parsed, wd_real or base, base, ops))
        elif spec.kind == "recipient":
            addrs = parse_recipients(value)
            if not addrs and policy.name == "send_email":
                raise BadArgs("at least one recipient is required")
            out.extend(Target("recipient", a, "", a) for a in addrs)
        elif spec.kind == "url":
            scheme, host = parse_url(value)
            out.append(Target("url", host, "", value, scheme=scheme))
        elif spec.kind == "app":
            if spec.role == "frontmost":
                if frontmost:
                    out.append(Target("app", appnames.canonical(frontmost), "frontmost", frontmost))
                continue
            if not isinstance(value, str) or not value.strip():
                raise BadArgs(f"{spec.arg} is required")
            if policy.name == "open_app" and value.strip().lower().startswith(("http://", "https://")):
                # F14: a URL given to open_app is a URL target: the fetch floors, domain rules and
                # the reviewer's URL pre-blocks all see it, exactly as for fetch_url.
                scheme, host = parse_url(value.strip())
                out.append(Target("url", host, "", value.strip(), scheme=scheme))
                continue
            if policy.name == "open_app":
                opened = _opened_path(value.strip(), base, ops)
                if opened is not None:
                    # NF3: a file or folder given to open_app is a path the floors and path rules must see.
                    out.append(Target("path", opened, "read", value, matchable=False, named=True, opened=True))
                    continue
            app_id = appnames.canonical(value)
            if not app_id:
                raise BadArgs("not an application name")
            out.append(Target("app", app_id, "", value))
        elif spec.kind == "pid":
            if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip().isdigit():
                raise BadArgs("pid must be a number")
            pid = int(value)
            out.append(Target("pid", str(pid), "", str(pid), matchable=False))
            app_id = pid_app(pid) if pid_app else None
            if app_id:
                out.append(Target("app", appnames.canonical(app_id), "pid", app_id))
    return tuple(out)


def _opened_path(value: str, base: str, ops: PathOps) -> str | None:
    """The real path open_app would open, when its argument is, or resolves to, a file or folder."""
    pm = ops.pm
    shaped = "/" in value or "\\" in value or value.startswith("~") or bool(pm.splitdrive(value)[0]) \
        or value.startswith("$") or value.startswith("%")
    if not shaped:
        # a bare name that exists where AURA runs is opened as that file (device/apps.py)
        try:
            if not os.path.exists(pm.expanduser(value)):
                return None
        except (OSError, ValueError):
            return None
        return normalize_path(value, os.getcwd(), ops)
    try:
        return normalize_path(value, base, ops)
    except BadArgs:
        raise


def _literal_head(word: str) -> str:
    """The leading components of a path word that hold no wildcard (`fin*/x` -> `.`, `~/work/f*/x` -> `~/work`)."""
    from app.permissions import pathglob

    text = word.replace("\\", "/")
    comps: list[str] = []
    for comp in text.split("/"):
        if pathglob.has_glob(comp):
            break
        comps.append(comp)
    head = "/".join(comps)
    if not head:
        return "/" if text.startswith("/") else "."
    return head


def _anchor_of(pattern: str, ops: PathOps) -> str:
    kind, head, _glob = _split_pattern(pattern, ops)
    if kind in ("tree", "exact"):
        return head
    if kind == "glob":
        return head
    return pattern



# ---------------------------------------------------------------------------
# Commands that walk a whole tree (NF4)
# ---------------------------------------------------------------------------

_WALK_ALWAYS = {"find", "rg", "ag", "ack", "fd", "fdfind", "tree", "du", "locate", "plocate", "tar", "bsdtar",
                "robocopy", "7z", "7za", "7zr", "compress-archive"}
_WALK_GREPS = {"grep", "egrep", "fgrep", "zgrep", "rgrep"}
_WALK_LISTERS = {"get-childitem", "gci", "ls", "dir", "select-string", "sls", "copy-item", "cpi", "get-item"}
_WALK_COPIERS = {"cp", "rsync", "scp", "zip", "diff", "xcopy", "mv", "chmod", "chown", "chgrp"}
_RECURSE_SHORT = re.compile(r"^-[A-Za-z]*[rRa][A-Za-z]*$")
_RECURSE_UPPER = re.compile(r"^-[A-Za-z]*[rR][A-Za-z]*$")


def _prog_name(program: str) -> str:
    name = re.split(r"[\\/]", program)[-1].casefold()
    return re.sub(r"\.(exe|cmd|bat|ps1)$", "", name)


def _walks(prog: str, tokens: Sequence[str]) -> bool:
    low = [t.casefold() for t in tokens]
    if prog in _WALK_ALWAYS:
        return True
    if prog in _WALK_GREPS:
        return any(_RECURSE_UPPER.match(t) or t in ("--recursive", "--dereference-recursive") for t in tokens) \
            or any(t.startswith("--directories=recurse") for t in low)
    if prog in _WALK_LISTERS:
        return any(t.startswith("-rec") or t == "-r" or t == "/s" or t == "--recursive"
                   or (prog == "ls" and _RECURSE_UPPER.match(tokens[i]) and "R" in tokens[i])
                   for i, t in enumerate(low))
    if prog in _WALK_COPIERS:
        if prog == "xcopy":
            return any(t in ("/s", "/e") for t in low)
        if prog == "zip":
            return any(_RECURSE_UPPER.match(t) for t in tokens)
        if prog in ("mv", "chmod", "chown", "chgrp"):
            return any(t == "-R" or t == "--recursive" for t in tokens)
        return any(_RECURSE_SHORT.match(t) or t == "--recursive" for t in tokens)
    return False


def walk_roots(parsed: shellparse.Parsed, base_dir: str, ops: PathOps | None = None) -> list[str]:
    """The folders a command walks through whole (grep -r, find, ls -R, rg, tree, tar, zip -r, cp -r, Get-ChildItem
    -Recurse, dir /s, robocopy, xcopy /s ...): its folder arguments, or the working directory when it names none."""
    from app.permissions import pathglob

    ops = ops or PathOps()
    windows = ops.pm is not os.path or os.name == "nt" or parsed.dialect != shellparse.POSIX
    out: list[str] = []
    for seg in parsed.segments:
        prog = _prog_name(seg.program)
        args = list(seg.tokens[1:])
        if not _walks(prog, args):
            continue
        dirs: list[str] = []
        from_globs: list[str] = []
        files_named = False
        for tok in args:
            if prog in ("find", "fd", "fdfind") and tok.startswith(("-", "(", "!")) and prog == "find":
                break  # find: the folders come first, then the tests (`-name '*.txt'` is not a folder)
            if tok.startswith("-") or (windows and tok.startswith("/") and len(tok) <= 3 and tok[1:2].isalpha()):
                continue
            word = tok
            glob = pathglob.has_glob(word)
            if glob:
                word = _literal_head(word)
            try:
                real = normalize_path(word, base_dir, ops)
            except BadArgs:
                continue
            if os.path.isdir(real):
                (from_globs if glob else dirs).append(real)
            elif os.path.exists(real) and not glob:
                files_named = True
        if dirs:
            out.extend(dirs)
        elif from_globs:
            out.extend(from_globs)
        elif not files_named:
            out.append(base_dir)
    return list(dict.fromkeys(out))


def named_paths(parsed: shellparse.Parsed, base_dir: str, home: str, ops: PathOps | None = None) -> list[Target]:
    """The paths a shell command names, as targets for path block and ask rules (F7).

    Every path the parser found, with wildcard and brace words replaced by what they expand to now,
    each resolved the way a path argument is (relative paths from the working directory). They
    are never something an allow rule has to cover: only a rule that blocks or asks can match them.
    """
    from app.permissions import pathglob

    ops = ops or PathOps()
    windows = ops.pm is not os.path or os.name == "nt" or parsed.dialect != shellparse.POSIX
    ci = ops.ci or windows
    dot_ok = windows or pathglob.dotglob_on(parsed.raw)
    out: list[Target] = []
    seen: set[tuple[str, str]] = set()

    def add(raw: str, value: str, role: str) -> None:
        key = (fold(value, ops, always=True), role)
        if key not in seen and len(out) < 200:
            seen.add(key)
            out.append(Target("path", value, role, raw, matchable=False, named=True))

    for ref in parsed.paths:
        raw = ref.raw
        if "\x00" in raw or not raw.strip():
            continue
        if parsed.dialect != shellparse.POSIX and os.name != "nt" and ops.pm is os.path:
            raw = raw.replace("\\", "/")  # PowerShell and cmd paths use both separators; this OS only knows one
        if windows:
            # Windows ignores trailing dots and spaces in a name (`finance.` is `finance`)
            raw = re.sub(r"[^\\/]+", lambda m: pathglob._clean(m.group(0), True), raw)
        if pathglob.has_glob(raw):
            for found in pathglob.existing_matches(raw, base_dir, home, ci=ci, windows=windows, dot_ok=dot_ok, limit=100):
                add(raw, found, ref.role)
            # ... and what it could expand to: the literal folder before the first wildcard stands for the word
            try:
                head = _literal_head(raw)
                folder = normalize_path(head, base_dir, ops)
            except BadArgs:
                continue
            key = (fold(folder, ops, always=True) + "\x00" + raw, ref.role)
            if key not in seen and len(out) < 200:
                seen.add(key)
                out.append(Target("path", folder, ref.role, raw, matchable=False, named=True, glob=raw, base=base_dir))
            continue
        try:
            add(raw, normalize_path(raw, base_dir, ops), ref.role)
        except BadArgs:
            continue
    for root in walk_roots(parsed, base_dir, ops):
        key = (fold(root, ops, always=True) + "\x00walk", "read")
        if key not in seen and len(out) < 260:
            seen.add(key)
            out.append(Target("path", root, "read", root, matchable=False, named=True, tree=False, walk=True))
    return out


# ---------------------------------------------------------------------------
# Applying rules to targets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleSpec:
    """The matching-relevant part of a rule. Database rows work too (same attribute names)."""

    id: str
    effect: str
    target_kind: str = ""
    pattern: str = ""


def pattern_covers(rule_kind: str, pattern: str, target: Target, dialect: str, ops: PathOps | None = None) -> bool:
    """One non-command pattern against one target of the right kind."""
    if rule_kind == "path":
        return path_matches(pattern, target.value, ops)
    if rule_kind == "recipient":
        return recipient_matches(pattern, target.value)
    if rule_kind == "domain":
        return domain_matches(pattern, target.scheme, target.value)
    if rule_kind == "app":
        return target.value == pattern
    return False


def _targets_of(rule_kind: str, targets: Sequence[Target]) -> list[Target]:
    return [t for t in targets if (t.matchable or t.named) and RULE_KIND_FOR_TARGET.get(t.kind) == rule_kind]


def rule_applies_to(rule: Any, targets: Sequence[Target]) -> bool:
    """False when the rule limits itself to a target kind this call does not have (skipped)."""
    if not rule.target_kind:
        return not rule.pattern  # a pattern without a kind is invalid; never matches
    return bool(_targets_of(rule.target_kind, targets))


def path_pattern_inside(pattern: str, folder: str, ops: PathOps | None = None) -> bool:
    """Does the tree a stored path pattern names lie inside `folder` (or is it that folder)?"""
    ops = ops or PathOps()
    kind, head, _glob = _split_pattern(pattern, ops)
    if kind == "bad":
        return False
    anchor = head if kind in ("tree", "exact") else ops.pm.dirname(pattern)
    a, f = fold(anchor, ops), fold(folder, ops)
    try:
        return ops.pm.commonpath([a, f]) == f
    except ValueError:
        return False


def _glob_could_name(pattern: str, t: Target, ops: PathOps | None) -> bool:
    """Could the wildcard word behind `t` expand to the tree (or file) a path rule names?"""
    from app.permissions import pathglob

    ops = ops or PathOps()
    anchor = _anchor_of(pattern, ops)
    if not anchor:
        return False
    windows = ops.pm is not os.path or os.name == "nt"
    try:
        return pathglob.could_match(t.glob, anchor, t.base or t.value, ops.pm.expanduser("~"),
                                    ci=ops.ci or windows, windows=windows, dot_ok=True)
    except Exception:
        return True


def rule_matches_any(rule: Any, targets: Sequence[Target], dialect: str = shellparse.POSIX, ops: PathOps | None = None,
                     descend: bool = False) -> bool:
    """For block and ask rules: does the rule match any target? A tool-wide rule always does.

    `descend` (ask rules): a search root also matches a path rule whose tree lies anywhere inside it."""
    if not rule.target_kind:
        return not rule.pattern
    mine = _targets_of(rule.target_kind, targets)
    if not mine:
        return False
    if not rule.pattern:
        return True
    for t in mine:
        if rule.target_kind == "command":
            p = t.parsed
            if p is None:
                continue
            if raw_contains(rule.pattern, p.raw):
                return True
            if any(segment_matches(rule.pattern, s.text, dialect) or segment_matches(rule.pattern, s.outer, dialect)
                   for s in p.segments):
                return True
        elif pattern_covers(rule.target_kind, rule.pattern, t, dialect, ops):
            return True
        elif t.glob and rule.target_kind == "path" and _glob_could_name(rule.pattern, t, ops):
            return True
        elif t.walk and rule.target_kind == "path" and path_pattern_inside(rule.pattern, t.value, ops):
            return True
        elif descend and t.tree and rule.target_kind == "path" and path_pattern_inside(rule.pattern, t.value, ops):
            return True
    return False


def first_match(
    effect: str, rules: Iterable[Any], targets: Sequence[Target],
    dialect: str = shellparse.POSIX, ops: PathOps | None = None,
) -> Any | None:
    """First block/ask rule (in the order given) that matches this call."""
    for rule in rules:
        if rule.effect == effect and rule_matches_any(rule, targets, dialect, ops, descend=effect == "ask"):
            return rule
    return None


def _command_covered(parsed: shellparse.Parsed, rules: Sequence[Any], dialect: str) -> list[Any] | None:
    """Allow semantics for a command: every segment covered, nothing opaque or dangerous."""
    for r in rules:
        if not r.pattern:  # "any command" rule, as tool-wide
            return [r]
    if parsed.opaque or parsed.dangerous or not parsed.segments:
        return None
    used: list[Any] = []
    for seg in parsed.matchable_segments:
        if seg.opaque or seg.dangerous or seg.deletes:
            return None
        hit = next((r for r in rules if not r.pattern.startswith("~") and segment_matches(r.pattern, seg.text, dialect)), None)
        if hit is None:
            return None
        if hit not in used:
            used.append(hit)
    return used or None


def allow_cover(
    rules: Iterable[Any], targets: Sequence[Target],
    dialect: str = shellparse.POSIX, ops: PathOps | None = None,
) -> tuple[Any, ...] | None:
    """The allow rules that together cover the call, or None.

    A tool-wide rule covers everything. Otherwise every rule-matchable target
    must be covered: all recipients, both paths of a move, every command segment.
    Working directories and pids are not rule targets; the floors check them.
    """
    allow = [r for r in rules if r.effect == "allow"]
    for r in allow:
        if not r.target_kind and not r.pattern:
            return (r,)
    matchable = [t for t in targets if t.matchable]
    if not matchable:
        return None
    used: list[Any] = []

    def note(rs: Iterable[Any]) -> None:
        for r in rs:
            if r not in used:
                used.append(r)

    for t in matchable:
        kind = RULE_KIND_FOR_TARGET.get(t.kind)
        mine = [r for r in allow if r.target_kind == kind]
        if not mine:
            return None
        if t.kind == "command":
            if t.parsed is None:
                return None
            cover = _command_covered(t.parsed, mine, dialect)
            if cover is None:
                return None
            note(cover)
            continue
        hit = next((r for r in mine if not r.pattern or pattern_covers(kind, r.pattern, t, dialect, ops)), None)
        if hit is None:
            return None
        note([hit])
    return tuple(used)


# ---------------------------------------------------------------------------
# Rule creation helpers
# ---------------------------------------------------------------------------


def normalise_pattern(target_kind: str, pattern: str, effect: str, dialect: str = shellparse.POSIX,
                      ops: PathOps | None = None) -> str:
    """Validate and normalise a rule pattern for storage. Raises PatternError."""
    if not target_kind:
        if pattern:
            raise PatternError("a pattern needs a target kind")
        return ""
    if not (pattern or "").strip():
        return ""
    if target_kind == "path":
        return normalise_path_pattern(pattern, ops)
    if target_kind == "command":
        return normalise_command_pattern(pattern, effect, dialect)
    if target_kind == "recipient":
        return normalise_recipient_pattern(pattern)
    if target_kind == "domain":
        return normalise_domain_pattern(pattern)
    if target_kind == "app":
        app_id = appnames.canonical(pattern)
        if not app_id:
            raise PatternError("not an application name")
        return app_id
    raise PatternError(f"unknown target kind {target_kind!r}")


def command_card_patterns(parsed: shellparse.Parsed) -> list[str]:
    """One proposed pattern per uncovered segment; empty when only once/reject may be offered."""
    if not parsed.offers_durations:
        return []
    out: list[str] = []
    for seg in parsed.matchable_segments:
        pat = shellparse.card_pattern(seg)
        if pat not in out:
            out.append(pat)
    return out
