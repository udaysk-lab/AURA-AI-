"""Daily briefing and inbox summary generation."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import get_provider
from app.models import CalendarEvent, EmailMessage, Task, User

PRIORITY_WEIGHT = {"urgent": 4.0, "high": 3.0, "medium": 2.0, "low": 1.0}


def day_bounds(reference: datetime | None = None) -> tuple[datetime, datetime]:
    now = reference or datetime.now(timezone.utc)
    start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on round-trip; normalise back to UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def todays_events(db: Session, user_id: str) -> list[CalendarEvent]:
    start, end = day_bounds()
    rows = db.scalars(
        select(CalendarEvent)
        .where(CalendarEvent.user_id == user_id, CalendarEvent.status != "cancelled")
        .order_by(CalendarEvent.start_at)
    ).all()
    return [e for e in rows if start <= (_aware(e.start_at) or start) < end]


def tasks_due(db: Session, user_id: str, horizon_days: int = 2) -> list[Task]:
    cutoff = datetime.now(timezone.utc) + timedelta(days=horizon_days)
    rows = db.scalars(
        select(Task)
        .where(Task.user_id == user_id, Task.status != "done")
        .order_by(Task.due_at)
    ).all()
    return [t for t in rows if t.due_at and (_aware(t.due_at) or cutoff) <= cutoff]


def score_task(task: Task) -> float:
    """Deterministic prioritisation: urgency x time pressure."""
    score = PRIORITY_WEIGHT.get(task.priority, 2.0)
    due = _aware(task.due_at)
    if due:
        hours = (due - datetime.now(timezone.utc)).total_seconds() / 3600
        if hours < 0:
            score += 5.0          # overdue dominates
        elif hours < 24:
            score += 3.0
        elif hours < 72:
            score += 1.5
    if task.status == "doing":
        score += 0.5              # finish what's started
    return round(score, 2)


def rank_tasks(db: Session, user_id: str, limit: int = 10) -> list[Task]:
    rows = db.scalars(
        select(Task).where(Task.user_id == user_id, Task.status != "done")
    ).all()
    for t in rows:
        t.ai_score = score_task(t)
    db.commit()
    return sorted(rows, key=lambda t: t.ai_score, reverse=True)[:limit]


def urgent_emails(db: Session, user_id: str, limit: int = 5) -> list[EmailMessage]:
    return list(
        db.scalars(
            select(EmailMessage)
            .where(
                EmailMessage.user_id == user_id,
                EmailMessage.is_archived == False,  # noqa: E712
                EmailMessage.importance.in_(["urgent", "high"]),
            )
            .order_by(EmailMessage.received_at.desc())
            .limit(limit)
        ).all()
    )


def inbox_stats(db: Session, user_id: str) -> dict:
    rows = db.scalars(
        select(EmailMessage).where(
            EmailMessage.user_id == user_id,
            EmailMessage.is_archived == False,  # noqa: E712
        )
    ).all()
    return {
        "total": len(rows),
        "unread": sum(1 for e in rows if not e.is_read),
        "urgent": sum(1 for e in rows if e.importance == "urgent"),
        "needs_reply": sum(1 for e in rows if e.needs_reply),
        "rows": rows,
    }


def summarize_inbox(db: Session, user_id: str) -> dict:
    stats = inbox_stats(db, user_id)
    rows: list[EmailMessage] = stats.pop("rows")

    highlights = [
        {
            "id": e.id,
            "sender": e.sender_name or e.sender,
            "subject": e.subject,
            "importance": e.importance,
            "summary": e.ai_summary,
            "needs_reply": e.needs_reply,
        }
        for e in sorted(
            rows,
            key=lambda e: (
                {"urgent": 0, "high": 1, "normal": 2, "low": 3}.get(e.importance, 2),
                -(_aware(e.received_at) or datetime.now(timezone.utc)).timestamp(),
            ),
        )[:5]
    ]

    parts = [f"{stats['unread']} unread of {stats['total']}."]
    if stats["urgent"]:
        parts.append(f"{stats['urgent']} marked urgent.")
    if stats["needs_reply"]:
        parts.append(f"{stats['needs_reply']} waiting on a reply from you.")
    if highlights:
        top = highlights[0]
        parts.append(f"Top of the pile: {top['sender']} — {top['subject']}.")

    return {**stats, "summary": " ".join(parts), "highlights": highlights}


def _greeting(now: datetime) -> str:
    hour = now.hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def build_briefing(db: Session, user: User) -> dict:
    now = datetime.now(timezone.utc)
    events = todays_events(db, user.id)
    ranked = rank_tasks(db, user.id, limit=5)
    due = tasks_due(db, user.id)
    urgent = urgent_emails(db, user.id)
    inbox = summarize_inbox(db, user.id)

    first_name = (user.name or user.email.split("@")[0]).split(" ")[0]

    headline_bits = []
    if events:
        first = _aware(events[0].start_at)
        headline_bits.append(
            f"{len(events)} meeting{'s' if len(events) != 1 else ''}, first at "
            f"{first.strftime('%H:%M') if first else '—'}"
        )
    else:
        headline_bits.append("no meetings scheduled")
    if due:
        headline_bits.append(f"{len(due)} task{'s' if len(due) != 1 else ''} due")
    if inbox["urgent"]:
        headline_bits.append(f"{inbox['urgent']} urgent email{'s' if inbox['urgent'] != 1 else ''}")

    priorities = [f"{t.title} ({t.priority})" for t in ranked[:3]]
    for e in urgent[:2]:
        if e.needs_reply:
            priorities.append(f"Reply to {e.sender_name or e.sender} — {e.subject}")

    return {
        "generated_at": now,
        "greeting": f"{_greeting(now)}, {first_name}",
        "headline": "Today: " + ", ".join(headline_bits) + ".",
        "meetings": events,
        "tasks_due": due,
        "urgent_emails": urgent,
        "suggested_priorities": priorities[:5],
        "inbox": {
            "total": inbox["total"],
            "unread": inbox["unread"],
            "urgent": inbox["urgent"],
            "needs_reply": inbox["needs_reply"],
            "summary": inbox["summary"],
            "highlights": inbox["highlights"],
        },
    }


def draft_reply(db: Session, user: User, email: EmailMessage, instruction: str) -> dict:
    """Generate a reply draft, using stored writing-style memories as context."""
    from app.services import memory as memory_service

    style, _ = memory_service.build_context_block(
        db, user.id, "writing style tone email preferences", limit=4
    )
    provider = get_provider()

    if provider.name == "mock":
        body = (
            f"Hi {(email.sender_name or email.sender).split(' ')[0]},\n\n"
            "Thanks for this — I've read it and will come back to you with a decision shortly.\n\n"
            "Best,\n"
            f"{user.name or user.email.split('@')[0]}\n\n"
            "---\n_Demo-mode draft. Configure a model provider for real drafting._"
        )
        return {"subject": f"Re: {email.subject}", "body": body}

    resp = provider.complete(
        [
            {
                "role": "system",
                "content": (
                    "You draft email replies on behalf of the user. Match their voice. "
                    "Be concise. Output only the reply body — no subject line, no preamble, "
                    "no commentary.\n\n" + style
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{instruction}\n\n--- Email to reply to ---\n"
                    f"From: {email.sender_name} <{email.sender}>\n"
                    f"Subject: {email.subject}\n\n{email.body or email.snippet}"
                ),
            },
        ]
    )
    return {"subject": f"Re: {email.subject}", "body": resp.text.strip()}
