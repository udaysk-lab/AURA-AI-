"""Local computer access: connected folders, app grants, the app blocklist, presence (spec 10 section 10).

`snapshot(db, user)` reads the user's folders, grants and blocks fresh from the database every
time it is called (no cache), so a change applies to the very next call without a restart.
`bound(db, user)` makes a snapshot the "current access" of the code running inside it; the file
tools read their roots from it (`device/files.py`), and an unbound call has no roots and refuses.

Install-level environment settings are ceilings, never levels: DEVICE_ALLOWED_ROOTS (when set)
limits which folders count (a connected folder outside every root is inert); the user's choices
live in the database and must fit inside.

An existing install keeps its folders: the data migration `pm02` wrote the folders of
DEVICE_ALLOWED_ROOTS as real rows (source `legacy`). The hosted site has none of this: with device
control off every function here answers "nothing" without touching the database.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.device import appnames
from app.models import DeviceAppBlock, DeviceAppGrant, DeviceFolder, User, UserSettings, utcnow
from app.permissions import match

TIERS = ("view", "click", "full")
TIER_RANK = {t: i for i, t in enumerate(TIERS)}
PRESENCE_SECONDS = 90
SUSPEND_SECONDS = 60
COOLDOWN_SECONDS = 10

# The default blocklist (spec 10.3): (id, match). Keywords match inside a canonical app id.
DEFAULT_BLOCKS: tuple[tuple[str, str], ...] = tuple(
    [(k, "keyword") for k in (
        "bank", "wallet", "trading", "trade", "crypto", "coinbase", "binance", "kraken", "robinhood",
        "metatrader", "thinkorswim", "tradingview", "zerodha", "kite", "groww", "upstox", "angel-one",
        "paytm", "phonepe", "metamask", "ledger-live", "trezor", "exodus", "electrum", "1password",
        "bitwarden", "lastpass", "dashlane", "keepass", "keepassxc", "enpass", "nordpass", "proton-pass",
    )]
    + [("keychain-access", "app"), ("passwords", "app")]
)

# What AURA's own program or launcher is called to the operating system.
AURA_APP_IDS = frozenset({"aura", "aura-ai", "aura-desktop", "aura-launcher"})


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FolderInfo:
    id: str
    path: str
    path_key: str
    can_delete: bool
    source: str
    within_limit: bool = True
    created_at: datetime | None = None


@dataclass(frozen=True)
class GrantInfo:
    id: str
    app_id: str
    display_name: str
    tier: str


@dataclass(frozen=True)
class BlockInfo:
    id: str
    app_id: str
    match: str
    source: str


def _fold(path: str) -> str:
    return match.fold(path, always=True)


def _under(folded: str, root: str) -> bool:
    try:
        return os.path.commonpath([folded, root]) == root
    except ValueError:
        return False


def path_key(real_path: str) -> str:
    """sha256 of the folded realpath: how a connected folder is recognised (spec 2.5)."""
    return hashlib.sha256(match.fold(real_path).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Access:
    folders: tuple[FolderInfo, ...] = ()  # every active folder, inert ones included
    grants: tuple[GrantInfo, ...] = ()
    blocks: tuple[BlockInfo, ...] = ()
    ceiling: tuple[str, ...] = ()  # DEVICE_ALLOWED_ROOTS, when set

    @property
    def usable(self) -> tuple[FolderInfo, ...]:
        return tuple(f for f in self.folders if f.within_limit)

    @property
    def roots(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.usable)

    def folder_for(self, real_path: str) -> FolderInfo | None:
        """The most specific usable folder holding this (real) path."""
        f = _fold(real_path)
        best: FolderInfo | None = None
        for folder in self.usable:
            root = _fold(folder.path)
            if _under(f, root) and (best is None or len(root) > len(_fold(best.path))):
                best = folder
        return best

    def covers(self, real_path: str) -> bool:
        return self.folder_for(real_path) is not None

    def within_ceiling(self, real_path: str) -> bool:
        return within_ceiling(real_path, self.ceiling)

    def blocked(self, app_id: str) -> bool:
        return is_blocked(app_id, self.blocks)

    def grant(self, app_id: str) -> GrantInfo | None:
        return next((g for g in self.grants if g.app_id == app_id), None)


EMPTY = Access()
CURRENT_ACCESS: contextvars.ContextVar[Access | None] = contextvars.ContextVar("aura_current_access", default=None)


def current() -> Access:
    """The access of the running tool call; none bound means no folders at all."""
    return CURRENT_ACCESS.get() or EMPTY


def roots() -> list[str]:
    return list(current().roots)


@contextlib.contextmanager
def use(access: Access):
    token = CURRENT_ACCESS.set(access)
    try:
        yield access
    finally:
        CURRENT_ACCESS.reset(token)


@contextlib.contextmanager
def bound(db: Session | None, user: User | None):
    """Bind the user's fresh access for the code inside (tool handlers, device listing routes)."""
    with use(snapshot(db, user) if db is not None and user is not None else EMPTY) as acc:
        yield acc


# ---------------------------------------------------------------------------
# Ceilings
# ---------------------------------------------------------------------------


def ceiling_roots() -> tuple[str, ...]:
    """DEVICE_ALLOWED_ROOTS when it is set (non-empty); empty means no ceiling."""
    raw = [r.strip() for r in settings.device_allowed_roots.split(",") if r.strip()]
    return tuple(os.path.realpath(os.path.expanduser(r)) for r in raw)


def within_ceiling(real_path: str, ceiling: tuple[str, ...] | None = None) -> bool:
    ceiling = ceiling_roots() if ceiling is None else ceiling
    if not ceiling:
        return True
    f = _fold(real_path)
    return any(_under(f, _fold(r)) for r in ceiling)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _settings_row(db: Session, user: User) -> UserSettings | None:
    return user.settings or db.scalars(select(UserSettings).where(UserSettings.user_id == user.id)).first()


def _extra(db: Session, user: User) -> dict:
    row = _settings_row(db, user)
    return dict(row.extra or {}) if row else {}


def _set_extra(db: Session, user: User, **values) -> None:
    row = _settings_row(db, user)
    if row is None:
        row = UserSettings(user_id=user.id)
        db.add(row)
    row.extra = {**(row.extra or {}), **values}
    db.commit()


def seed_blocklist(db: Session, user_id: str) -> int:
    """Insert the default blocklist once per user. A default the user later deletes stays deleted."""
    user = db.get(User, user_id)
    if user is None:
        return 0
    if _extra(db, user).get("app_blocklist_seeded"):
        return 0
    have = {b.app_id for b in db.scalars(select(DeviceAppBlock).where(DeviceAppBlock.user_id == user_id)).all()}
    added = 0
    for app_id, how in DEFAULT_BLOCKS:
        if app_id not in have:
            db.add(DeviceAppBlock(user_id=user_id, app_id=app_id, match=how, source="default"))
            added += 1
    db.flush()
    _set_extra(db, user, app_blocklist_seeded=1)
    return added


def snapshot(db: Session | None, user: User | None) -> Access:
    """The user's access, read now. Empty on an install where device control is off."""
    if db is None or user is None or not settings.device_control_enabled:
        return EMPTY
    seed_blocklist(db, user.id)
    ceiling = ceiling_roots()
    rows = db.scalars(
        select(DeviceFolder).where(DeviceFolder.user_id == user.id).order_by(DeviceFolder.created_at, DeviceFolder.id)
    ).all()
    folders = [
        FolderInfo(id=r.id, path=r.path, path_key=r.path_key, can_delete=bool(r.can_delete), source=r.source,
                   within_limit=within_ceiling(r.path, ceiling), created_at=r.created_at)
        for r in rows if r.revoked_at is None
    ]
    grants = tuple(
        GrantInfo(g.id, g.app_id, g.display_name or g.app_id, g.tier)
        for g in db.scalars(
            select(DeviceAppGrant).where(DeviceAppGrant.user_id == user.id, DeviceAppGrant.revoked_at.is_(None))
            .order_by(DeviceAppGrant.created_at, DeviceAppGrant.id)
        ).all()
    )
    blocks = tuple(
        BlockInfo(b.id, b.app_id, b.match, b.source)
        for b in db.scalars(
            select(DeviceAppBlock).where(DeviceAppBlock.user_id == user.id)
            .order_by(DeviceAppBlock.created_at, DeviceAppBlock.id)
        ).all()
    )
    return Access(tuple(folders), grants, blocks, ceiling)


# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------


def keyword_form(text: str) -> str:
    return re.sub(r"[\W_]+", "-", (text or "").lower()).strip("-")


def is_blocked(app_id: str, blocks) -> bool:
    app_id = (app_id or "").lower()
    for b in blocks:
        bid = (b.app_id or "").lower()
        if (b.match == "keyword" and bid and bid in app_id) or (b.match != "keyword" and app_id == bid):
            return True
    return False


def is_aura_app(app_id: str) -> bool:
    cid = appnames.canonical(app_id)
    if cid in AURA_APP_IDS:
        return True
    import sys

    if getattr(sys, "frozen", False):
        return cid == appnames.canonical(os.path.basename(sys.executable))
    return False


def need_tier(tool: str) -> str:
    return {"take_screenshot": "view", "mouse_click": "click", "scroll_screen": "click"}.get(tool, "full")


# ---------------------------------------------------------------------------
# Screen control: the user's switch, presence, suspension, synthetic-input cooldown
# ---------------------------------------------------------------------------


def screen_control_user(db: Session, user: User) -> bool:
    """The user's own switch for mouse and keyboard use. On unless they turned it off."""
    return _extra(db, user).get("device_screen_control", True) is not False


def set_screen_control_user(db: Session, user: User, enabled: bool) -> None:
    _set_extra(db, user, device_screen_control=bool(enabled))


_lock = threading.Lock()
_presence: dict[str, float] = {}
_suspended: dict[str, float] = {}
_synthetic: dict[str, float] = {}


def _now() -> float:
    return time.monotonic()


def note_presence(user_id: str) -> None:
    with _lock:
        _presence[user_id] = _now()


def is_present(user_id: str) -> bool:
    with _lock:
        ts = _presence.get(user_id)
    return ts is not None and _now() - ts <= PRESENCE_SECONDS


def clear_presence(user_id: str | None = None) -> None:
    with _lock:
        if user_id is None:
            _presence.clear()
        else:
            _presence.pop(user_id, None)


def suspend_screen(user_id: str) -> None:
    with _lock:
        _suspended[user_id] = _now() + SUSPEND_SECONDS


def screen_suspended(user_id: str) -> bool:
    with _lock:
        until = _suspended.get(user_id)
    return until is not None and _now() < until


def note_synthetic_input(user_id: str) -> None:
    with _lock:
        _synthetic[user_id] = _now()


def recent_synthetic_input(user_id: str, within: float = COOLDOWN_SECONDS) -> bool:
    with _lock:
        ts = _synthetic.get(user_id)
    return ts is not None and _now() - ts <= within


def reset_state() -> None:
    """For tests."""
    with _lock:
        _presence.clear()
        _suspended.clear()
        _synthetic.clear()
