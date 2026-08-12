"""Email, calendar and task endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import or_, select

from app.agent.tools import parse_when
from app.automation import engine as automation_engine
from app.deps import CurrentUser, DbSession
from app.models import CalendarEvent, EmailMessage, Task, utcnow
from app.schemas import (
    ConflictOut,
    DraftReplyIn,
    DraftReplyOut,
    EmailOut,
    EventIn,
    EventOut,
    FreeSlot,
    InboxSummaryOut,
    TaskIn,
    TaskOut,
    TaskPatch,
)
from app.services import briefing as briefing_service
from app.services import google as google_service
from app.services import triage as triage_service

router = APIRouter(prefix="/api", tags=["workspace"])


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


@router.get("/emails", response_model=list[EmailOut])
def list_emails(
    user: CurrentUser,
    db: DbSession,
    folder: str = Query("inbox", pattern="^(inbox|unread|important|archive)$"),
    q: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
) -> list[EmailOut]:
    stmt = select(EmailMessage).where(EmailMessage.user_id == user.id)
    if folder == "archive":
        stmt = stmt.where(EmailMessage.is_archived == True)  # noqa: E712
    else:
        stmt = stmt.where(EmailMessage.is_archived == False)  # noqa: E712
    if folder == "unread":
        stmt = stmt.where(EmailMessage.is_read == False)  # noqa: E712
    if folder == "important":
        stmt = stmt.where(EmailMessage.importance.in_(["urgent", "high"]))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                EmailMessage.subject.ilike(like),
                EmailMessage.sender.ilike(like),
                EmailMessage.sender_name.ilike(like),
                EmailMessage.body.ilike(like),
            )
        )
    rows = db.scalars(stmt.order_by(EmailMessage.received_at.desc()).limit(limit)).all()
    return [EmailOut.model_validate(e) for e in rows]


@router.get("/emails/summary", response_model=InboxSummaryOut)
def inbox_summary(user: CurrentUser, db: DbSession) -> InboxSummaryOut:
    data = briefing_service.summarize_inbox(db, user.id)
    data.pop("rows", None)
    return InboxSummaryOut(**data)


@router.get("/emails/{email_id}", response_model=EmailOut)
def get_email(email_id: str, user: CurrentUser, db: DbSession) -> EmailOut:
    email = db.get(EmailMessage, email_id)
    if not email or email.user_id != user.id:
        raise HTTPException(status_code=404, detail="Email not found")
    if not email.is_read:
        email.is_read = True
        db.commit()
    return EmailOut.model_validate(email)


@router.post("/emails/{email_id}/archive", response_model=EmailOut)
def archive(email_id: str, user: CurrentUser, db: DbSession) -> EmailOut:
    email = db.get(EmailMessage, email_id)
    if not email or email.user_id != user.id:
        raise HTTPException(status_code=404, detail="Email not found")
    google_service.archive_email(db, user, email.external_id)
    email.is_archived = True
    db.commit()
    db.refresh(email)
    return EmailOut.model_validate(email)


@router.post("/emails/draft-reply", response_model=DraftReplyOut)
def draft_reply(payload: DraftReplyIn, user: CurrentUser, db: DbSession) -> DraftReplyOut:
    email = db.get(EmailMessage, payload.email_id)
    if not email or email.user_id != user.id:
        raise HTTPException(status_code=404, detail="Email not found")
    return DraftReplyOut(**briefing_service.draft_reply(db, user, email, payload.instruction))


@router.post("/emails/sync")
def sync_email(user: CurrentUser, db: DbSession) -> dict:
    before = {e.id for e in db.scalars(
        select(EmailMessage).where(EmailMessage.user_id == user.id)
    ).all()}

    result = google_service.sync_all(db, user)
    triage_service.triage_pending(db, user.id)

    # Fire email_received automations for anything genuinely new.
    fresh = db.scalars(
        select(EmailMessage).where(EmailMessage.user_id == user.id)
    ).all()
    fired = 0
    for email in fresh:
        if email.id not in before:
            automation_engine.on_email_received(db, user, email)
            fired += 1

    return {**result, "automations_triggered": fired}


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


@router.get("/events", response_model=list[EventOut])
def list_events(
    user: CurrentUser,
    db: DbSession,
    days: int = Query(14, ge=1, le=120),
    day_offset: int = Query(0, ge=-30, le=120),
) -> list[EventOut]:
    start = (datetime.now(timezone.utc) + timedelta(days=day_offset)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(days=days)
    rows = db.scalars(
        select(CalendarEvent)
        .where(CalendarEvent.user_id == user.id)
        .order_by(CalendarEvent.start_at)
    ).all()
    return [
        EventOut.model_validate(e)
        for e in rows
        if start <= (_aware(e.start_at) or start) < end
    ]


@router.post("/events", response_model=EventOut)
def create_event(payload: EventIn, user: CurrentUser, db: DbSession) -> EventOut:
    if payload.end_at <= payload.start_at:
        raise HTTPException(status_code=422, detail="end_at must be after start_at")
    event = CalendarEvent(
        user_id=user.id,
        title=payload.title,
        description=payload.description,
        location=payload.location,
        start_at=payload.start_at,
        end_at=payload.end_at,
        attendees=payload.attendees,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    try:
        remote_id = google_service.push_event(db, user, event)
        if remote_id:
            event.external_id = remote_id
            event.source = "google"
            db.commit()
            db.refresh(event)
    except Exception:
        pass  # local event still stands; sync will reconcile
    return EventOut.model_validate(event)


@router.delete("/events/{event_id}")
def delete_event(event_id: str, user: CurrentUser, db: DbSession) -> dict:
    event = db.get(CalendarEvent, event_id)
    if not event or event.user_id != user.id:
        raise HTTPException(status_code=404, detail="Event not found")
    google_service.delete_event_remote(db, user, event.external_id)
    db.delete(event)
    db.commit()
    return {"message": "Deleted"}


@router.get("/events/free-slots", response_model=list[FreeSlot])
def free_slots(
    user: CurrentUser,
    db: DbSession,
    duration_minutes: int = Query(30, ge=15, le=480),
    days: int = Query(5, ge=1, le=21),
) -> list[FreeSlot]:
    from app.agent.coordinator import execute_tool

    slots = execute_tool(
        db, user, "find_free_time",
        {"duration_minutes": duration_minutes, "days": days},
    )
    return [FreeSlot(**s) for s in slots] if isinstance(slots, list) else []


@router.get("/events/conflicts", response_model=list[ConflictOut])
def conflicts(user: CurrentUser, db: DbSession) -> list[ConflictOut]:
    now = datetime.now(timezone.utc)
    rows = sorted(
        [
            e for e in db.scalars(
                select(CalendarEvent).where(CalendarEvent.user_id == user.id)
            ).all()
            if _aware(e.start_at) and _aware(e.start_at) >= now - timedelta(days=1)
        ],
        key=lambda e: _aware(e.start_at),
    )
    out: list[ConflictOut] = []
    for i in range(len(rows) - 1):
        a, b = rows[i], rows[i + 1]
        a_end, b_start = _aware(a.end_at), _aware(b.start_at)
        if a_end and b_start and b_start < a_end:
            out.append(
                ConflictOut(
                    event_a=EventOut.model_validate(a),
                    event_b=EventOut.model_validate(b),
                    overlap_minutes=int((a_end - b_start).total_seconds() // 60),
                )
            )
    return out


@router.get("/events/{event_id}/brief")
def meeting_brief(event_id: str, user: CurrentUser, db: DbSession) -> dict:
    from app.agent.coordinator import execute_tool

    result = execute_tool(db, user, "prepare_meeting", {"event_id": event_id})
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    user: CurrentUser,
    db: DbSession,
    status: str = Query("all", pattern="^(all|todo|doing|done)$"),
) -> list[TaskOut]:
    stmt = select(Task).where(Task.user_id == user.id)
    if status != "all":
        stmt = stmt.where(Task.status == status)
    rows = db.scalars(stmt).all()
    for t in rows:
        t.ai_score = briefing_service.score_task(t)
    db.commit()
    rows = sorted(rows, key=lambda t: (t.status == "done", -t.ai_score))
    return [TaskOut.model_validate(t) for t in rows]


@router.post("/tasks", response_model=TaskOut)
def create_task(payload: TaskIn, user: CurrentUser, db: DbSession) -> TaskOut:
    task = Task(
        user_id=user.id,
        title=payload.title,
        notes=payload.notes,
        status=payload.status,
        priority=payload.priority,
        due_at=payload.due_at,
        recurrence=payload.recurrence,
        tags=payload.tags,
        parent_id=payload.parent_id,
        source="manual",
    )
    task.ai_score = briefing_service.score_task(task)
    db.add(task)
    db.commit()
    db.refresh(task)
    return TaskOut.model_validate(task)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: str, payload: TaskPatch, user: CurrentUser, db: DbSession) -> TaskOut:
    task = db.get(Task, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="Task not found")

    data = payload.model_dump(exclude_unset=True)
    was_done = task.status == "done"
    for key, value in data.items():
        setattr(task, key, value)

    if task.status == "done" and not was_done:
        task.completed_at = utcnow()
        if task.recurrence:
            step = {"daily": 1, "weekly": 7, "monthly": 30}[task.recurrence]
            base = _aware(task.due_at) or datetime.now(timezone.utc)
            db.add(
                Task(
                    user_id=user.id, title=task.title, notes=task.notes,
                    priority=task.priority, due_at=base + timedelta(days=step),
                    recurrence=task.recurrence, tags=task.tags, source=task.source,
                )
            )
    elif task.status != "done":
        task.completed_at = None

    task.ai_score = briefing_service.score_task(task)
    db.commit()
    db.refresh(task)
    return TaskOut.model_validate(task)


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, user: CurrentUser, db: DbSession) -> dict:
    task = db.get(Task, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="Task not found")
    db.delete(task)
    db.commit()
    return {"message": "Deleted"}


@router.post("/tasks/quick", response_model=TaskOut)
def quick_add(
    user: CurrentUser,
    db: DbSession,
    text: str = Query(..., min_length=1, max_length=500),
) -> TaskOut:
    """Natural-language quick capture: 'pay invoice tomorrow 5pm urgent'."""
    due = parse_when(text, default_hour=17)
    priority = "medium"
    lowered = text.lower()
    if "urgent" in lowered or "asap" in lowered:
        priority = "urgent"
    elif "important" in lowered or "high" in lowered:
        priority = "high"
    elif "sometime" in lowered or "low" in lowered:
        priority = "low"

    task = Task(
        user_id=user.id, title=text.strip()[:500], due_at=due,
        priority=priority, source="manual",
    )
    task.ai_score = briefing_service.score_task(task)
    db.add(task)
    db.commit()
    db.refresh(task)
    return TaskOut.model_validate(task)
