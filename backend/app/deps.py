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


def generated_local_key() -> str:
    """The sign-in key this process made at startup ("" when none, or when one is configured)."""
    return _generated_local_key


def ensure_local_key() -> str:
    """LOCAL_MODE without AURA_LOCAL_LOGIN_KEY: make a key, keep it in memory and in
    `<aura_home>/local-login.key` (0600). Signing in with it gives `amr = local-key`; signing in
    without it still works but gives `local-open`, which cannot change permissions (spec 8.5).

    The key itself is never written to the log. A person starting AURA from a terminal is shown the
    sign-in link there; otherwise the file is where to read it."""
    global _generated_local_key
    if not settings.local_mode or settings.aura_local_login_key:
        return ""
    key = secrets.token_urlsafe(32)
    path = os.path.join(settings.aura_home_path, LOCAL_KEY_FILE)
    try:
        os.makedirs(settings.aura_home_path, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(key)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as exc:
        log.warning("Could not write the local sign-in key file (%s)", exc)
        path = ""
    _generated_local_key = key
    port = os.environ.get("AURA_PORT") or "8765"
    log.info("This session has no sign-in key configured. Signing in with the generated key "
             "(%s) allows permission changes; signing in without it does not.", path or "kept in memory only")
    try:
        if sys.stderr and sys.stderr.isatty():
            print(f"Open http://127.0.0.1:{port}/login#key={key}", file=sys.stderr)
    except Exception:
        pass
    return key


def check_local_key(offered: str) -> bool:
    return bool(_generated_local_key and offered and hmac.compare_digest(offered.encode(), _generated_local_key.encode()))


def token_amr(request: Request) -> str:
    """How this request's session was opened: local-key, local-open, password, oauth, demo or remote.

    On a local install a session token without the claim (made before this existed) counts as
    `local-open`: it can be used, not trusted to change permissions."""
    if is_remote_session(request):
        return "remote"
    claims = getattr(request.state, "token_claims", None) or {}
    amr = str(claims.get("amr") or "")
    if not amr and settings.local_mode:
        return "local-open"
    return amr


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


def peer_is_aura_spawned(request: Request) -> bool:
    """Does this loopback request come from a process AURA started?

    Finds the process that owns the client end of the connection. It is AURA's if it is this
    server, if it carries this instance's `AURA_SPAWNED_BY` marker (every shell tool sets it), or
    if one of its ancestors is this server (a browser AURA opened for the user is exempt from that
    last test only: the marker still catches a browser run from a shell tool). Any lookup failure
    (no psutil, access denied) answers False: this is a layer, not a wall (spec 8.6)."""
    psutil = _load_psutil()
    peer = request.client
    if psutil is None or peer is None or not peer.port:
        return False
    try:
        pid = None
        for c in psutil.net_connections("tcp"):
            if c.laddr and c.laddr.port == peer.port and c.pid:
                pid = c.pid
                break
        if pid is None:
            return False
        me = os.getpid()
        if pid == me:
            return True
        proc = psutil.Process(pid)
        chain = [proc, *proc.parents()]
        marker = _instance_id()
        for p in chain:
            try:
                if (p.environ() or {}).get("AURA_SPAWNED_BY") == marker:
                    return True
            except Exception:
                continue
        if any(p.pid == me for p in chain[1:]) and not _is_browser_process(proc):
            return True
    except Exception:
        return False
    return False


def _refuse_self(db: Session, user: User, what: str) -> None:
    from app.permissions import changes

    changes.self_change_blocked(db, user, what)
    raise HTTPException(
        status_code=403,
        detail="A program on this computer started by AURA tried to do this, so it was refused. Use the AURA window yourself.",
    )


def guard_local_caller(request: Request, db: Session, user: User, what: str) -> None:
    """The checks every local caller of a permission-changing route or an approval answer passes:
    not a process AURA started, and not within seconds of AURA clicking or typing."""
    if not settings.local_mode or is_remote_session(request):
        return
    if peer_is_aura_spawned(request):
        _refuse_self(db, user, what)
    from app.permissions import access

    if access.recent_synthetic_input(user.id):
        raise HTTPException(status_code=409, detail="Use your own mouse or keyboard to answer this.")


def require_presence(request: Request, user: CurrentUser, db: DbSession) -> User:
    """For every route that changes what AURA may do (spec 8.5).

    Hosted: the signed-in session is enough (the agent has no credential and no HTTP tool there).
    Local: the request must come from this computer, not from a paired device; the session must
    not be a `local-open` one; and the caller must pass `guard_local_caller`."""
    if not settings.local_mode:
        return user
    from app.middleware import is_loopback

    peer = request.client.host if request.client else ""
    if is_remote_session(request) or not is_loopback(peer):
        raise HTTPException(status_code=403, detail="This can only be changed on the computer AURA runs on.")
    if token_amr(request) == "local-open":
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
