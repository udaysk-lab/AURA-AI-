"""Tool registry.

Every capability the agent has is a function here with a JSON schema. The
coordinator never special-cases a tool — it reads this registry, hands the specs
to the model, and dispatches whatever comes back. Adding a capability means
adding one decorated function and nothing else.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.llm import ToolSpec
from app.models import (
    ActivityLog,
    CalendarEvent,
    Contact,
    Delegation,
    Document,
    EmailMessage,
    Notification,
    Task,
    User,
    utcnow,
)
from app.services import briefing as briefing_service
from app.services import google as google_service
from app.services import memory as memory_service

log = logging.getLogger("aura.tools")


@dataclass
class ToolContext:
    db: Session
    user: User


@dataclass
class Tool:
    spec: ToolSpec
    handler: Callable[[ToolContext, dict], Any]
    destructive: bool
    preview: Callable[[dict], str] | None = None


REGISTRY: dict[str, Tool] = {}


def tool(
    name: str,
    description: str,
    parameters: dict | None = None,
    destructive: bool = False,
    preview: Callable[[dict], str] | None = None,
):
    def decorator(fn: Callable[[ToolContext, dict], Any]):
        REGISTRY[name] = Tool(
            spec=ToolSpec(
                name=name,
                description=description,
                parameters=parameters
                or {"type": "object", "properties": {}, "additionalProperties": False},
            ),
            handler=fn,
            destructive=destructive,
            preview=preview,
        )
        return fn

    return decorator


def tool_specs() -> list[ToolSpec]:
    return [t.spec for t in REGISTRY.values()]


# NOTE: whether a tool needs approval is decided by agent/autonomy.py, which
# owns the risk classes and trust tiers. The `destructive=True` flag on each tool
# below is documentation for the reader; don't add a second gate here, because
# two sources of truth for "is this dangerous" is how one of them ends up wrong.


def build_preview(name: str, args: dict) -> str:
    t = REGISTRY.get(name)
    if t and t.preview:
        try:
            return t.preview(args)
        except Exception:
            pass
    return f"{name}({', '.join(f'{k}={v!r}' for k, v in args.items())})"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RELATIVE = {
    "today": 0, "tomorrow": 1, "day after tomorrow": 2,
    "next week": 7, "next monday": None,  # handled below
}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_when(value: Any, default_hour: int = 9) -> datetime | None:
    """Parse ISO timestamps and common relative phrases into aware UTC datetimes."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    now = datetime.now(timezone.utc)
    lowered = text.lower()

    hour, minute = default_hour, 0
    time_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", lowered)
    if time_match:
        h = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        meridiem = time_match.group(3)
        if meridiem == "pm" and h < 12:
            h += 12
        elif meridiem == "am" and h == 12:
            h = 0
        if 0 <= h <= 23 and (meridiem or ":" in lowered or h > 7):
            hour = h

    offset: int | None = None
    for phrase, days in _RELATIVE.items():
        if days is not None and phrase in lowered:
            offset = days
            break
    if offset is None:
        for i, name in enumerate(_WEEKDAYS):
            if name in lowered:
                delta = (i - now.weekday()) % 7
                offset = delta or 7
                break
    if offset is None:
        if "next" in lowered:
            offset = 7
        elif "week" in lowered:
            offset = 7
        else:
            return None

    return (now + timedelta(days=offset)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _log(ctx: ToolContext, action: str, target: str = "", detail: dict | None = None,
         status: str = "success") -> None:
    ctx.db.add(
        ActivityLog(
            user_id=ctx.user.id, actor="agent", action=action,
            target=target[:300], status=status, detail=detail or {},
        )
    )
    ctx.db.commit()


def _event_dict(e: CalendarEvent) -> dict:
    return {
        "id": e.id, "title": e.title, "location": e.location,
        "start_at": _aware(e.start_at).isoformat() if e.start_at else None,
        "end_at": _aware(e.end_at).isoformat() if e.end_at else None,
        "attendees": e.attendees or [], "description": e.description,
    }


def _task_dict(t: Task) -> dict:
    return {
        "id": t.id, "title": t.title, "status": t.status, "priority": t.priority,
        "due_at": _aware(t.due_at).isoformat() if t.due_at else None,
        "tags": t.tags or [], "notes": t.notes,
    }


def _email_dict(e: EmailMessage, include_body: bool = False) -> dict:
    d = {
        "id": e.id, "from": f"{e.sender_name} <{e.sender}>", "subject": e.subject,
        "received_at": _aware(e.received_at).isoformat() if e.received_at else None,
        "importance": e.importance, "category": e.category, "is_read": e.is_read,
        "needs_reply": e.needs_reply, "summary": e.ai_summary,
        "action_items": e.action_items or [],
    }
    if include_body:
        d["body"] = e.body or e.snippet
    return d


# ---------------------------------------------------------------------------
# Email tools
# ---------------------------------------------------------------------------


@tool(
    "summarize_inbox",
    "Summarise the user's inbox: counts, what's urgent, and what is waiting on a reply. "
    "Use for any 'what's in my inbox' or 'catch me up on email' request.",
)
def _summarize_inbox(ctx: ToolContext, args: dict) -> dict:
    data = briefing_service.summarize_inbox(ctx.db, ctx.user.id)
    data.pop("rows", None)
    return data


@tool(
    "search_emails",
    "Search cached emails by sender, subject or body text. Also accepts filters for "
    "unread-only and importance.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Free text to match"},
            "unread_only": {"type": "boolean"},
            "importance": {
                "type": "string",
                "enum": ["urgent", "high", "normal", "low"],
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
    },
)
def _search_emails(ctx: ToolContext, args: dict) -> list[dict]:
    q = (args.get("query") or "").strip()
    stmt = select(EmailMessage).where(
        EmailMessage.user_id == ctx.user.id,
        EmailMessage.is_archived == False,  # noqa: E712
    )
    if q and q.lower() not in {"urgent", "unread", "all"}:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                EmailMessage.subject.ilike(like),
                EmailMessage.sender.ilike(like),
                EmailMessage.sender_name.ilike(like),
                EmailMessage.body.ilike(like),
                EmailMessage.snippet.ilike(like),
            )
        )
    if args.get("unread_only"):
        stmt = stmt.where(EmailMessage.is_read == False)  # noqa: E712
    importance = args.get("importance") or ("urgent" if q.lower() == "urgent" else None)
    if importance:
        stmt = stmt.where(EmailMessage.importance == importance)

    stmt = stmt.order_by(EmailMessage.received_at.desc()).limit(
        int(args.get("limit") or 10)
    )
    return [_email_dict(e) for e in ctx.db.scalars(stmt).all()]


@tool(
    "get_email",
    "Read one email in full, including its body.",
    {
        "type": "object",
        "properties": {"email_id": {"type": "string"}},
        "required": ["email_id"],
    },
)
def _get_email(ctx: ToolContext, args: dict) -> dict:
    email = ctx.db.get(EmailMessage, args.get("email_id", ""))
    if not email or email.user_id != ctx.user.id:
        return {"error": "Email not found"}
    return _email_dict(email, include_body=True)


@tool(
    "draft_reply",
    "Draft (but do not send) a reply to an email, matching the user's writing style.",
    {
        "type": "object",
        "properties": {
            "email_id": {"type": "string"},
            "instruction": {
                "type": "string",
                "description": "What the reply should say or achieve",
            },
        },
        "required": ["email_id"],
    },
)
def _draft_reply(ctx: ToolContext, args: dict) -> dict:
    email = ctx.db.get(EmailMessage, args.get("email_id", ""))
    if not email or email.user_id != ctx.user.id:
        return {"error": "Email not found"}
    draft = briefing_service.draft_reply(
        ctx.db, ctx.user, email,
        args.get("instruction") or "Write a concise, professional reply.",
    )
    _log(ctx, "draft_reply", email.subject, {"email_id": email.id})
    return {**draft, "to": email.sender, "email_id": email.id}


@tool(
    "send_email",
    "Send an email. Irreversible and externally visible — always requires user approval.",
    {
        "type": "object",
        "properties": {
            "to": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["to", "subject", "body"],
    },
    destructive=True,
    preview=lambda a: f"Send email to {a.get('to')}\nSubject: {a.get('subject')}\n\n{a.get('body', '')[:600]}",
)
def _send_email(ctx: ToolContext, args: dict) -> dict:
    from app.services import mailer

    result = mailer.send(ctx.db, ctx.user, args["to"], args["subject"], args["body"])
    _log(
        ctx, "send_email", args["to"],
        {k: v for k, v in result.items() if k != "error"},
        status="success" if result.get("sent") else "skipped",
    )

    if result.get("sent"):
        return {"message": f"Sent to {result['to']} via {result['route']}.", **result}

    reasons = {
        "no_route": (
            f"Nothing was sent — there's no send route configured. The draft to "
            f"{result['to']} is ready. Connect Google in Settings, or set SMTP_HOST."
        ),
        "invalid_recipient": f"{args.get('to')!r} isn't a valid email address.",
        "smtp_error": f"The mail server rejected it: {result.get('error', 'unknown error')}",
        "google_not_connected": (
            f"Google isn't connected, so nothing was sent. The draft to {result['to']} "
            "is ready — connect Google in Settings to send it."
        ),
    }
    return {
        "message": reasons.get(result.get("reason", ""), "Nothing was sent."),
        **result,
    }


@tool(
    "archive_email",
    "Archive an email (removes it from the inbox). Requires user approval.",
    {
        "type": "object",
        "properties": {"email_id": {"type": "string"}},
        "required": ["email_id"],
    },
    destructive=True,
    preview=lambda a: f"Archive email {a.get('email_id')}",
)
def _archive_email(ctx: ToolContext, args: dict) -> dict:
    email = ctx.db.get(EmailMessage, args.get("email_id", ""))
    if not email or email.user_id != ctx.user.id:
        return {"error": "Email not found"}
    google_service.archive_email(ctx.db, ctx.user, email.external_id)
    email.is_archived = True
    ctx.db.commit()
    _log(ctx, "archive_email", email.subject)
    return {"message": f"Archived: {email.subject}"}


# ---------------------------------------------------------------------------
# Calendar tools
# ---------------------------------------------------------------------------


@tool(
    "list_events",
    "List calendar events in a window. Defaults to the next 7 days.",
    {
        "type": "object",
        "properties": {
            "days": {"type": "integer", "minimum": 1, "maximum": 60},
            "day_offset": {
                "type": "integer",
                "description": "0 = today, 1 = tomorrow. Use with days=1 for a single day.",
            },
        },
    },
)
def _list_events(ctx: ToolContext, args: dict) -> list[dict]:
    days = int(args.get("days") or 7)
    offset = int(args.get("day_offset") or 0)
    start = (datetime.now(timezone.utc) + timedelta(days=offset)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(days=days)
    rows = ctx.db.scalars(
        select(CalendarEvent)
        .where(CalendarEvent.user_id == ctx.user.id, CalendarEvent.status != "cancelled")
        .order_by(CalendarEvent.start_at)
    ).all()
    return [
        _event_dict(e) for e in rows if start <= (_aware(e.start_at) or start) < end
    ]


@tool(
    "create_event",
    "Create a calendar event. Accepts ISO timestamps or phrases like 'tomorrow 3pm'.",
    {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "start_at": {"type": "string"},
            "duration_minutes": {"type": "integer", "minimum": 5, "maximum": 600},
            "end_at": {"type": "string"},
            "description": {"type": "string"},
            "location": {"type": "string"},
            "attendees": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title"],
    },
)
def _create_event(ctx: ToolContext, args: dict) -> dict:
    start = parse_when(args.get("start_at"), default_hour=10)
    if not start:
        start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
    end = parse_when(args.get("end_at")) or start + timedelta(
        minutes=int(args.get("duration_minutes") or 30)
    )

    event = CalendarEvent(
        user_id=ctx.user.id,
        title=args["title"],
        description=args.get("description", ""),
        location=args.get("location", ""),
        start_at=start,
        end_at=end,
        attendees=args.get("attendees") or [],
    )
    ctx.db.add(event)
    ctx.db.commit()
    ctx.db.refresh(event)

    try:
        remote_id = google_service.push_event(ctx.db, ctx.user, event)
        if remote_id:
            event.external_id = remote_id
            event.source = "google"
            ctx.db.commit()
    except Exception as exc:
        log.warning("Could not push event to Google: %s", exc)

    _log(ctx, "create_event", event.title, {"start_at": start.isoformat()})
    return {"message": f"Created '{event.title}' at {start.strftime('%a %d %b %H:%M')}",
            **_event_dict(event)}


@tool(
    "delete_event",
    "Delete a calendar event. Requires user approval.",
    {
        "type": "object",
        "properties": {"event_id": {"type": "string"}},
        "required": ["event_id"],
    },
    destructive=True,
    preview=lambda a: f"Delete calendar event {a.get('event_id')}",
)
def _delete_event(ctx: ToolContext, args: dict) -> dict:
    event = ctx.db.get(CalendarEvent, args.get("event_id", ""))
    if not event or event.user_id != ctx.user.id:
        return {"error": "Event not found"}
    title = event.title
    google_service.delete_event_remote(ctx.db, ctx.user, event.external_id)
    ctx.db.delete(event)
    ctx.db.commit()
    _log(ctx, "delete_event", title)
    return {"message": f"Deleted '{title}'"}


@tool(
    "find_free_time",
    "Find open slots in the user's calendar. Respects working hours (09:00-18:00 by default).",
    {
        "type": "object",
        "properties": {
            "duration_minutes": {"type": "integer", "minimum": 15, "maximum": 480},
            "days": {"type": "integer", "minimum": 1, "maximum": 21},
            "earliest_hour": {"type": "integer", "minimum": 0, "maximum": 23},
            "latest_hour": {"type": "integer", "minimum": 1, "maximum": 24},
        },
    },
)
def _find_free_time(ctx: ToolContext, args: dict) -> list[dict]:
    duration = timedelta(minutes=int(args.get("duration_minutes") or 30))
    days = int(args.get("days") or 5)
    earliest = int(args.get("earliest_hour") or 9)
    latest = int(args.get("latest_hour") or 18)

    now = datetime.now(timezone.utc)
    events = sorted(
        [
            e for e in ctx.db.scalars(
                select(CalendarEvent).where(
                    CalendarEvent.user_id == ctx.user.id,
                    CalendarEvent.status != "cancelled",
                )
            ).all()
        ],
        key=lambda e: _aware(e.start_at) or now,
    )

    slots: list[dict] = []
    for day in range(days):
        date = (now + timedelta(days=day)).date()
        if date.weekday() >= 5:  # skip weekends
            continue
        cursor = datetime.combine(date, datetime.min.time(), tzinfo=timezone.utc).replace(
            hour=earliest
        )
        day_end = cursor.replace(hour=latest - 1, minute=59)
        if cursor < now:
            cursor = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

        busy = [
            (_aware(e.start_at), _aware(e.end_at))
            for e in events
            if _aware(e.start_at) and _aware(e.start_at).date() == date
        ]
        for b_start, b_end in busy:
            if b_start - cursor >= duration:
                slots.append({"start_at": cursor.isoformat(),
                              "end_at": (cursor + duration).isoformat()})
            cursor = max(cursor, b_end)
        if day_end - cursor >= duration:
            slots.append({"start_at": cursor.isoformat(),
                          "end_at": (cursor + duration).isoformat()})
        if len(slots) >= 8:
            break
    return slots[:8]


@tool(
    "detect_conflicts",
    "Find overlapping calendar events (double-bookings) in the next 14 days.",
)
def _detect_conflicts(ctx: ToolContext, args: dict) -> list[dict]:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=14)
    events = sorted(
        [
            e for e in ctx.db.scalars(
                select(CalendarEvent).where(
                    CalendarEvent.user_id == ctx.user.id,
                    CalendarEvent.status != "cancelled",
                )
            ).all()
            if _aware(e.start_at) and now - timedelta(days=1) <= _aware(e.start_at) <= horizon
        ],
        key=lambda e: _aware(e.start_at),
    )

    conflicts = []
    for i in range(len(events) - 1):
        a, b = events[i], events[i + 1]
        a_end, b_start = _aware(a.end_at), _aware(b.start_at)
        if a_end and b_start and b_start < a_end:
            overlap = int((a_end - b_start).total_seconds() // 60)
            conflicts.append(
                {
                    "overlap_minutes": overlap,
                    "event_a": _event_dict(a),
                    "event_b": _event_dict(b),
                }
            )
    return conflicts


@tool(
    "prepare_meeting",
    "Build a briefing for a meeting: attendees, related emails, related tasks, and an agenda.",
    {
        "type": "object",
        "properties": {"event_id": {"type": "string"}},
        "required": ["event_id"],
    },
)
def _prepare_meeting(ctx: ToolContext, args: dict) -> dict:
    event = ctx.db.get(CalendarEvent, args.get("event_id", ""))
    if not event or event.user_id != ctx.user.id:
        return {"error": "Event not found"}

    attendees = event.attendees or []
    related_emails: list[dict] = []
    contacts: list[dict] = []
    for addr in attendees:
        rows = ctx.db.scalars(
            select(EmailMessage)
            .where(EmailMessage.user_id == ctx.user.id, EmailMessage.sender == addr)
            .order_by(EmailMessage.received_at.desc())
            .limit(3)
        ).all()
        related_emails.extend(_email_dict(e) for e in rows)

        c = ctx.db.scalars(
            select(Contact).where(Contact.user_id == ctx.user.id, Contact.email == addr)
        ).first()
        if c:
            contacts.append(
                {"name": c.name, "email": c.email, "company": c.company,
                 "role": c.role, "notes": c.notes}
            )

    keyword = event.title.split("—")[0].strip()
    related_tasks = ctx.db.scalars(
        select(Task).where(
            Task.user_id == ctx.user.id,
            Task.status != "done",
            Task.title.ilike(f"%{keyword[:20]}%"),
        ).limit(5)
    ).all()

    hits = memory_service.search(ctx.db, ctx.user.id, f"{event.title} {' '.join(attendees)}", limit=4)

    return {
        "event": _event_dict(event),
        "contacts": contacts,
        "related_emails": related_emails,
        "related_tasks": [_task_dict(t) for t in related_tasks],
        "relevant_memories": [h.memory.content for h in hits],
        "suggested_agenda": [
            f"Recap since last contact ({len(related_emails)} recent email(s))",
            f"Main topic: {event.title}",
            "Open questions and blockers",
            "Decisions and owners",
            "Next steps and dates",
        ],
    }


# ---------------------------------------------------------------------------
# Task tools
# ---------------------------------------------------------------------------


@tool(
    "list_tasks",
    "List the user's tasks, optionally filtered by status, and ranked by urgency.",
    {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["todo", "doing", "done", "all"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
    },
)
def _list_tasks(ctx: ToolContext, args: dict) -> list[dict]:
    status = args.get("status") or "todo"
    stmt = select(Task).where(Task.user_id == ctx.user.id)
    if status != "all":
        stmt = stmt.where(Task.status == status)
    rows = ctx.db.scalars(stmt).all()
    for t in rows:
        t.ai_score = briefing_service.score_task(t)
    ctx.db.commit()
    rows = sorted(rows, key=lambda t: t.ai_score, reverse=True)[: int(args.get("limit") or 20)]
    return [{**_task_dict(t), "ai_score": t.ai_score} for t in rows]


@tool(
    "create_task",
    "Create a task. Use for anything the user needs to remember or do.",
    {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "notes": {"type": "string"},
            "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
            "due_at": {"type": "string", "description": "ISO timestamp or 'tomorrow 5pm'"},
            "recurrence": {"type": "string", "enum": ["", "daily", "weekly", "monthly"]},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title"],
    },
)
def _create_task(ctx: ToolContext, args: dict) -> dict:
    task = Task(
        user_id=ctx.user.id,
        title=args["title"][:500],
        notes=args.get("notes", ""),
        priority=args.get("priority") or "medium",
        due_at=parse_when(args.get("due_at"), default_hour=17),
        recurrence=args.get("recurrence") or "",
        tags=args.get("tags") or [],
        source="agent",
    )
    task.ai_score = briefing_service.score_task(task)
    ctx.db.add(task)
    ctx.db.commit()
    ctx.db.refresh(task)
    _log(ctx, "create_task", task.title)
    return {"message": f"Task created: {task.title}", **_task_dict(task)}


@tool(
    "complete_task",
    "Mark a task as done. Recurring tasks roll forward to their next occurrence.",
    {
        "type": "object",
        "properties": {"task_id": {"type": "string"}, "title": {"type": "string"}},
    },
)
def _complete_task(ctx: ToolContext, args: dict) -> dict:
    task = None
    if args.get("task_id"):
        task = ctx.db.get(Task, args["task_id"])
    if (not task or task.user_id != ctx.user.id) and args.get("title"):
        task = ctx.db.scalars(
            select(Task).where(
                Task.user_id == ctx.user.id,
                Task.status != "done",
                Task.title.ilike(f"%{args['title']}%"),
            )
        ).first()
    if not task or task.user_id != ctx.user.id:
        return {"error": "Task not found"}

    task.status = "done"
    task.completed_at = utcnow()

    if task.recurrence:
        step = {"daily": 1, "weekly": 7, "monthly": 30}[task.recurrence]
        base = _aware(task.due_at) or datetime.now(timezone.utc)
        ctx.db.add(
            Task(
                user_id=ctx.user.id, title=task.title, notes=task.notes,
                priority=task.priority, due_at=base + timedelta(days=step),
                recurrence=task.recurrence, tags=task.tags, source=task.source,
            )
        )
    ctx.db.commit()
    _log(ctx, "complete_task", task.title)
    return {"message": f"Completed: {task.title}"}


@tool(
    "delete_task",
    "Delete a task permanently. Requires user approval.",
    {
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
    },
    destructive=True,
    preview=lambda a: f"Delete task {a.get('task_id')}",
)
def _delete_task(ctx: ToolContext, args: dict) -> dict:
    task = ctx.db.get(Task, args.get("task_id", ""))
    if not task or task.user_id != ctx.user.id:
        return {"error": "Task not found"}
    title = task.title
    ctx.db.delete(task)
    ctx.db.commit()
    _log(ctx, "delete_task", title)
    return {"message": f"Deleted task: {title}"}


# ---------------------------------------------------------------------------
# Memory, contacts, notifications, briefing
# ---------------------------------------------------------------------------


@tool(
    "save_memory",
    "Store a durable fact about the user: a preference, a project detail, a decision, or "
    "how they like to communicate. Do not store transient one-off requests.",
    {
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "kind": {
                "type": "string",
                "enum": ["preference", "contact", "project", "decision", "style", "habit", "fact"],
            },
        },
        "required": ["content"],
    },
)
def _save_memory(ctx: ToolContext, args: dict) -> dict:
    m = memory_service.remember(
        ctx.db, ctx.user.id, args["content"],
        kind=args.get("kind") or "fact", source="chat", confidence=0.85,
    )
    _log(ctx, "save_memory", m.content[:80])
    return {"message": "Noted.", "id": m.id, "content": m.content, "kind": m.kind}


@tool(
    "search_memory",
    "Retrieve what the assistant knows about the user, by semantic similarity.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
    },
)
def _search_memory(ctx: ToolContext, args: dict) -> list[dict]:
    hits = memory_service.search(
        ctx.db, ctx.user.id, args["query"], limit=int(args.get("limit") or 6)
    )
    return [
        {"content": h.memory.content, "kind": h.memory.kind, "score": round(h.score, 3)}
        for h in hits
    ]


@tool(
    "list_contacts",
    "Look up the user's contacts by name, email or company.",
    {"type": "object", "properties": {"query": {"type": "string"}}},
)
def _list_contacts(ctx: ToolContext, args: dict) -> list[dict]:
    stmt = select(Contact).where(Contact.user_id == ctx.user.id)
    q = (args.get("query") or "").strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(Contact.name.ilike(like), Contact.email.ilike(like), Contact.company.ilike(like))
        )
    return [
        {"name": c.name, "email": c.email, "company": c.company,
         "role": c.role, "notes": c.notes}
        for c in ctx.db.scalars(stmt.limit(20)).all()
    ]


@tool(
    "notify",
    "Raise an in-app notification for the user.",
    {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
            "level": {"type": "string", "enum": ["info", "success", "warning", "urgent"]},
        },
        "required": ["title"],
    },
)
def _notify(ctx: ToolContext, args: dict) -> dict:
    ctx.db.add(
        Notification(
            user_id=ctx.user.id, title=args["title"][:300],
            body=args.get("body", ""), level=args.get("level") or "info",
        )
    )
    ctx.db.commit()
    return {"message": "Notification sent."}


@tool(
    "get_daily_briefing",
    "Produce the user's briefing: today's meetings, tasks due, urgent email, and "
    "suggested priorities. Use for 'what's my day look like' style requests.",
)
def _get_daily_briefing(ctx: ToolContext, args: dict) -> dict:
    data = briefing_service.build_briefing(ctx.db, ctx.user)
    return {
        "greeting": data["greeting"],
        "headline": data["headline"],
        "meetings": [_event_dict(e) for e in data["meetings"]],
        "tasks_due": [_task_dict(t) for t in data["tasks_due"]],
        "urgent_emails": [_email_dict(e) for e in data["urgent_emails"]],
        "suggested_priorities": data["suggested_priorities"],
        "inbox_summary": data["inbox"]["summary"],
    }


@tool(
    "sync_google",
    "Pull the latest email and calendar data from the user's connected Google account.",
)
def _sync_google(ctx: ToolContext, args: dict) -> dict:
    result = google_service.sync_all(ctx.db, ctx.user)
    if not result["connected"]:
        return {"message": "Google isn't connected. Connect it in Settings to sync real data."}
    return {"message": f"Synced {result['emails']} emails and {result['events']} events.", **result}


# ---------------------------------------------------------------------------
# Research (Research plugin)
# ---------------------------------------------------------------------------


@tool(
    "web_research",
    "Search the web and return a sourced answer. Use for anything about the outside "
    "world the user's own data can't answer.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    },
)
def _web_research(ctx: ToolContext, args: dict) -> dict:
    from app.services import research as research_service

    result = research_service.research(args["query"], limit=int(args.get("limit") or 6))
    _log(ctx, "web_research", args["query"][:80], {"sources": len(result.get("sources", []))})
    return result


@tool(
    "brief_subject",
    "Build a dossier on a person or company, combining the web with the user's own "
    "inbox, contacts and documents. Use before meetings and intro calls.",
    {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "Person name, company or email"},
        },
        "required": ["subject"],
    },
)
def _brief_subject(ctx: ToolContext, args: dict) -> dict:
    from app.services import documents as document_service
    from app.services import research as research_service

    subject = args["subject"].strip()
    like = f"%{subject}%"

    contacts = ctx.db.scalars(
        select(Contact).where(
            Contact.user_id == ctx.user.id,
            or_(Contact.name.ilike(like), Contact.email.ilike(like), Contact.company.ilike(like)),
        ).limit(5)
    ).all()

    emails = ctx.db.scalars(
        select(EmailMessage)
        .where(
            EmailMessage.user_id == ctx.user.id,
            or_(
                EmailMessage.sender.ilike(like),
                EmailMessage.sender_name.ilike(like),
                EmailMessage.subject.ilike(like),
            ),
        )
        .order_by(EmailMessage.received_at.desc())
        .limit(5)
    ).all()

    memories = memory_service.search(ctx.db, ctx.user.id, subject, limit=4)
    passages = document_service.search(ctx.db, ctx.user.id, subject, limit=3)

    web = research_service.research(
        f"{subject} company background recent news", limit=5
    )

    _log(ctx, "brief_subject", subject[:80])
    return {
        "subject": subject,
        "contacts": [
            {"name": c.name, "email": c.email, "company": c.company,
             "role": c.role, "notes": c.notes}
            for c in contacts
        ],
        "recent_emails": [_email_dict(e) for e in emails],
        "known_facts": [h.memory.content for h in memories],
        "document_passages": passages,
        "web_summary": web.get("summary", ""),
        "sources": web.get("sources", []),
        "search_note": web.get("error", ""),
    }


# ---------------------------------------------------------------------------
# Documents (Documents plugin)
# ---------------------------------------------------------------------------


@tool(
    "search_documents",
    "Search the user's uploaded documents by meaning and return the passages that apply.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    },
)
def _search_documents(ctx: ToolContext, args: dict) -> list[dict]:
    from app.services import documents as document_service

    return document_service.search(
        ctx.db, ctx.user.id, args["query"], limit=int(args.get("limit") or 5)
    )


@tool(
    "summarize_document",
    "Summarise one uploaded document, leading with what the user must decide or do.",
    {
        "type": "object",
        "properties": {
            "document_id": {"type": "string"},
            "title": {"type": "string", "description": "Match by title if no id is known"},
        },
    },
)
def _summarize_document(ctx: ToolContext, args: dict) -> dict:
    from app.services import documents as document_service

    document = None
    if args.get("document_id"):
        document = ctx.db.get(Document, args["document_id"])
    if (not document or document.user_id != ctx.user.id) and args.get("title"):
        document = ctx.db.scalars(
            select(Document).where(
                Document.user_id == ctx.user.id,
                Document.title.ilike(f"%{args['title']}%"),
            )
        ).first()
    if not document or document.user_id != ctx.user.id:
        return {"error": "Document not found"}

    return {
        "id": document.id,
        "title": document.title,
        "summary": document_service.summarise(ctx.db, ctx.user.id, document),
    }


@tool(
    "compare_documents",
    "Compare two uploaded documents and report only the material differences.",
    {
        "type": "object",
        "properties": {
            "left_id": {"type": "string"},
            "right_id": {"type": "string"},
        },
        "required": ["left_id", "right_id"],
    },
)
def _compare_documents(ctx: ToolContext, args: dict) -> dict:
    from app.services import documents as document_service

    left = ctx.db.get(Document, args["left_id"])
    right = ctx.db.get(Document, args["right_id"])
    if not left or not right or left.user_id != ctx.user.id or right.user_id != ctx.user.id:
        return {"error": "One or both documents not found"}
    return document_service.compare(ctx.db, ctx.user.id, left, right)


# ---------------------------------------------------------------------------
# Delegation (Delegation plugin)
# ---------------------------------------------------------------------------


@tool(
    "delegate_task",
    "Record something handed to another person, with the context they need and a date "
    "to chase it. Does not send anything on its own.",
    {
        "type": "object",
        "properties": {
            "assignee": {"type": "string", "description": "Name or email"},
            "title": {"type": "string"},
            "context": {"type": "string"},
            "due_at": {"type": "string", "description": "ISO timestamp or 'next friday'"},
        },
        "required": ["assignee", "title"],
    },
)
def _delegate_task(ctx: ToolContext, args: dict) -> dict:
    assignee = args["assignee"].strip()
    contact = ctx.db.scalars(
        select(Contact).where(
            Contact.user_id == ctx.user.id,
            or_(Contact.name.ilike(f"%{assignee}%"), Contact.email.ilike(f"%{assignee}%")),
        )
    ).first()

    row = Delegation(
        user_id=ctx.user.id,
        assignee_name=contact.name if contact else assignee,
        assignee_email=contact.email if contact else (assignee if "@" in assignee else ""),
        title=args["title"][:500],
        context=args.get("context", ""),
        due_at=parse_when(args.get("due_at"), default_hour=17),
    )
    ctx.db.add(row)
    ctx.db.commit()
    ctx.db.refresh(row)
    _log(ctx, "delegate_task", f"{row.assignee_name}: {row.title}"[:120])
    return {
        "message": f"Delegated to {row.assignee_name}: {row.title}",
        "id": row.id,
        "assignee": row.assignee_name,
        "due_at": _aware(row.due_at).isoformat() if row.due_at else None,
    }


@tool(
    "list_delegations",
    "List what the user has handed to other people and whether it has landed.",
    {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["open", "chased", "done", "all"]},
        },
    },
)
def _list_delegations(ctx: ToolContext, args: dict) -> list[dict]:
    stmt = select(Delegation).where(Delegation.user_id == ctx.user.id)
    status = args.get("status") or "open"
    if status != "all":
        stmt = stmt.where(Delegation.status == status)
    now = datetime.now(timezone.utc)
    return [
        {
            "id": d.id,
            "assignee": d.assignee_name,
            "title": d.title,
            "status": d.status,
            "due_at": _aware(d.due_at).isoformat() if d.due_at else None,
            "overdue": bool(d.due_at and (_aware(d.due_at) or now) < now and d.status != "done"),
        }
        for d in ctx.db.scalars(stmt.order_by(Delegation.due_at)).all()
    ]


@tool(
    "chase_delegation",
    "Draft a nudge for something delegated that has gone quiet. Drafts only — sending "
    "still needs approval.",
    {
        "type": "object",
        "properties": {"delegation_id": {"type": "string"}},
        "required": ["delegation_id"],
    },
)
def _chase_delegation(ctx: ToolContext, args: dict) -> dict:
    row = ctx.db.get(Delegation, args.get("delegation_id", ""))
    if not row or row.user_id != ctx.user.id:
        return {"error": "Delegation not found"}

    due = _aware(row.due_at)
    when = due.strftime("%A %d %B") if due else "the agreed date"
    first_name = (row.assignee_name or "there").split(" ")[0]
    draft = (
        f"Hi {first_name},\n\n"
        f"Checking in on {row.title.lower()} — we had it down for {when}. "
        "Where has it got to?\n\nThanks."
    )

    row.status = "chased"
    row.chased_at = utcnow()
    ctx.db.commit()
    _log(ctx, "chase_delegation", row.title[:120])
    return {
        "message": f"Chase drafted for {row.assignee_name}",
        "to": row.assignee_email,
        "subject": f"Following up: {row.title}",
        "body": draft,
    }


# ---------------------------------------------------------------------------
# Focus Guard (Focus plugin)
# ---------------------------------------------------------------------------


@tool(
    "check_focus_blocks",
    "Check whether anything has been booked into the hours the user protects, and "
    "propose alternatives for anything that has.",
    {
        "type": "object",
        "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 14}},
    },
)
def _check_focus_blocks(ctx: ToolContext, args: dict) -> dict:
    # The protected window is learned, not configured — it comes from whatever
    # the user has told the assistant about how they work.
    hits = memory_service.search(
        ctx.db, ctx.user.id, "protected hours deep work mornings focus time no meetings", limit=4
    )
    protected_start, protected_end = 9, 11
    for hit in hits:
        found = re.findall(r"\b(\d{1,2})(?::\d{2})?\s*(am|pm)?\b", hit.memory.content.lower())
        hours = []
        for raw, meridiem in found:
            hour = int(raw)
            if meridiem == "pm" and hour < 12:
                hour += 12
            if 0 <= hour <= 23:
                hours.append(hour)
        if len(hours) >= 2:
            protected_start, protected_end = min(hours[:2]), max(hours[:2])
            break

    days = int(args.get("days") or 7)
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=days)

    intrusions = []
    for event in ctx.db.scalars(
        select(CalendarEvent)
        .where(CalendarEvent.user_id == ctx.user.id, CalendarEvent.status != "cancelled")
        .order_by(CalendarEvent.start_at)
    ).all():
        start = _aware(event.start_at)
        if not start or not (now <= start <= horizon):
            continue
        if protected_start <= start.hour < protected_end:
            intrusions.append(_event_dict(event))

    alternatives = []
    if intrusions:
        alternatives = _find_free_time(
            ctx, {"duration_minutes": 30, "days": days, "earliest_hour": protected_end}
        )

    return {
        "protected_window": f"{protected_start:02d}:00–{protected_end:02d}:00",
        "learned_from": [h.memory.content for h in hits[:2]],
        "intrusions": intrusions,
        "suggested_alternatives": alternatives[:4],
    }
