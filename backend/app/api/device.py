"""Device control endpoints.

Two audiences:

* **The Device screen** — what this machine is, what AURA may do on it, which
  folders are in scope, what's running, and a console the *user* can type into
  directly. A command typed here is the person acting on their own computer, so
  it doesn't go through the agent's approval gate; it is still logged to the
  activity trail with `actor="user"` so the audit history stays complete.
* **/setup and diagnostics** — the same status payload, so a local install can
  say "screen control needs one more package" instead of silently not working.

Every route is 404 when device control is off, so a hosted deployment doesn't
advertise a surface it won't serve.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import skills as skill_registry
from app.config import settings
from app.device import files as device_files
from app.device import policy as device_policy
from app.device import shell as device_shell
from app.device import system as device_system
from app.device.runtime import DeviceUnavailable, capabilities
from app.services import redaction
from app.deps import CurrentUser, DbSession, GuardedUser, PresentUser, is_remote_session
from app.device import appnames, windows
from app.middleware import is_loopback
from app.models import ActivityLog, DeviceAppBlock, DeviceAppGrant, DeviceFolder, User, utcnow
from app.permissions import access, changes, floors, match
from app.schemas import (
    DeviceAppBlockOut,
    DeviceAppGrantOut,
    DeviceAppIn,
    DeviceAppsOut,
    DeviceBlockIn,
    DeviceFolderIn,
    DeviceFolderOut,
    DeviceFolderPatch,
    DeviceRunningAppOut,
    ScreenControlIn,
    DeviceBackgroundOut,
    DeviceListingOut,
    DevicePolicyOut,
    DeviceProcessOut,
    DeviceRunIn,
    DeviceRunOut,
    DeviceStatusOut,
)

log = logging.getLogger("aura.device.api")
router = APIRouter(prefix="/api/device", tags=["device"])


def _require_device() -> None:
    if not settings.device_control_enabled:
        raise HTTPException(
            status_code=404,
            detail=(
                "Device control is off on this instance. It's local-only: run AURA on "
                "your own computer with LOCAL_MODE=true."
            ),
        )


@router.get("", response_model=DeviceStatusOut)
def device_status(request: Request, user: CurrentUser, db: DbSession) -> DeviceStatusOut:
    """What this machine is and what AURA may do on it.

    Answers even when device control is off (with `enabled: false`), because
    that is exactly the question the Device screen needs answered in order to
    explain itself.
    """
    data = capabilities(include_policy=True)
    raw_policy = data.pop("policy", None)
    active: list[str] = []
    if settings.device_control_enabled:
        enabled = skill_registry.enabled_codes(db, user.id)
        active = sorted(c for c in enabled if skill_registry.is_device_skill(c))

    out_policy = None
    if raw_policy:
        out_policy = DevicePolicyOut(**raw_policy, explanation=device_policy.describe_mode())

    local_session = _presence_source_ok(request)
    return DeviceStatusOut(
        **{k: v for k, v in data.items() if k in DeviceStatusOut.model_fields},
        policy=out_policy,
        active_skills=active,
        presence_accepted=local_session,
        present=bool(settings.device_control_enabled and access.is_present(user.id)),
        screen_control_user=access.screen_control_user(db, user) if settings.device_control_enabled else True,
    )


@router.post("/run", response_model=DeviceRunOut)
def run_command(
    payload: DeviceRunIn, request: Request, user: PresentUser, db: DbSession
) -> DeviceRunOut:
    """Run a command from the Device console.

    This is the user's own terminal inside their own app, so there's no
    approval card — they typed it. The denylist floor in the shell layer still
    applies, and it lands in the activity log either way.

    Not from a paired phone: a lost phone shouldn't be a shell on this
    computer. From there, ask AURA in chat, where commands go through the
    permission gate and the approval cards like everything else.
    """
    _require_device()
    if is_remote_session(request):
        raise HTTPException(
            status_code=403,
            detail=(
                "The console only works on the computer itself. From another device, "
                "ask AURA in chat — commands then come to you for approval."
            ),
        )
    from app import permissions

    decision = permissions.console_decision(db, user, payload.command, payload.workdir or None)
    if decision.kind != "allow":
        result = {"command": payload.command, "error": f"Refused: {decision.reason}"}
    else:
        try:
            with access.bound(db, user):
                result = device_shell.run(payload.command, payload.workdir or None, payload.timeout)
        except DeviceUnavailable as exc:
            result = {"command": payload.command, "error": str(exc)}

    db.add(
        ActivityLog(
            user_id=user.id,
            actor="user",
            action="device_console",
            target=redaction.scrub(db, user.id, payload.command)[:300],
            status="error" if result.get("error") or not result.get("ok") else "success",
            detail={"exit_code": result.get("exit_code")},
        )
    )
    db.commit()
    return DeviceRunOut(**{k: v for k, v in result.items() if k in DeviceRunOut.model_fields})


@router.get("/files", response_model=DeviceListingOut)
def list_files(
    user: CurrentUser,
    db: DbSession,
    path: str = Query(default="~"),
    show_hidden: bool = Query(default=False),
) -> DeviceListingOut:
    """Browse a folder inside the allowed roots."""
    _require_device()
    try:
        with access.bound(db, user):
            return DeviceListingOut(**device_files.list_directory(path, show_hidden))
    except DeviceUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/processes", response_model=list[DeviceProcessOut])
def list_processes(
    user: CurrentUser,
    query: str = Query(default=""),
    limit: int = Query(default=30, ge=1, le=200),
) -> list[DeviceProcessOut]:
    _require_device()
    try:
        rows = device_system.list_processes(query, limit)
    except DeviceUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [DeviceProcessOut(**r) for r in rows]


@router.get("/background", response_model=list[DeviceBackgroundOut])
def list_background(user: CurrentUser) -> list[DeviceBackgroundOut]:
    """Background commands AURA has running in this process."""
    _require_device()
    return [DeviceBackgroundOut(**s) for s in device_shell.list_sessions()]


@router.post("/background/{session_id}/stop", response_model=DeviceBackgroundOut)
def stop_background(session_id: str, user: CurrentUser, db: DbSession) -> DeviceBackgroundOut:
    _require_device()
    result = device_shell.stop(session_id)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    db.add(
        ActivityLog(
            user_id=user.id, actor="user", action="device_stop_background",
            target=session_id, detail=result,
        )
    )
    db.commit()
    return DeviceBackgroundOut(
        session_id=session_id,
        command="",
        status=result.get("status", "stopped"),
        exit_code=result.get("exit_code"),
    )


@router.get("/system")
def system_snapshot(user: CurrentUser) -> dict:
    """CPU, memory, disk and battery, for the Device screen's header."""
    _require_device()
    try:
        return device_system.system_info()
    except DeviceUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# This computer: connected folders, apps, blocklist, presence, screen control
# (spec 10 sections 10 and 11). Every change needs the person at the computer
# (PresentUser) and is logged and notified (permissions/changes.py).
# ---------------------------------------------------------------------------


def _presence_source_ok(request: Request) -> bool:
    """Presence is accepted only from this computer itself, never from a paired device."""
    peer = request.client.host if request.client else ""
    return bool(settings.device_control_enabled and settings.local_mode
                and not is_remote_session(request) and is_loopback(peer))


@router.post("/presence", status_code=204)
def presence(request: Request, user: GuardedUser) -> Response:
    """The web app says the person is looking at AURA (every 30 s while a page is visible)."""
    _require_device()
    if not _presence_source_ok(request):
        raise HTTPException(status_code=403, detail="Presence counts only from the computer itself.")
    access.note_presence(user.id)
    return Response(status_code=204)


@router.put("/screen-control")
def set_screen_control(payload: ScreenControlIn, user: PresentUser, db: DbSession) -> dict:
    """The user's switch for mouse and keyboard use. The install setting stays the ceiling above it."""
    _require_device()
    before = access.screen_control_user(db, user)
    access.set_screen_control_user(db, user, payload.enabled)
    if before != payload.enabled:
        changes.record(
            db, user, "system_changed",
            "screen control turned " + ("on" if payload.enabled else "off"),
            widens=payload.enabled, target="screen-control", detail={"enabled": payload.enabled},
        )
    return {"enabled": payload.enabled, "available": bool(capabilities().get("screen_control"))}


# --- folders ---------------------------------------------------------------


def _folder_out(f: access.FolderInfo) -> DeviceFolderOut:
    return DeviceFolderOut(id=f.id, path=f.path, can_delete=f.can_delete, source=f.source,
                           within_install_limit=f.within_limit, created_at=f.created_at)


def _row_out(row: DeviceFolder) -> DeviceFolderOut:
    return DeviceFolderOut(id=row.id, path=row.path, can_delete=bool(row.can_delete), source=row.source,
                           within_install_limit=access.within_ceiling(row.path), created_at=row.created_at)


def _folder_row(db: Session, user: User, folder_id: str) -> DeviceFolder:
    row = db.get(DeviceFolder, folder_id)
    if row is None or row.user_id != user.id or row.revoked_at is not None:
        raise HTTPException(status_code=404, detail="Folder not found")
    return row


@router.get("/folders", response_model=list[DeviceFolderOut])
def list_folders(user: CurrentUser, db: DbSession) -> list[DeviceFolderOut]:
    _require_device()
    return [_folder_out(f) for f in access.snapshot(db, user).folders]


def _vet_folder(raw: str) -> str:
    """A folder the user may connect: it exists, is not a whole drive, not AURA's own, within the install limit."""
    try:
        real = match.normalize_path(raw)
    except match.BadArgs as exc:
        raise HTTPException(status_code=422, detail=f"That is not a usable path: {exc}") from exc
    if not os.path.isdir(real):
        raise HTTPException(status_code=422, detail="That folder does not exist.")
    if os.path.dirname(real) == real:
        raise HTTPException(status_code=422, detail="A whole drive cannot be connected. Pick a folder on it.")
    if floors.in_self_path(real):
        raise HTTPException(status_code=422, detail="That is part of AURA itself and cannot be connected.")
    if floors.in_protected(real):
        raise HTTPException(status_code=422, detail="That is a protected location (keys, credentials, startup items).")
    if not access.within_ceiling(real):
        raise HTTPException(status_code=422, detail="That is outside the limit this install sets for AURA's folders.")
    return real


@router.post("/folders", response_model=DeviceFolderOut)
def connect_folder(payload: DeviceFolderIn, user: PresentUser, db: DbSession) -> DeviceFolderOut:
    _require_device()
    real = _vet_folder(payload.path)
    key = access.path_key(real)
    row = db.scalars(select(DeviceFolder).where(DeviceFolder.user_id == user.id, DeviceFolder.path_key == key)).first()
    if row is None:
        row = DeviceFolder(user_id=user.id, path=real, path_key=key, can_delete=payload.can_delete, source="user")
        db.add(row)
    elif row.revoked_at is not None:
        row.revoked_at, row.can_delete, row.source = None, payload.can_delete, "user"
    elif payload.can_delete and not row.can_delete:
        row.can_delete = True
    else:
        return _row_out(row)
    db.commit()
    db.refresh(row)
    changes.record(db, user, "folder_added", f"folder connected: {real}", widens=True, target=real,
                   detail={"folder_id": row.id, "can_delete": bool(row.can_delete)})
    return _row_out(row)


@router.patch("/folders/{folder_id}", response_model=DeviceFolderOut)
def update_folder(folder_id: str, payload: DeviceFolderPatch, user: PresentUser, db: DbSession) -> DeviceFolderOut:
    _require_device()
    row = _folder_row(db, user, folder_id)
    if bool(row.can_delete) != payload.can_delete:
        row.can_delete = payload.can_delete
        db.commit()
        changes.record(db, user, "folder_changed",
                       f"deleting files {'allowed' if payload.can_delete else 'not allowed'} in {row.path}",
                       widens=payload.can_delete, target=row.path,
                       detail={"folder_id": row.id, "can_delete": payload.can_delete})
    db.refresh(row)
    return _row_out(row)


@router.delete("/folders/{folder_id}")
def remove_folder(folder_id: str, user: PresentUser, db: DbSession) -> dict:
    _require_device()
    row = _folder_row(db, user, folder_id)
    row.revoked_at = utcnow()
    db.commit()
    changes.record(db, user, "folder_removed", f"folder removed: {row.path}", target=row.path,
                   detail={"folder_id": row.id})
    return {"removed": True}


# --- apps and blocklist ----------------------------------------------------


@router.get("/apps", response_model=DeviceAppsOut)
def list_apps(user: CurrentUser, db: DbSession) -> DeviceAppsOut:
    _require_device()
    acc = access.snapshot(db, user)
    return DeviceAppsOut(
        grants=[DeviceAppGrantOut(id=g.id, app_id=g.app_id, display_name=g.display_name, tier=g.tier)
                for g in acc.grants],
        blocks=[DeviceAppBlockOut(id=b.id, app_id=b.app_id, match=b.match, source=b.source) for b in acc.blocks],
        running=[DeviceRunningAppOut(**r) for r in windows.running_apps()],
    )


@router.put("/apps/{app_id:path}", response_model=DeviceAppGrantOut)
def grant_app(app_id: str, payload: DeviceAppIn, user: PresentUser, db: DbSession) -> DeviceAppGrantOut:
    _require_device()
    canon = appnames.canonical(app_id)
    if not canon:
        raise HTTPException(status_code=422, detail="That is not an app name.")
    acc = access.snapshot(db, user)
    if acc.blocked(canon) or access.is_aura_app(canon):
        raise HTTPException(status_code=422, detail="That app is on the blocked list, so it cannot be allowed.")
    if payload.tier == "full" and (appnames.is_terminal(canon) or appnames.is_ide(canon)):
        raise HTTPException(status_code=422, detail="Terminals and code editors can be allowed to click and scroll only.")
    row = db.scalars(select(DeviceAppGrant).where(DeviceAppGrant.user_id == user.id, DeviceAppGrant.app_id == canon)).first()
    name = (app_id.strip() or canon)[:200]
    if row is None:
        row = DeviceAppGrant(user_id=user.id, app_id=canon, display_name=name, tier=payload.tier)
        db.add(row)
        widens = True
    else:
        widens = row.revoked_at is not None or access.TIER_RANK[payload.tier] > access.TIER_RANK.get(row.tier, 0)
        row.tier, row.revoked_at = payload.tier, None
    db.commit()
    db.refresh(row)
    changes.record(db, user, "app_granted", f"{row.display_name or canon} allowed on screen ({payload.tier})",
                   widens=widens, target=canon, detail={"tier": payload.tier})
    return DeviceAppGrantOut(id=row.id, app_id=row.app_id, display_name=row.display_name, tier=row.tier)


@router.delete("/apps/{app_id:path}")
def revoke_app(app_id: str, user: PresentUser, db: DbSession) -> dict:
    _require_device()
    canon = appnames.canonical(app_id)
    row = db.scalars(select(DeviceAppGrant).where(
        DeviceAppGrant.user_id == user.id, DeviceAppGrant.app_id == canon, DeviceAppGrant.revoked_at.is_(None))).first()
    if row is None:
        raise HTTPException(status_code=404, detail="That app was not allowed.")
    row.revoked_at = utcnow()
    db.commit()
    changes.record(db, user, "app_revoked", f"{row.display_name or canon} no longer allowed on screen", target=canon)
    return {"removed": True}


@router.post("/blocklist", response_model=DeviceAppBlockOut)
def add_block(payload: DeviceBlockIn, user: PresentUser, db: DbSession) -> DeviceAppBlockOut:
    _require_device()
    app_id = appnames.canonical(payload.app_id) if payload.match == "app" else access.keyword_form(payload.app_id)
    if not app_id:
        raise HTTPException(status_code=422, detail="Give an app name or a word in its name.")
    row = db.scalars(select(DeviceAppBlock).where(DeviceAppBlock.user_id == user.id, DeviceAppBlock.app_id == app_id)).first()
    if row is None:
        row = DeviceAppBlock(user_id=user.id, app_id=app_id, match=payload.match, source="user")
        db.add(row)
        # Blocking an app ends any access it had.
        for g in db.scalars(select(DeviceAppGrant).where(DeviceAppGrant.user_id == user.id,
                                                         DeviceAppGrant.revoked_at.is_(None))).all():
            if access.is_blocked(g.app_id, [row]):
                g.revoked_at = utcnow()
        db.commit()
        db.refresh(row)
        changes.record(db, user, "block_added", f"app blocked: {app_id}", target=app_id, detail={"match": payload.match})
    return DeviceAppBlockOut(id=row.id, app_id=row.app_id, match=row.match, source=row.source)


@router.delete("/blocklist/{block_id}")
def remove_block(block_id: str, user: PresentUser, db: DbSession) -> dict:
    _require_device()
    row = db.get(DeviceAppBlock, block_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not on the blocked list")
    app_id = row.app_id
    db.delete(row)
    db.commit()
    changes.record(db, user, "block_removed", f"app no longer blocked: {app_id}", widens=True, target=app_id)
    return {"removed": True}
