"""Briefing, memory, automations, approvals, notifications, activity, settings."""

from __future__ import annotations

from datetime import timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.automation import engine as automation_engine
from app.deps import CurrentUser, DbSession, PresentUser, get_user_settings, guard_local_caller, is_remote_session, token_amr
from app.models import (
    ActivityLog,
    AutomationRule,
    Memory,
    Notification,
    PendingAction,
    utcnow,
)
from app.schemas import (
    ActivityOut,
    AutomationIn,
    AutomationOut,
    AutomationPatch,
    BriefingOut,
    EmailOut,
    EventOut,
    MemoryIn,
    MemoryOut,
    MemoryPatch,
    MemoryResetIn,
    MemoryResetOut,
    MemorySettingsIn,
    MemorySettingsOut,
    NotificationOut,
    PendingActionOut,
    PendingDecisionIn,
    SettingsIn,
    SettingsOut,
    TaskOut,
)
from app.services import briefing as briefing_service
from app.services import demo as demo_service
from app.permissions import approvals, changes as perm_changes, events as permission_events, reviewer
from app.security import decode_access_token
from app.services import memory as memory_service
from app.services import preferences as preference_service
from app.services import workmemory as workmemory_service

router = APIRouter(prefix="/api", tags=["assistant"])


# ---------------------------------------------------------------------------
# Briefing / dashboard
# ---------------------------------------------------------------------------


@router.get("/briefing", response_model=BriefingOut)
def briefing(user: CurrentUser, db: DbSession) -> BriefingOut:
    data = briefing_service.build_briefing(db, user)
    return BriefingOut(
        generated_at=data["generated_at"],
        greeting=data["greeting"],
        headline=data["headline"],
        meetings=[EventOut.model_validate(e) for e in data["meetings"]],
        tasks_due=[TaskOut.model_validate(t) for t in data["tasks_due"]],
        urgent_emails=[EmailOut.model_validate(e) for e in data["urgent_emails"]],
        suggested_priorities=data["suggested_priorities"],
        inbox=data["inbox"],
    )


@router.get("/activity", response_model=list[ActivityOut])
def activity(
    user: CurrentUser, db: DbSession, limit: int = Query(30, ge=1, le=200)
) -> list[ActivityOut]:
    rows = db.scalars(
        select(ActivityLog)
        .where(ActivityLog.user_id == user.id)
        .order_by(ActivityLog.created_at.desc())
        .limit(limit)
    ).all()
    return [ActivityOut.model_validate(a) for a in rows]


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


@router.get("/notifications", response_model=list[NotificationOut])
def notifications(user: CurrentUser, db: DbSession) -> list[NotificationOut]:
    rows = db.scalars(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(50)
    ).all()
    return [NotificationOut.model_validate(n) for n in rows]


@router.post("/notifications/read-all")
def mark_all_read(user: CurrentUser, db: DbSession) -> dict:
    rows = db.scalars(
        select(Notification).where(
            Notification.user_id == user.id, Notification.read == False  # noqa: E712
        )
    ).all()
    for n in rows:
        n.read = True
    db.commit()
    return {"marked": len(rows)}


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


@router.get("/memories", response_model=list[MemoryOut])
def list_memories(
    user: CurrentUser, db: DbSession, kind: str = Query("")
) -> list[MemoryOut]:
    stmt = select(Memory).where(Memory.user_id == user.id)
    if kind:
        stmt = stmt.where(Memory.kind == kind)
    rows = db.scalars(
        stmt.order_by(Memory.pinned.desc(), Memory.created_at.desc())
    ).all()
    return [MemoryOut.model_validate(m) for m in rows]


@router.post("/memories", response_model=MemoryOut)
def create_memory(payload: MemoryIn, user: CurrentUser, db: DbSession) -> MemoryOut:
    m = memory_service.remember(
        db, user.id, payload.content, kind=payload.kind,
        source="manual", confidence=1.0, pinned=payload.pinned,
    )
    return MemoryOut.model_validate(m)


@router.get("/memories/search", response_model=list[MemoryOut])
def search_memories(
    user: CurrentUser, db: DbSession, q: str = Query(..., min_length=1)
) -> list[MemoryOut]:
    hits = memory_service.search(db, user.id, q, limit=10)
    return [MemoryOut.model_validate(h.memory) for h in hits]


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: str, user: CurrentUser, db: DbSession) -> dict:
    m = db.get(Memory, memory_id)
    if not m or m.user_id != user.id:
        raise HTTPException(status_code=404, detail="Memory not found")
    db.delete(m)
    db.commit()
    return {"message": "Forgotten"}


@router.patch("/memories/{memory_id}/pin", response_model=MemoryOut)
def pin_memory(memory_id: str, user: CurrentUser, db: DbSession) -> MemoryOut:
    m = db.get(Memory, memory_id)
    if not m or m.user_id != user.id:
        raise HTTPException(status_code=404, detail="Memory not found")
    m.pinned = not m.pinned
    db.commit()
    db.refresh(m)
    return MemoryOut.model_validate(m)


@router.patch("/memories/{memory_id}", response_model=MemoryOut)
def edit_memory(memory_id: str, payload: MemoryPatch, user: CurrentUser, db: DbSession) -> MemoryOut:
    """The owner's edit of a fact: content, kind, pinned. The row becomes `manual`, so compaction
    never merges or decays it afterwards."""
    m = db.get(Memory, memory_id)
    if not m or m.user_id != user.id:
        raise HTTPException(status_code=404, detail="Memory not found")
    try:
        memory_service.edit(db, m, **payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return MemoryOut.model_validate(m)


@router.get("/memory/settings", response_model=MemorySettingsOut)
def get_memory_settings(user: CurrentUser, db: DbSession) -> MemorySettingsOut:
    return MemorySettingsOut(paused=bool(get_user_settings(db, user).memory_paused))


@router.patch("/memory/settings", response_model=MemorySettingsOut)
def set_memory_settings(payload: MemorySettingsIn, user: CurrentUser, db: DbSession) -> MemorySettingsOut:
    """Pause memory: no new preferences or facts from chat, new tasks are not kept for recall.
    What is already stored is still used. Only the signed-in owner can change it."""
    row = get_user_settings(db, user)
    row.memory_paused = payload.paused
    db.commit()
    return MemorySettingsOut(paused=bool(row.memory_paused))


@router.post("/memory/reset", response_model=MemoryResetOut)
def reset_memory(payload: MemoryResetIn, user: CurrentUser, db: DbSession) -> MemoryResetOut:
    """Wipe one kind of memory, or all of it. The caller has to send confirm = "RESET"."""
    if payload.confirm != "RESET":
        raise HTTPException(status_code=422, detail='Type RESET to confirm. This cannot be undone.')
    deleted = {"preferences": 0, "work_items": 0, "memories": 0}
    if payload.scope in ("preferences", "all"):
        deleted["preferences"] = preference_service.reset(db, user.id)
    if payload.scope in ("task_memory", "all"):
        deleted["work_items"] = workmemory_service.reset(db, user.id)
    if payload.scope in ("facts", "all"):
        rows = db.scalars(select(Memory).where(Memory.user_id == user.id)).all()
        for row in rows:
            db.delete(row)
        db.commit()
        deleted["memories"] = len(rows)
    return MemoryResetOut(deleted=deleted)


# ---------------------------------------------------------------------------
# Automations
# ---------------------------------------------------------------------------


@router.get("/automations", response_model=list[AutomationOut])
def list_automations(user: CurrentUser, db: DbSession) -> list[AutomationOut]:
    rows = db.scalars(
        select(AutomationRule)
        .where(AutomationRule.user_id == user.id)
        .order_by(AutomationRule.created_at.desc())
    ).all()
    return [AutomationOut.model_validate(r) for r in rows]


def _check_mode(mode: str | None) -> None:
    """`auto` sends each approval to the safety reviewer, which needs a connected model."""
    if mode == "auto" and not reviewer.available():
        raise HTTPException(
            status_code=409,
            detail="Auto needs a connected AI model. The built-in demo model can't review actions.",
        )


@router.post("/automations", response_model=AutomationOut)
def create_automation(payload: AutomationIn, user: PresentUser, db: DbSession) -> AutomationOut:
    _check_mode(payload.approval_mode)
    try:
        rule, warnings = automation_engine.create_rule_checked(
            db, user, payload.natural_language, payload.enabled, approval_mode=payload.approval_mode
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    out = AutomationOut.model_validate(rule)
    out.warnings = warnings
    if rule.approval_mode == "auto":
        perm_changes.record(db, user, "schedule_mode_changed", f"automation “{rule.name}” set to Auto", widens=True,
                       target=f"automation:{rule.id}", detail={"kind": "automation", "from": "ask", "to": "auto"})
    return out


@router.patch("/automations/{rule_id}", response_model=AutomationOut)
def update_automation(
    rule_id: str, payload: AutomationPatch, user: PresentUser, db: DbSession
) -> AutomationOut:
    rule = db.get(AutomationRule, rule_id)
    if not rule or rule.user_id != user.id:
        raise HTTPException(status_code=404, detail="Automation not found")
    changes = payload.model_dump(exclude_unset=True)
    old_mode = rule.approval_mode
    if changes.get("approval_mode") is None:
        changes.pop("approval_mode", None)
    elif changes["approval_mode"] != old_mode:
        _check_mode(changes["approval_mode"])
    for key, value in changes.items():
        setattr(rule, key, value)
    db.commit()
    db.refresh(rule)
    if rule.approval_mode != old_mode:
        perm_changes.record(
            db, user, "schedule_mode_changed", f"automation “{rule.name}” approval mode: {old_mode} to {rule.approval_mode}",
            widens=rule.approval_mode == "auto", target=f"automation:{rule.id}",
            detail={"kind": "automation", "from": old_mode, "to": rule.approval_mode},
        )
    return AutomationOut.model_validate(rule)


@router.delete("/automations/{rule_id}")
def delete_automation(rule_id: str, user: CurrentUser, db: DbSession) -> dict:
    rule = db.get(AutomationRule, rule_id)
    if not rule or rule.user_id != user.id:
        raise HTTPException(status_code=404, detail="Automation not found")
    db.delete(rule)
    db.commit()
    return {"message": "Deleted"}


@router.post("/automations/{rule_id}/run")
def run_automation(rule_id: str, user: CurrentUser, db: DbSession) -> dict:
    rule = db.get(AutomationRule, rule_id)
    if not rule or rule.user_id != user.id:
        raise HTTPException(status_code=404, detail="Automation not found")
    return automation_engine.run_rule(db, user, rule, {"subject": rule.name})


@router.post("/automations/tick")
def tick(user: CurrentUser, db: DbSession) -> dict:
    """Fire any scheduled rules that are due. Call from a cron/worker each minute."""
    results = automation_engine.run_due_schedules(db)
    results += automation_engine.on_task_due(db, user)
    return {"fired": len(results), "results": results}


# ---------------------------------------------------------------------------
# Pending actions (confirmation gate)
# ---------------------------------------------------------------------------


def _pending_out(action: PendingAction) -> PendingActionOut:
    """The client's `PendingAction`: the row, with the options a card always has and no internals."""
    out = PendingActionOut.model_validate(action)
    out.options = list(approvals.default_options(action))
    out.card_payload = {k: v for k, v in (action.card_payload or {}).items() if k != "rule_draft"}
    out.card = action.card or "tool"
    if action.expires_at is not None and action.expires_at.tzinfo is None:
        out.expires_at = action.expires_at.replace(tzinfo=timezone.utc)
    return out


def _caller(request: Request) -> approvals.Caller:
    """Who is answering: a paired device, the keyless local login (`amr` = local-open), or the owner."""
    if is_remote_session(request):
        return approvals.Caller("remote", str(getattr(request.state, "remote_device_id", "") or ""))
    if token_amr(request) == "local-open":
        return approvals.Caller("local-open")
    return approvals.Caller("session")


@router.get("/pending-actions", response_model=list[PendingActionOut])
def list_pending(user: CurrentUser, db: DbSession) -> list[PendingActionOut]:
    approvals.expire_due(db, user.id)  # lazily: anything overdue is settled before it is listed
    rows = db.scalars(
        select(PendingAction)
        .where(PendingAction.user_id == user.id, PendingAction.status == "pending")
        .order_by(PendingAction.created_at.desc())
    ).all()
    return [_pending_out(a) for a in rows]


@router.get("/pending-actions/{action_id}", response_model=PendingActionOut)
def get_pending(action_id: str, user: CurrentUser, db: DbSession) -> PendingActionOut:
    action = db.get(PendingAction, action_id)
    if not action or action.user_id != user.id:
        raise HTTPException(status_code=404, detail="Action not found")
    approvals.expire_if_due(db, action)
    return _pending_out(action)


@router.post("/pending-actions/{action_id}")
def decide_pending(
    action_id: str, payload: PendingDecisionIn, request: Request, user: CurrentUser, db: DbSession
):
    """decision: once | window | always | reject (approve on folder / app cards).

    Answers the card, runs the approved tool inside this request and returns its result, plus
    `work_item` ({id, status}; status `ready` means the paused task can be continued). A second
    answer gets 409 and changes nothing. A paired device or the terminal login may answer once or
    no only; the always-ask cards need the computer itself.
    """
    # Any answer, "no" included, must come from the person: not from a program AURA started, and
    # not within seconds of AURA's own clicks (spec 6.3 and 8.5).
    guard_local_caller(request, db, user, f"answer approval {action_id}")
    try:
        return approvals.resolve(db, user, action_id, payload.decision, _caller(request))
    except approvals.ApprovalError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@router.get("/settings", response_model=SettingsOut)
def get_settings_route(user: CurrentUser, db: DbSession) -> SettingsOut:
    return SettingsOut.model_validate(get_user_settings(db, user))


@router.patch("/settings", response_model=SettingsOut)
def update_settings(payload: SettingsIn, user: CurrentUser, db: DbSession) -> SettingsOut:
    row = get_user_settings(db, user)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return SettingsOut.model_validate(row)


@router.post("/settings/seed-demo")
def seed_demo(user: CurrentUser, db: DbSession) -> dict:
    return demo_service.seed_user(db, user)


@router.get("/settings/demo-data")
def demo_data_status(user: CurrentUser, db: DbSession) -> dict:
    """Whether the sample workspace is still there, and how much of it."""
    return demo_service.sample_data_status(db, user)


@router.delete("/settings/demo-data")
def clear_demo_data(user: CurrentUser, db: DbSession) -> dict:
    """Remove the sample emails, events, tasks and the rest — nothing else.

    demo.py has always promised this endpoint; it didn't exist, so sample data,
    once seeded, could only be removed by deleting the whole account.
    """
    return demo_service.clear_user(db, user)
