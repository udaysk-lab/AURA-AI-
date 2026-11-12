"""Hard floors: what nobody can allow from inside a run (spec 10 section 8, decision 6).

`check(...)` looks at one call's targets and answers one of three things:

* nothing to say (the call may go on to the rules);
* deny, with a code (`FLOOR_SELF_PATH`, `FLOOR_PROTECTED_PATH`, `FLOOR_SELF_API`,
  `FLOOR_DENYLIST`, `FLOOR_PRIVATE_NETWORK`, `FLOOR_AURA_PROCESS` ...);
* "this call needs the user every time" (`always_user`), for a shell command that
  names a protected location.

No rule, mode or earlier approval overrides a deny here. The gate evaluates floors
before any rule (step 3 of the algorithm).

What is NOT here yet (work package 8): the connected-folder fence and its
`folder_access` / `delete_permission` cards, per-app screen grants and the
frontmost-window floors. Until then the existing DEVICE_ALLOWED_ROOTS fence in
`device/files.py` still bounds the file tools.

Residual risk, stated plainly (spec 8.6): a shell command runs unsandboxed as the
user. The text checks below catch direct references only.
"""

from __future__ import annotations

import os
import platform
import re
import sys
from dataclasses import dataclass

from app.config import settings
from app.permissions import match
from app.permissions.catalogue import ToolPolicy

# Sentinel ports/hosts the shell floor looks for next to a network client.
_SELF_NAMES = ("aura.db", "aura.env", "local-login.key", ".aura")
_CLIENT_WORDS = re.compile(
    r"\b(curl|wget|invoke-webrequest|iwr|irm|invoke-restmethod|nc|ncat|netcat|python\d*|node|nodejs|"
    r"ruby|perl|php|telnet)\b|http",
    re.IGNORECASE,
)
_GIT_EXECUTABLE = re.compile(r"(^|[\\/])\.git[\\/](hooks([\\/]|$)|config$)", re.IGNORECASE)
_SPAWNERS = {"sqlite", "sqlite3", "alembic"}


@dataclass(frozen=True)
class FloorResult:
    code: str = ""  # a deny code, or "" for no deny
    reason: str = ""
    always_user: bool = False

    @property
    def deny(self) -> bool:
        return bool(self.code)


OK = FloorResult()


def _deny(code: str, reason: str) -> FloorResult:
    return FloorResult(code=code, reason=reason)


# ---------------------------------------------------------------------------
# AURA's own places
# ---------------------------------------------------------------------------


def _real(path: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


def install_dir() -> str:
    """The installed app folder; from source, the folder holding the `app` package."""
    if getattr(sys, "frozen", False):
        exe = _real(sys.executable)
        probe = exe
        while True:
            parent = os.path.dirname(probe)
            if probe.lower().endswith(".app"):
                return probe
            if parent == probe:
                break
            probe = parent
        return os.path.dirname(exe)
    import app

    return _real(os.path.join(os.path.dirname(os.path.abspath(app.__file__)), os.pardir))


def _sqlite_file(url: str) -> str | None:
    from sqlalchemy.engine import make_url

    try:
        parsed = make_url(url)
    except Exception:
        return None
    if not parsed.drivername.startswith("sqlite") or not parsed.database or parsed.database == ":memory:":
        return None
    return _real(parsed.database)


def self_paths() -> list[str]:
    """AURA's data folder, database, settings, install folder (real paths).

    Computed on each call from the live settings, so a changed setting or a
    different working directory is seen at once. Cheap: a handful of realpaths.
    """
    out: list[str] = []

    def add(path: str) -> None:
        real = _real(path)
        if real not in out:
            out.append(real)

    add(settings.aura_home_path)
    db = _sqlite_file(settings.database_url)
    if db:
        for suffix in ("", "-wal", "-shm", "-journal"):
            add(db + suffix)
    if settings.aura_config_file.strip():
        add(settings.aura_config_file.strip())
    cwd = os.getcwd()
    add(os.path.join(cwd, ".env"))
    add(os.path.join(cwd, os.pardir, ".env"))
    add(install_dir())
    return out


def _self_names() -> set[str]:
    names = {n.casefold() for n in _SELF_NAMES}
    db = _sqlite_file(settings.database_url)
    if db:
        base = os.path.basename(db).casefold()
        names.update({base, base + "-wal", base + "-shm", base + "-journal"})
    if settings.aura_config_file.strip():
        names.add(os.path.basename(settings.aura_config_file.strip()).casefold())
    return names


def _under(folded: str, folded_root: str) -> bool:
    try:
        return os.path.commonpath([folded, folded_root]) == folded_root
    except ValueError:
        return False


def _f(path: str) -> str:
    return match.fold(path, always=True)


def in_self_path(real: str) -> bool:
    f = _f(real)
    return any(_under(f, _f(p)) for p in self_paths())


def in_protected(real: str) -> bool:
    f = _f(real)
    return any(_under(f, _f(p)) for p in settings.device_protected_path_list)


def holds_protected_or_self(real: str) -> bool:
    """A folder with a protected or self path somewhere inside it (moving/deleting it takes them along)."""
    f = _f(real)
    return any(
        _under(_f(p), f) and _f(p) != f for p in [*settings.device_protected_path_list, *self_paths()]
    )


def aura_port() -> str:
    return (os.environ.get("AURA_PORT") or "8765").strip()


# ---------------------------------------------------------------------------
# File tools
# ---------------------------------------------------------------------------


def _file_floor(targets) -> FloorResult:
    for t in targets:
        if t.kind != "path" or t.role == "exec":
            continue
        real = t.value
        if in_self_path(real):
            return _deny("FLOOR_SELF_PATH", "That is part of AURA itself (its data, database, settings or program files). AURA never touches those.")
        if in_protected(real):
            return _deny("FLOOR_PROTECTED_PATH", "That is a protected location (keys, credentials, shell profiles, startup items). AURA won't touch it.")
        if t.role in ("write", "delete") and _GIT_EXECUTABLE.search(real):
            return _deny("FLOOR_PROTECTED_PATH", "That is a git hook or git config: files that make git run commands.")
        if t.role == "delete" and holds_protected_or_self(real):
            return _deny("FLOOR_PROTECTED_PATH", "That folder contains a protected location, and moving or deleting it would take that along.")
    return OK


# ---------------------------------------------------------------------------
# Shell commands
# ---------------------------------------------------------------------------

_HOME_FORMS = re.compile(r"(\$\{home\}|\$home|%userprofile%|%home%|\$env:userprofile|\$env:home)", re.IGNORECASE)


def _prepare_text(raw: str) -> str:
    """Folded, forward-slash text with home forms spelled out."""
    home = os.path.expanduser("~")
    text = raw.replace("\\", "/")
    text = _HOME_FORMS.sub(home.replace("\\", "/"), text)
    text = re.sub(r"(^|[\s='\"(;|&])~(?=/|\s|$)", lambda m: m.group(1) + home.replace("\\", "/"), text)
    return text.casefold()


def _names_pattern(names: set[str]) -> re.Pattern:
    alts = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    # a name as a whole path component (or the tail of one)
    return re.compile(rf"(?:^|[/\s'\"=:;&|<>(,])(?:{alts})(?:$|[/\s'\"=:;&|<>),])")


def _mentions_self(raw: str) -> bool:
    text = _prepare_text(raw)
    for p in self_paths():
        needle = p.replace("\\", "/").casefold().rstrip("/")
        if needle and needle in text:
            return True
    return bool(_names_pattern(_self_names()).search(text))


def _hits_local_api(raw: str) -> bool:
    low = raw.casefold()
    hostname = (platform.node() or "").casefold()
    places = ["localhost", "127.", "[::1]", "0.0.0.0", f":{aura_port()}"]
    near_host = any(p in low for p in places)
    if not near_host and len(hostname) >= 3:
        near_host = re.search(rf"(?<![\w.-]){re.escape(hostname)}(?![\w-])", low) is not None
    if near_host and _CLIENT_WORDS.search(raw):
        return True
    return "/api/" in low and "aura_cli" in low


def _unique(items):
    out = []
    for i in items:
        if i not in out:
            out.append(i)
    return out


_URL = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)


def _path_like(tok: str) -> bool:
    if _URL.match(tok):
        return False
    return bool(tok) and ("/" in tok or "\\" in tok or tok.startswith(("~", ".")) or tok.casefold() in _SELF_NAMES)


def _shell_floor(policy: ToolPolicy, targets) -> tuple[FloorResult, bool]:
    """(floor result, needs the user every time)."""
    cmd = next((t for t in targets if t.kind == "command"), None)
    if cmd is None or cmd.parsed is None:
        return OK, False
    parsed = cmd.parsed
    raw = cmd.raw
    workdir = next((t.value for t in targets if t.kind == "path" and t.role == "exec"), None)
    base = workdir or os.path.expanduser("~")
    # Where a relative path may really point: the working directory when one is given, else
    # wherever the shell starts (home, or the first allowed root when home is outside them).
    bases = [workdir] if workdir else _unique([base, *settings.device_allowed_root_list])

    # 1. AURA's own files, by path or by name anywhere in the text.
    if workdir and in_self_path(workdir):
        return _deny("FLOOR_SELF_PATH", "Commands may not run inside AURA's own folders."), False
    if _mentions_self(raw):
        return _deny("FLOOR_SELF_PATH", "That command refers to AURA's own data, database, settings or program files. AURA never touches those."), False
    protected = bool(workdir and in_protected(workdir))
    for ref in parsed.paths:
        try:
            real = match.normalize_path(ref.raw, base)
        except match.BadArgs:
            continue
        takes_self_along = parsed.deletes and any(
            _f(p) != _f(real) and _under(_f(p), _f(real)) for p in self_paths()
        )
        if in_self_path(real) or takes_self_along:
            return _deny("FLOOR_SELF_PATH", "That command refers to AURA's own data, database, settings or program files. AURA never touches those."), False
        if in_protected(real) or (ref.role == "write" and _GIT_EXECUTABLE.search(real)):
            protected = True

    for seg in parsed.segments:
        for tok in seg.tokens:
            if not _path_like(tok):
                continue
            for b in bases:
                try:
                    real = match.normalize_path(tok, b)
                except match.BadArgs:
                    continue
                if in_self_path(real):
                    return _deny("FLOOR_SELF_PATH", "That command refers to AURA's own data, database, settings or program files. AURA never touches those."), False

    # 2. database tools and migrations.
    for seg in parsed.segments:
        prog = re.sub(r"\.(exe|cmd|bat|ps1)$", "", os.path.basename(seg.program.replace("\\", "/")).casefold())
        if prog == "alembic":
            return _deny("FLOOR_SELF_PATH", "Database migrations are run by AURA itself, never from a command."), False
        if prog in _SPAWNERS and _mentions_self(raw):
            return _deny("FLOOR_SELF_PATH", "AURA's database is off limits to commands."), False

    # 3. AURA's own HTTP API.
    if _hits_local_api(raw):
        return _deny("FLOOR_SELF_API", "Commands may not talk to AURA's own API or to services on this computer's loopback."), False

    # 4. The install's command denylist (unchanged setting).
    from app.device import policy as device_policy

    verdict = device_policy.classify(raw)
    if not verdict.allowed:
        return _deny("FLOOR_DENYLIST", f"This command {verdict.reason}."), False

    always = bool(policy.dyn_always_user and (parsed.deletes or protected))
    return OK, always


# ---------------------------------------------------------------------------
# Fetches and processes
# ---------------------------------------------------------------------------


def _fetch_floor(targets) -> FloorResult:
    from app.device import net

    for t in targets:
        if t.kind != "url":
            continue
        try:
            net.check_url(t.raw)
        except net.FetchDenied as exc:
            if exc.code == "FLOOR_UNRESOLVED":
                continue  # the fetch itself re-checks and fails closed
            return _deny(exc.code, exc.reason)
    return OK


def _process_floor(targets) -> FloorResult:
    for t in targets:
        if t.kind == "pid" and t.value.isdigit() and int(t.value) in {os.getpid(), os.getppid()}:
            return _deny("FLOOR_AURA_PROCESS", "That is AURA's own process.")
    return OK


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def check(db, user, policy: ToolPolicy, targets, ctx=None) -> FloorResult:
    """Step 3 of decide(). `targets` are the call's extracted targets."""
    r = _file_floor(targets)
    if r.deny:
        return r
    shell, always = _shell_floor(policy, targets)
    if shell.deny:
        return shell
    r = _fetch_floor(targets)
    if r.deny:
        return r
    r = _process_floor(targets)
    if r.deny:
        return r
    if always:
        return FloorResult(always_user=True)
    return OK


def env_floor(command_targets) -> FloorResult:
    """For the user's own console: only the install's denylist applies (spec 4.5)."""
    from app.device import policy as device_policy

    for t in command_targets:
        if t.kind == "command":
            verdict = device_policy.classify(t.raw)
            if not verdict.allowed:
                return _deny("FLOOR_DENYLIST", f"This command {verdict.reason}.")
    return OK
