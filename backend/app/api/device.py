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

from fastapi import APIRouter, HTTPException, Query, Request

from app.agent import skills as skill_registry
from app.config import settings
from app.device import files as device_files
from app.device import policy as device_policy
from app.device import shell as device_shell
from app.device import system as device_system
from app.device.runtime import DeviceUnavailable, capabilities
from app.deps import CurrentUser, DbSession, is_remote_session
from app.models import ActivityLog
from app.schemas import (
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
def device_status(user: CurrentUser, db: DbSession) -> DeviceStatusOut:
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

    return DeviceStatusOut(
        **{k: v for k, v in data.items() if k in DeviceStatusOut.model_fields},
        policy=out_policy,
        active_skills=active,
    )


@router.post("/run", response_model=DeviceRunOut)
def run_command(
    payload: DeviceRunIn, request: Request, user: CurrentUser, db: DbSession
) -> DeviceRunOut:
    """Run a command from the Device console.

    This is the user's own terminal inside their own app, so there's no
    approval card — they typed it. The denylist floor in the shell layer still
    applies, and it lands in the activity log either way.

    Not from a paired phone: a lost phone shouldn't be a shell on this
    computer. From there, ask AURA in chat, where commands go through the
    trust tier and the approval cards like everything else.
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
    try:
        result = device_shell.run(payload.command, payload.workdir or None, payload.timeout)
    except DeviceUnavailable as exc:
        result = {"command": payload.command, "error": str(exc)}

    db.add(
        ActivityLog(
            user_id=user.id,
            actor="user",
            action="device_console",
            target=payload.command[:300],
            status="error" if result.get("error") or not result.get("ok") else "success",
            detail={"exit_code": result.get("exit_code")},
        )
    )
    db.commit()
    return DeviceRunOut(**{k: v for k, v in result.items() if k in DeviceRunOut.model_fields})


@router.get("/files", response_model=DeviceListingOut)
def list_files(
    user: CurrentUser,
    path: str = Query(default="~"),
    show_hidden: bool = Query(default=False),
) -> DeviceListingOut:
    """Browse a folder inside the allowed roots."""
    _require_device()
    try:
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
