"""Hard floors: what nobody can allow from inside a run (spec 10 section 8, decision 6).

`check(...)` looks at one call's targets and answers one of three things:

* nothing to say (the call may go on to the rules);
* deny, with a code (`FLOOR_SELF_PATH`, `FLOOR_PROTECTED_PATH`, `FLOOR_SELF_API`,
  `FLOOR_DENYLIST`, `FLOOR_PRIVATE_NETWORK`, `FLOOR_AURA_PROCESS` ...);
* "this call needs the user every time" (`always_user`), for a shell command that
  names a protected location.

No rule, mode or earlier approval overrides a deny here. The gate evaluates floors
before any rule (step 3 of the algorithm).

Also here (spec 8.2 and 8.4): the connected-folder fence with its `folder_access` /
`delete_permission` cards, the app floors (AURA's own window, blocked apps) and the screen
floors (presence, front window known, per-app grants and tiers). These need the database,
so `check(None, None, ...)` applies only the hard floors above.

A "card" is not a deny: it is a question only the user can answer. The gate turns it into an
always-user ask after the trigger limits and block rules have had their say. `refuse_code` is what
the call is denied with if it still needs the card when the user approved something else.

Residual risk, stated plainly (spec 8.6): a shell command runs unsandboxed as the
user. The text checks below catch direct references only.
"""

from __future__ import annotations

import os
import platform
import re
import sys
from dataclasses import dataclass, field

from app.config import settings
from app.device import appnames
from app.permissions import access as access_mod
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
    card: str = ""  # folder_access | app_grant | delete_permission
    card_payload: dict = field(default_factory=dict)
    refuse_code: str = ""  # the deny code when the card cannot be asked (or is unanswered at approval)

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


def _shell_floor(policy: ToolPolicy, targets, roots=None) -> tuple[FloorResult, bool]:
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
    bases = [workdir] if workdir else _unique([base, *(settings.device_allowed_root_list if roots is None else roots)])

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
# Connected folders (spec 8.2)
# ---------------------------------------------------------------------------


def _nearest_existing_dir(real: str) -> str:
    """The folder a card would offer to connect: the target if it is a folder, else its closest existing parent."""
    probe = real
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return probe


def _is_fs_root(path: str) -> bool:
    return os.path.dirname(path) == path


def _cards_allowed(ctx) -> bool:
    return ctx is not None and ctx.trigger == "chat"


def _outside(acc, ctx, real: str, role: str) -> FloorResult:
    """A target outside every usable connected folder: a card in chat, a deny anywhere else."""
    folder = _nearest_existing_dir(real)
    if not acc.within_ceiling(folder) or not acc.within_ceiling(real):
        return _deny("FLOOR_OUTSIDE_FOLDERS",
                     "That is outside the limit this install sets for the folders AURA may use "
                     "(DEVICE_ALLOWED_ROOTS in its settings file).")
    if not _cards_allowed(ctx) or not folder or _is_fs_root(folder):
        return _deny("FLOOR_OUTSIDE_FOLDERS",
                     "That is outside the folders connected to AURA. Connect the folder in Settings, This computer.")
    if role == "delete":
        return FloorResult(
            card="delete_permission", refuse_code="FLOOR_NO_DELETE_PERMISSION",
            card_payload={"path": folder, "connect": True},
            reason=f"AURA wants to delete or move files in {folder}, which is not connected yet.",
        )
    return FloorResult(
        card="folder_access", refuse_code="FLOOR_OUTSIDE_FOLDERS", card_payload={"path": folder},
        reason=f"AURA wants to use files in {folder}, which is not connected yet.",
    )


def _fence_floor(acc, policy: ToolPolicy, targets, ctx) -> FloorResult:
    for t in targets:
        if t.kind != "path":
            continue
        real = t.value
        if t.role == "exec":  # a shell's working directory
            if not acc.covers(real):
                return _outside(acc, ctx, real, "read")
            continue
        folder = acc.folder_for(real)
        if folder is None:
            return _outside(acc, ctx, real, t.role)
        if t.role == "delete" and not folder.can_delete:
            if not _cards_allowed(ctx):
                return _deny("FLOOR_NO_DELETE_PERMISSION",
                             f"Deleting is not allowed in {folder.path}. Turn it on in Settings, This computer.")
            return FloorResult(
                card="delete_permission", refuse_code="FLOOR_NO_DELETE_PERMISSION",
                card_payload={"path": folder.path},
                reason=f"AURA wants to delete or move files in {folder.path}, where deleting is not allowed yet.",
            )
    # A shell command with no working directory starts in the first connected folder.
    if any(t.kind == "command" for t in targets) and not any(t.kind == "path" and t.role == "exec" for t in targets):
        if not acc.roots:
            return _deny("FLOOR_NO_FOLDERS",
                         "No folder is connected yet, so there is nowhere to run commands. Give a workdir "
                         "(AURA will ask to connect it) or connect a folder in Settings, This computer.")
    return OK


# ---------------------------------------------------------------------------
# Apps and the screen (spec 8.4)
# ---------------------------------------------------------------------------


def _app_floor(acc, policy: ToolPolicy, targets) -> FloorResult:
    """open_app / close_app / kill_process never touch a blocked app or AURA itself."""
    if policy.kind not in ("app", "process"):
        return OK
    for t in targets:
        if t.kind != "app":
            continue
        if access_mod.is_aura_app(t.value):
            return _deny("FLOOR_AURA_WINDOW", "That is AURA itself.")
        if acc.blocked(t.value):
            return _deny("FLOOR_BLOCKED_APP", "That app is on your blocked list. AURA never opens, closes or uses it.")
    return OK


def _screen_floor(db, user, acc, policy: ToolPolicy, targets, ctx, front) -> FloorResult:
    if policy.kind not in ("screen_read", "screen_input"):
        return OK
    if not access_mod.screen_control_user(db, user):
        return _deny("FLOOR_SCREEN_OFF", "You turned screen control off. Turn it on in Settings, This computer.")
    if access_mod.screen_suspended(user.id):
        return _deny("FLOOR_SCREEN_SUSPENDED",
                     "Screen control is paused for a minute because AURA's window or a blocked app came to the front.")
    if ctx is None or ctx.trigger != "chat" or not ctx.present:
        from app.permissions import triggers

        if ctx is not None and triggers.check(policy, ctx) is not None:
            return OK  # the trigger's own limit (step 4) says it better
        return _deny("NOT_PRESENT", "AURA uses the screen only while the app is open and you are here.")
    if front is None:
        return _deny("FLOOR_FRONTMOST_UNKNOWN",
                     "AURA cannot tell which window is in front on this computer, so it will not use the screen.")
    title = (front.title or "").casefold()
    if (front.pid and (front.pid == os.getpid() or _is_descendant(front.pid))) or "· aura" in title \
            or access_mod.is_aura_app(front.app_id):
        return _deny("FLOOR_AURA_WINDOW", "AURA's own window is in front. AURA never uses the screen on itself.")
    if acc.blocked(front.app_id):
        return _deny("FLOOR_BLOCKED_APP", "The app in front is on your blocked list. AURA never uses it.")
    need = access_mod.need_tier(policy.name)
    if need == "full" and (appnames.is_terminal(front.app_id) or appnames.is_ide(front.app_id)):
        return _deny("FLOOR_APP_TIER",
                     "Terminals and code editors are click and scroll only. Commands go through the shell, where your rules apply.")
    grant = acc.grant(front.app_id)
    if grant is None:
        name = front.name or front.app_id
        return FloorResult(
            card="app_grant", refuse_code="FLOOR_NO_APP_GRANT",
            card_payload={"app_id": front.app_id, "display_name": name, "tier": need},
            reason=f"AURA wants to use {name} on screen ({_TIER_WORDS[need]}).",
        )
    if access_mod.TIER_RANK.get(grant.tier, -1) < access_mod.TIER_RANK[need]:
        return _deny("FLOOR_APP_TIER", f"You allowed {grant.display_name} for {_TIER_WORDS[grant.tier]} only.")
    return OK


_TIER_WORDS = {"view": "look only", "click": "click and scroll", "full": "click, scroll and type"}


def _is_descendant(pid: int) -> bool:
    from app.device import windows

    return windows.is_aura_process(pid)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def check(db, user, policy: ToolPolicy, targets, ctx=None, *, front=None, access=None) -> FloorResult:
    """Step 3 of decide(). `targets` are the call's extracted targets.

    With a database and a user the folder, app and screen floors apply as well; `front` is the
    front window (device/windows.py) the gate read for a screen tool, `access` a snapshot to reuse."""
    live = db is not None and user is not None and policy.system == "device-control"
    acc = (access if access is not None else access_mod.snapshot(db, user)) if live else None
    r = _file_floor(targets)
    if r.deny:
        return r
    shell, always = _shell_floor(policy, targets, None if acc is None else list(acc.roots))
    if shell.deny:
        return shell
    r = _fetch_floor(targets)
    if r.deny:
        return r
    r = _process_floor(targets)
    if r.deny:
        return r
    if live and ctx is not None:
        from app.permissions import triggers

        if triggers.check(policy, ctx) is not None:
            live = False  # the trigger's own limit (step 4) denies it, with the clearer reason
    if live:
        r = _app_floor(acc, policy, targets)
        if r.deny:
            return r
        r = _screen_floor(db, user, acc, policy, targets, ctx, front)
        if r.deny or r.card:
            return r
        r = _fence_floor(acc, policy, targets, ctx)
        if r.deny or r.card:
            return FloorResult(code=r.code, reason=r.reason, card=r.card, card_payload=r.card_payload,
                               refuse_code=r.refuse_code, always_user=always)
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
