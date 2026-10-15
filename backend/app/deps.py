"""Shared FastAPI dependencies."""

from __future__ import annotations

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
