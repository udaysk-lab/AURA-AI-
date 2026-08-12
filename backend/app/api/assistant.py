"""Briefing, memory, automations, approvals, notifications, activity, settings."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.agent.coordinator import resolve_pending
from app.automation import engine as automation_engine
from app.deps import CurrentUser, DbSession, get_user_settings
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
    NotificationOut,
    PendingActionOut,
    PendingDecisionIn,
    SettingsIn,
    SettingsOut,
    TaskOut,
)
from app.services import briefing as briefing_service
from app.services import demo as demo_service
from app.services import memory as memory_service

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


@router.post("/automations", response_model=AutomationOut)
def create_automation(payload: AutomationIn, user: CurrentUser, db: DbSession) -> AutomationOut:
    rule = automation_engine.create_rule(db, user, payload.natural_language, payload.enabled)
    return AutomationOut.model_validate(rule)


@router.patch("/automations/{rule_id}", response_model=AutomationOut)
def update_automation(
    rule_id: str, payload: AutomationPatch, user: CurrentUser, db: DbSession
) -> AutomationOut:
    rule = db.get(AutomationRule, rule_id)
    if not rule or rule.user_id != user.id:
        raise HTTPException(status_code=404, detail="Automation not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(rule, key, value)
    db.commit()
    db.refresh(rule)
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


@router.get("/pending-actions", response_model=list[PendingActionOut])
def list_pending(user: CurrentUser, db: DbSession) -> list[PendingActionOut]:
    rows = db.scalars(
        select(PendingAction)
        .where(PendingAction.user_id == user.id, PendingAction.status == "pending")
        .order_by(PendingAction.created_at.desc())
    ).all()
    return [PendingActionOut.model_validate(a) for a in rows]


@router.post("/pending-actions/{action_id}")
def decide_pending(
    action_id: str, payload: PendingDecisionIn, user: CurrentUser, db: DbSession
) -> dict:
    """decision: reject | once | always | window (window uses `minutes`)."""
    action = db.get(PendingAction, action_id)
    if not action or action.user_id != user.id:
        raise HTTPException(status_code=404, detail="Action not found")
    return resolve_pending(db, user, action, payload.decision, minutes=payload.minutes)


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
