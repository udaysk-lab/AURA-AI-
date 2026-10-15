"""Using this computer's AURA from another device.

Pairing, the whole story:

1. On this computer, Settings → "Use AURA from your phone" shows a one-time
   code (8 characters, valid 10 minutes) and this computer's addresses.
2. On the phone, open one of those addresses; the sign-in screen asks for the
   code. Five wrong tries and the code is burned.
3. The phone gets a token naming a PairedDevice row. Every request it makes
   carries it, and unpairing the row here signs the phone out at once.

What a paired device can't do: sign in as the owner directly, run commands
from the console, change AURA's model or remote-access settings, or pair
further devices. It uses AURA — chat, approvals, the screens — through the
same trust tiers and approval cards as everything else.

All of this is off unless REMOTE_ACCESS is on, and only on a local install.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import socket
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import settings
from app.deps import CurrentUser, DbSession, LocalOwner, is_remote_session
from app.models import PairedDevice, PairingCode, User, utcnow
from app.schemas import UserOut
from app.security import create_access_token

log = logging.getLogger("aura.remote")
router = APIRouter(prefix="/api/remote", tags=["remote"])

# No 0/O, 1/I/L: a code is read off one screen and typed on another.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5


class PairingOut(BaseModel):
    code: str
    expires_at: datetime
    urls: list[str]


class PairIn(BaseModel):
    code: str = Field(min_length=4, max_length=20)
    device_name: str = Field(default="", max_length=120)


class PairOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
    device_id: str


class DeviceOut(BaseModel):
    id: str
    name: str
    created_at: datetime
    last_seen_at: datetime | None
    last_address: str
    this_device: bool = False


class RemoteStatusOut(BaseModel):
    available: bool          # a local install, where remote access can exist at all
    enabled: bool            # remote access is on in the running app
    listening: bool          # AURA was started listening beyond this computer
    remote_session: bool     # the caller is a paired device
    urls: list[str]


def _normalise(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def _hash(code: str) -> str:
    return hashlib.sha256(_normalise(code).encode()).hexdigest()


def _format(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def _port() -> int:
    try:
        return int(os.environ.get("AURA_PORT") or 0) or 8765
    except ValueError:
        return 8765


def _listening() -> bool:
    return os.environ.get("AURA_BIND_HOST", "127.0.0.1") not in ("127.0.0.1", "localhost", "::1")


def network_addresses() -> list[str]:
    """This computer's addresses another device on the network could use."""
    found: list[str] = []

    def add(ip: str) -> None:
        if ip and not ip.startswith(("127.", "169.254.")) and ip not in found:
            found.append(ip)

    try:
        # A UDP "connect" sends nothing; it just asks the OS which interface
        # would carry traffic out, which is the address the LAN knows us by.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("10.255.255.255", 1))
            add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    return found


def pairing_urls() -> list[str]:
    scheme = "https" if os.environ.get("AURA_TLS") == "on" else "http"
    port = _port()
    hosts = settings.remote_hostname_list + network_addresses()
    return [f"{scheme}://{host}:{port}" for host in hosts]


def _require_enabled() -> None:
    if not settings.remote_access_enabled:
        raise HTTPException(
            status_code=404,
            detail="Remote access is turned off on this computer.",
        )


@router.get("/status", response_model=RemoteStatusOut)
def remote_status(request: Request, user: CurrentUser) -> RemoteStatusOut:
    return RemoteStatusOut(
        available=settings.local_mode,
        enabled=settings.remote_access_enabled,
        listening=settings.local_mode and _listening(),
        remote_session=is_remote_session(request),
        urls=pairing_urls() if settings.remote_access_enabled else [],
    )


@router.post("/pairing", response_model=PairingOut)
def start_pairing(owner: LocalOwner, db: DbSession) -> PairingOut:
    """A fresh code. Any earlier unused code stops working."""
    _require_enabled()
    now = utcnow()
    for old in db.scalars(
        select(PairingCode).where(PairingCode.user_id == owner.id, PairingCode.used_at.is_(None))
    ).all():
        old.used_at = now

    code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    row = PairingCode(user_id=owner.id, code_hash=_hash(code), expires_at=now + CODE_TTL)
    db.add(row)
    db.commit()
    log.info("Pairing started; code valid for %d minutes", int(CODE_TTL.total_seconds() // 60))
    return PairingOut(code=_format(code), expires_at=row.expires_at, urls=pairing_urls())


@router.post("/pair", response_model=PairOut)
def pair(payload: PairIn, request: Request, db: DbSession) -> PairOut:
    """Trade a pairing code for a device token. Reachable before sign-in."""
    _require_enabled()
    now = datetime.now(timezone.utc)
    wrong = HTTPException(
        status_code=400,
        detail="That code isn't right, or it has expired. Check it on the computer, or start pairing again.",
    )

    live = db.scalars(
        select(PairingCode)
        .where(PairingCode.used_at.is_(None))
        .order_by(PairingCode.created_at.desc())
    ).all()
    candidates = [
        c for c in live
        if (c.expires_at if c.expires_at.tzinfo else c.expires_at.replace(tzinfo=timezone.utc)) > now
    ]
    if not candidates:
        raise HTTPException(
            status_code=400,
            detail="No pairing is in progress. On the computer running AURA, open Settings → Use AURA from your phone → Pair a device.",
        )

    offered = _hash(payload.code)
    match = next((c for c in candidates if hmac.compare_digest(c.code_hash, offered)), None)
    if match is None:
        # Every live code pays for a wrong guess, so guessing can't be spread
        # across them, and five misses end the pairing.
        for c in candidates:
            c.attempts += 1
            if c.attempts >= MAX_ATTEMPTS:
                c.used_at = now
        db.commit()
        log.warning(
            "Wrong pairing code from %s", request.client.host if request.client else "?"
        )
        raise wrong

    user = db.get(User, match.user_id)
    if not user or not user.is_active:
        raise wrong

    match.used_at = now
    name = payload.device_name.strip() or "Paired device"
    device = PairedDevice(
        user_id=user.id,
        name=name[:120],
        user_agent=(request.headers.get("user-agent") or "")[:300],
        last_seen_at=now,
        last_address=(request.client.host if request.client else "")[:64],
    )
    db.add(device)
    db.commit()
    db.refresh(device)

    expires = now + timedelta(days=max(1, settings.remote_session_days))
    token = create_access_token(
        user.id, extra={"typ": "remote", "did": device.id, "exp": int(expires.timestamp())}
    )
    log.info("Paired a new device: %s", device.name)
    return PairOut(access_token=token, user=UserOut.model_validate(user), device_id=device.id)


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(request: Request, owner: CurrentUser, db: DbSession) -> list[DeviceOut]:
    if not settings.local_mode:
        raise HTTPException(status_code=404, detail="Only available when AURA runs on your own computer.")
    current = getattr(request.state, "remote_device_id", None)
    rows = db.scalars(
        select(PairedDevice)
        .where(PairedDevice.user_id == owner.id, PairedDevice.revoked_at.is_(None))
        .order_by(PairedDevice.created_at.desc())
    ).all()
    return [
        DeviceOut(
            id=d.id, name=d.name, created_at=d.created_at, last_seen_at=d.last_seen_at,
            last_address=d.last_address, this_device=d.id == current,
        )
        for d in rows
    ]


@router.delete("/devices/{device_id}")
def unpair(device_id: str, owner: LocalOwner, db: DbSession) -> dict:
    device = db.get(PairedDevice, device_id)
    if not device or device.user_id != owner.id or device.revoked_at is not None:
        raise HTTPException(status_code=404, detail="No such paired device.")
    device.revoked_at = utcnow()
    db.commit()
    log.info("Unpaired %s", device.name)
    return {"unpaired": device.id}
