"""Shared FastAPI dependencies."""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import PairedDevice, User, UserSettings
from app.security import decode_access_token

DbSession = Annotated[Session, Depends(get_db)]

log = logging.getLogger("aura.deps")

# How often a paired device's "last seen" is written. Every request would be a
# database write per click for a timestamp nobody needs to the second.
_SEEN_EVERY = timedelta(minutes=5)


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@lru_cache(maxsize=1)
def local_os_user() -> str:
    """Who this process runs as — on a local install, whose computer it is."""
    import getpass

    try:
        return getpass.getuser() or "owner"
    except Exception:
        return "owner"


def local_owner_email() -> str:
    """The one account a local install has: its OS user's, at localhost."""
    return f"{local_os_user().lower().replace(' ', '-')}@localhost"


def is_local_owner(user: User) -> bool:
    return (user.email or "").lower() == local_owner_email()


def _paired_device(db: Session, request: Request, payload: dict) -> PairedDevice:
    """The device a remote token was issued to — still paired, still owned."""
    if not settings.remote_access_enabled:
        raise _unauthorized("Remote access is turned off on this computer.")
    device = db.get(PairedDevice, str(payload.get("did") or ""))
    if not device or device.revoked_at is not None or device.user_id != payload.get("sub"):
        raise _unauthorized("This device has been unpaired. Pair it again to continue.")

    now = datetime.now(timezone.utc)
    seen = _aware(device.last_seen_at)
    if seen is None or now - seen > _SEEN_EVERY:
        device.last_seen_at = now
        device.last_address = (request.client.host if request.client else "")[:64]
        db.commit()
    return device


def get_current_user(
    request: Request,
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    token = _bearer(authorization)
    if not token:
        raise _unauthorized("Missing bearer token")
    payload = decode_access_token(token)
    if not payload or not payload.get("sub"):
        raise _unauthorized("Invalid or expired token")

    request.state.remote_device_id = None
    request.state.token_claims = payload
    if payload.get("typ") == "remote":
        device = _paired_device(db, request, payload)
        request.state.remote_device_id = device.id
    elif payload.get("typ"):
        # OAuth states, password-reset links and the like are signed with the
        # same key but are not sessions.
        raise _unauthorized("Invalid or expired token")
    elif settings.local_mode:
        # Belt and braces behind LocalAccessMiddleware: on a local install, a
        # session token is only good from this computer itself. From the
        # network it takes a paired device's token.
        from app.middleware import is_loopback

        if not is_loopback(request.client.host if request.client else ""):
            raise _unauthorized("Pair this device to use AURA from it.")
        if payload.get("amr") != "local-key":
            # Every way into a local install carries the sign-in key. A session made some other
            # way (an older one, made before the key was always required) is not honoured.
            raise _unauthorized("Sign in again from AURA's own link (it carries the sign-in key).")

    user = db.get(User, payload["sub"])
    if not user or not user.is_active:
        raise _unauthorized("User not found")
    if settings.local_mode and not is_local_owner(user):
        # A local install belongs to the person whose computer it is, and
        # their account is the only one it serves. Every other way to make an
        # account is closed there, but one that exists anyway — from before,
        # or made by another program or user on this computer — must not open
        # the shell, the settings or pairing.
        raise _unauthorized(
            "This is AURA on someone's own computer: sign in with “Open AURA on this computer”."
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def is_remote_session(request: Request) -> bool:
    return bool(getattr(request.state, "remote_device_id", None))


def require_local_owner(request: Request, user: CurrentUser) -> User:
    """For settings that change what AURA may do on this computer.

    Only a local install, only from this computer itself, and never from a
    paired device: a phone can use AURA, but it can't widen AURA's limits or
    pair further devices.
    """
    from app.middleware import is_loopback

    if not settings.local_mode:
        raise HTTPException(status_code=404, detail="Only available when AURA runs on your own computer.")
    peer = request.client.host if request.client else ""
    if is_remote_session(request) or not is_loopback(peer):
        raise HTTPException(
            status_code=403,
            detail="This can only be changed on the computer AURA runs on.",
        )
    return user


LocalOwner = Annotated[User, Depends(require_local_owner)]


def get_user_settings(db: Session, user: User) -> UserSettings:
    if user.settings:
        return user.settings
    row = UserSettings(user_id=user.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# How the session was opened (the `amr` claim) and the local sign-in key
# ---------------------------------------------------------------------------

LOCAL_KEY_FILE = "local-login.key"
_generated_local_key = ""
_generated_for = ""  # the key file path _generated_local_key was read from or written to


def generated_local_key() -> str:
    """The sign-in key kept in AURA's home ("" when none is needed or one is configured)."""
    return _generated_local_key


def local_key_path() -> str:
    return os.path.join(settings.aura_home_path, LOCAL_KEY_FILE)


def _tighten(path: str) -> None:
    """Owner-only permissions where the system has them (a Windows profile folder already is)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_key_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            key = fh.read().strip()
    except OSError:
        return ""
    # A key of any other shape (empty, hand-edited, cut short) is replaced, never trusted.
    return key if len(key) >= 32 and key.isascii() and key.isprintable() and " " not in key else ""


def _write_key_file(path: str, key: str) -> bool:
    """Write the key so that no other user can ever read it: created 0600, then moved into place."""
    tmp = f"{path}.{secrets.token_hex(4)}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(key)
        _tighten(tmp)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        log.warning("Could not write the local sign-in key file (%s)", exc)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def ensure_local_key() -> str:
    """A local install always has a sign-in key; there is no way in without one.

    A configured key (AURA_LOCAL_LOGIN_KEY in the settings file, which is what the desktop app
    makes on first start) is used as it is. Otherwise the key lives in `<aura_home>/local-login.key`
    (owner-only): made on first start, read again on later ones, so a restart does not sign the
    browser out. Signing in needs it (`amr = local-key`).

    The key itself is never written to the log. A person starting AURA from a terminal is shown the
    sign-in link there (an interactive terminal only); otherwise the file is where to read it."""
    global _generated_local_key, _generated_for
    if not settings.local_mode or settings.aura_local_login_key.strip():
        return ""
    path = local_key_path()
    key = _read_key_file(path)
    if key:
        _tighten(path)
    else:
        key = secrets.token_urlsafe(32)
        if not _write_key_file(path, key):
            path = ""  # kept in memory for this run only
    _generated_local_key, _generated_for = key, path
    log.info("The local sign-in key is %s.", f"kept in {path}" if path else "kept in memory only")
    port = os.environ.get("AURA_PORT") or "8765"
    try:
        if sys.stderr and sys.stderr.isatty():
            print(f"Open http://127.0.0.1:{port}/login#key={key}", file=sys.stderr)
    except Exception:
        pass
    return key


def effective_local_key() -> str:
    """The key a sign-in must present: the configured one, else the one in AURA's home.

    Made on demand if startup has not (so a local install is never without one)."""
    configured = settings.aura_local_login_key.strip()
    if configured:
        return configured
    if not settings.local_mode:
        return ""
    if not _generated_local_key or _generated_for != local_key_path():
        ensure_local_key()
    return _generated_local_key


def check_local_key(offered: str) -> bool:
    expected = effective_local_key()
    return bool(expected and offered and hmac.compare_digest(offered.encode(), expected.encode()))


def token_amr(request: Request) -> str:
    """How this request's session was opened: local-key, password, oauth, demo or remote.

    A token without the claim has none; on a local install `get_current_user` refuses anything
    but `local-key` and a paired device's token."""
    if is_remote_session(request):
        return "remote"
    claims = getattr(request.state, "token_claims", None) or {}
    return str(claims.get("amr") or "")


# ---------------------------------------------------------------------------
# Presence and self-change guards (spec 8.5)
# ---------------------------------------------------------------------------


def _load_psutil():
    try:
        import psutil  # type: ignore

        return psutil
    except ImportError:
        return None


def _instance_id() -> str:
    from app.device.runtime import INSTANCE_ID

    return INSTANCE_ID


def _is_browser_process(proc) -> bool:
    from app.device import appnames

    try:
        return appnames.is_browser(proc.name())
    except Exception:
        return False


class _NoLookup(Exception):
    """No way of finding which process owns the other end of a connection worked."""


def _run_tool(argv: list[str]) -> str:
    import subprocess

    out = subprocess.run(argv, capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL, check=False)
    return out.stdout or ""


def _pid_from_lsof(port: int) -> int | None:
    # -F: p<pid> then n<local>-><remote> lines; the client end is the one whose local port is `port`
    text = _run_tool(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:ESTABLISHED", "-Fpn"])
    pid = None
    for line in text.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and "->" in line and pid is not None:
            local = line[1:].split("->", 1)[0]
            if local.rsplit(":", 1)[-1] == str(port):
                return pid
    return None


def _pid_from_ss(port: int) -> int | None:
    import re

    text = _run_tool(["ss", "-tnpH", "state", "established", f"( sport = :{port} )"])
    m = re.search(r"pid=(\d+)", text)
    return int(m.group(1)) if m else None


def _pid_from_netstat(port: int) -> int | None:
    for line in _run_tool(["netstat", "-ano", "-p", "tcp"]).splitlines():
        cols = line.split()
        if len(cols) >= 5 and cols[0].upper() == "TCP" and cols[1].rsplit(":", 1)[-1] == str(port) \
                and cols[3].upper() == "ESTABLISHED" and cols[-1].isdigit():
            return int(cols[-1])
    return None


def _find_peer_pid(port: int) -> int | None:
    """The process that owns the client end of a loopback connection from `port`.

    psutil when it works, else the platform's own tools (lsof, ss, netstat -ano). Raises _NoLookup when
    none of them could be asked at all; returns None when one answered and found nothing."""
    psutil = _load_psutil()
    answered = False
    if psutil is not None:
        try:
            for c in psutil.net_connections("tcp"):
                if c.laddr and c.laddr.port == port and c.pid:
                    return c.pid
            answered = True
        except Exception:
            pass
    for finder in (_pid_from_lsof, _pid_from_ss, _pid_from_netstat):
        try:
            pid = finder(port)
        except Exception:
            continue
        if pid is not None:
            return pid
        answered = True
    if not answered:
        raise _NoLookup()
    return None


def _inspect_without_psutil(pid: int) -> tuple[list[int], list[dict]]:
    """(pids from the process up through its parents, their environments where readable). Linux and macOS."""
    chain: list[int] = []
    envs: list[dict] = []
    cur = pid
    for _ in range(64):
        if cur <= 1 or cur in chain:
            break
        chain.append(cur)
        env: dict = {}
        try:
            if sys.platform.startswith("linux"):
                with open(f"/proc/{cur}/environ", "rb") as fh:
                    for item in fh.read().split(b"\0"):
                        k, _, v = item.partition(b"=")
                        env[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
            else:
                text = _run_tool(["ps", "eww", "-o", "command=", "-p", str(cur)])
                marker = "AURA_SPAWNED_BY="
                if marker in text:
                    env["AURA_SPAWNED_BY"] = text.split(marker, 1)[1].split()[0]
        except OSError:
            pass
        envs.append(env)
        ppid = _run_tool(["ps", "-o", "ppid=", "-p", str(cur)]).strip()
        if not ppid.isdigit():
            if sys.platform.startswith("linux"):
                try:
                    with open(f"/proc/{cur}/stat") as fh:
                        ppid = fh.read().rsplit(")", 1)[1].split()[1]
                except (OSError, IndexError):
                    break
            else:
                break
        cur = int(ppid)
    return chain, envs


def _classify(request: Request) -> str:
    """'aura' (a program AURA started, or this server), 'other', or 'unknown' (the peer could not be identified).

    It is AURA's if it is this server, carries this instance's `AURA_SPAWNED_BY` marker (every shell tool
    sets it), or has this server among its ancestors (a browser AURA opened for the user is exempt from that
    last test only: the marker still catches a browser run from a shell tool)."""
    peer = request.client
    if peer is None or not peer.port:
        return "unknown"
    try:
        pid = _find_peer_pid(peer.port)
    except _NoLookup:
        return "unknown"
    except Exception:
        return "unknown"
    if pid is None:
        return "unknown"
    me = os.getpid()
    if pid == me:
        return "aura"
    marker = _instance_id()
    psutil = _load_psutil()
    try:
        if psutil is not None:
            proc = psutil.Process(pid)
            chain = [proc, *proc.parents()]
            for p in chain:
                try:
                    if (p.environ() or {}).get("AURA_SPAWNED_BY") == marker:
                        return "aura"
                except Exception:
                    continue
            if any(p.pid == me for p in chain[1:]) and not _is_browser_process(proc):
                return "aura"
        else:
            pids, envs = _inspect_without_psutil(pid)
            if not pids:
                return "unknown"
            if any(e.get("AURA_SPAWNED_BY") == marker for e in envs):
                return "aura"
            if me in pids[1:]:
                return "aura"
    except Exception:
        return "unknown"
    return "other"


def classify_peer(request: Request) -> str:
    return _classify(request)


def peer_is_aura_spawned(request: Request) -> bool:
    """Does this loopback request come from a process AURA started? (False when it cannot be told.)"""
    return _classify(request) == "aura"


def _refuse_self(db: Session, user: User, what: str) -> None:
    from app.permissions import changes

    changes.self_change_blocked(db, user, what)
    raise HTTPException(
        status_code=403,
        detail="A program on this computer started by AURA tried to do this, so it was refused. Use the AURA window yourself.",
    )


def guard_local_caller(request: Request, db: Session, user: User, what: str, *, strict: bool = True,
                       synthetic: bool = True) -> None:
    """The checks every local caller of a permission-changing route or an approval answer passes:
    not a process AURA started, not an unidentifiable one (`strict`), and not within seconds of AURA
    clicking or typing (`synthetic`). Routes that are not permission changes (chat) use `strict=False`:
    a caller that cannot be identified is let through, one that is AURA's own is not."""
    if not settings.local_mode or is_remote_session(request):
        return
    kind = classify_peer(request)
    if kind == "aura":
        _refuse_self(db, user, what)
    if kind == "unknown" and strict:
        raise HTTPException(
            status_code=403,
            detail="AURA could not tell which program is asking, so it did not do this. Try again from the AURA window.",
        )
    if synthetic:
        from app.permissions import access

        if access.recent_synthetic_input(user.id):
            raise HTTPException(status_code=409, detail="Use your own mouse or keyboard to answer this.")


def require_not_aura_spawned(request: Request, user: CurrentUser, db: DbSession) -> User:
    """For the routes that act as the owner without changing permissions (chat, stream, continue, stop,
    presence, channels, schedule and automation runs, tick): a program AURA started cannot use them."""
    guard_local_caller(request, db, user, f"{request.method} {request.url.path}", strict=False, synthetic=False)
    return user


GuardedUser = Annotated[User, Depends(require_not_aura_spawned)]


def require_presence(request: Request, user: CurrentUser, db: DbSession) -> User:
    """For every route that changes what AURA may do (spec 8.5).

    Hosted: the signed-in session is enough (the agent has no credential and no HTTP tool there).
    Local: the request must come from this computer, not from a paired device; the session must
    have been opened with the sign-in key; and the caller must pass `guard_local_caller`."""
    if not settings.local_mode:
        return user
    from app.middleware import is_loopback

    peer = request.client.host if request.client else ""
    if is_remote_session(request) or not is_loopback(peer):
        raise HTTPException(status_code=403, detail="This can only be changed on the computer AURA runs on.")
    if token_amr(request) != "local-key":
        raise HTTPException(
            status_code=403,
            detail="This sign-in cannot change permissions. Open AURA from its app window (the link with the sign-in key).",
        )
    guard_local_caller(request, db, user, f"{request.method} {request.url.path}")
    return user


PresentUser = Annotated[User, Depends(require_presence)]


def require_present_owner(request: Request, user: CurrentUser, db: DbSession) -> User:
    """`require_local_owner` (local installs only) plus the presence checks."""
    require_local_owner(request, user)
    return require_presence(request, user, db)


PresentOwner = Annotated[User, Depends(require_present_owner)]
