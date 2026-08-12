"""The heartbeat.

The difference between a chatbot and an assistant is that an assistant does
things while you're not looking. This runs on a timer, decides what is worth
doing right now, does it, and leaves a short report.

Two hard rules:
  * It only runs skills the user has enabled AND whose autonomy floor their
    trust tier clears. A heartbeat can never do something the user wouldn't
    have let it do in conversation.
  * It never fires an irreversible action. Those queue for approval like
    anything else.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import autonomy
from app.agent import skills as skill_registry
from app.models import (
    CalendarEvent,
    EmailMessage,
    HeartbeatReport,
    Notification,
    Task,
    User,
    UserSettings,
)
from app.services import google as google_service
from app.services import triage as triage_service

log = logging.getLogger("aura.heartbeat")


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def in_quiet_hours(spec: str, now: datetime | None = None) -> bool:
    """spec looks like '22:00-07:00'. Windows may wrap past midnight."""
    now = now or datetime.now(timezone.utc)
    try:
        start_s, end_s = spec.split("-")
        sh, sm = (int(x) for x in start_s.split(":"))
        eh, em = (int(x) for x in end_s.split(":"))
    except (ValueError, AttributeError):
        return False
    start, end, current = time(sh, sm), time(eh, em), now.time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def due(settings_row: UserSettings, last: HeartbeatReport | None, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if not settings_row.heartbeat_enabled:
        return False
    if in_quiet_hours(settings_row.quiet_hours, now):
        return False
    if last is None:
        return True
    previous = _aware(last.created_at) or now
    return (now - previous) >= timedelta(minutes=max(5, settings_row.heartbeat_interval_minutes))


def latest(db: Session, user_id: str) -> HeartbeatReport | None:
    return db.scalars(
        select(HeartbeatReport)
        .where(HeartbeatReport.user_id == user_id)
        .order_by(HeartbeatReport.created_at.desc())
        .limit(1)
    ).first()


def _can_run(tier: str, enabled: set[str], code: str) -> bool:
    skill = skill_registry.CATALOG.get(code)
    if not skill or code not in enabled or not skill.proactive:
        return False
    return autonomy.heartbeat_allows(tier, skill.autonomy_floor)


def run(db: Session, user: User, tier: str, force: bool = False) -> HeartbeatReport:
    """Do a pass of background work and write a report."""
    from app.agent.coordinator import execute_tool

    tier = autonomy.normalise_tier(tier)
    enabled = skill_registry.enabled_codes(db, user.id)
    now = datetime.now(timezone.utc)

    lines: list[str] = []
    codes_run: list[str] = []
    attention = 0

    def note(code: str, text: str) -> None:
        lines.append(f"[SKILL·{code}] {text}")
        codes_run.append(code)

    # --- Keep the mirror fresh ------------------------------------------
    if _can_run(tier, enabled, "SY01") and google_service.is_connected(db, user.id):
        result = execute_tool(db, user, "sync_google", {}, trigger="heartbeat")
        if isinstance(result, dict) and (result.get("emails") or result.get("events")):
            note("SY01", result.get("message", "Synced workspace"))

    # --- Triage whatever arrived ----------------------------------------
    if _can_run(tier, enabled, "EM01"):
        triaged = triage_service.triage_pending(db, user.id, limit=25)
        unread = db.scalars(
            select(EmailMessage).where(
                EmailMessage.user_id == user.id,
                EmailMessage.is_archived == False,  # noqa: E712
                EmailMessage.is_read == False,  # noqa: E712
            )
        ).all()
        urgent = [e for e in unread if e.importance == "urgent"]
        needs_reply = [e for e in unread if e.needs_reply]
        if unread:
            note(
                "EM01",
                f"Processed {len(unread)} unread · {len(urgent)} flagged · "
                f"{len(needs_reply)} awaiting reply"
                + (f" · {triaged} newly classified" if triaged else ""),
            )
        attention += len(urgent)

    # --- Turn commitments into tasks ------------------------------------
    if _can_run(tier, enabled, "TK01"):
        created = 0
        candidates = db.scalars(
            select(EmailMessage).where(
                EmailMessage.user_id == user.id,
                EmailMessage.is_archived == False,  # noqa: E712
                EmailMessage.importance.in_(["urgent", "high"]),
            )
        ).all()
        existing = {
            t.title.lower()
            for t in db.scalars(
                select(Task).where(Task.user_id == user.id, Task.status != "done")
            ).all()
        }
        for email in candidates:
            for item in (email.action_items or [])[:2]:
                if item and item.lower() not in existing:
                    execute_tool(
                        db, user, "create_task",
                        {"title": item, "priority": "high",
                         "notes": f"From: {email.sender_name or email.sender} — {email.subject}"},
                        trigger="heartbeat", log_skill=False,
                    )
                    existing.add(item.lower())
                    created += 1
        if created:
            note("TK01", f"Captured {created} commitment(s) from flagged email")

    # --- Watch the calendar ---------------------------------------------
    if _can_run(tier, enabled, "CA03"):
        conflicts = execute_tool(db, user, "detect_conflicts", {}, trigger="heartbeat")
        if isinstance(conflicts, list) and conflicts:
            note("CA03", f"Found {len(conflicts)} double-booking(s) — needs a decision")
            attention += len(conflicts)

    # --- Prepare the next meeting ---------------------------------------
    if _can_run(tier, enabled, "MP01"):
        upcoming = [
            e for e in db.scalars(
                select(CalendarEvent)
                .where(CalendarEvent.user_id == user.id, CalendarEvent.status != "cancelled")
                .order_by(CalendarEvent.start_at)
            ).all()
            if _aware(e.start_at) and now < _aware(e.start_at) <= now + timedelta(hours=3)
        ]
        if upcoming:
            brief = execute_tool(
                db, user, "prepare_meeting", {"event_id": upcoming[0].id}, trigger="heartbeat"
            )
            if isinstance(brief, dict) and not brief.get("error"):
                note("MP01", f"Briefed: {upcoming[0].title} · agenda and context ready")

    # --- Flag anything slipping -----------------------------------------
    if _can_run(tier, enabled, "TK02"):
        overdue = [
            t for t in db.scalars(
                select(Task).where(Task.user_id == user.id, Task.status != "done")
            ).all()
            if t.due_at and (_aware(t.due_at) or now) < now
        ]
        if overdue:
            note("TK02", f"{len(overdue)} task(s) now overdue · reprioritised")
            attention += len(overdue)

    headline = (
        "Nothing needed doing." if not lines
        else f"Handled {len(lines)} thing(s) while you were away"
        + (f" · {attention} need your attention" if attention else "")
    )

    report = HeartbeatReport(
        user_id=user.id,
        headline=headline,
        lines=lines,
        skills_run=sorted(set(codes_run)),
        needs_attention=attention,
    )
    db.add(report)

    if attention and _can_run(tier, enabled, "NT01"):
        db.add(
            Notification(
                user_id=user.id,
                title=f"{attention} item(s) need you",
                body=headline,
                level="warning" if attention < 3 else "urgent",
                link="/dashboard",
            )
        )

    db.commit()
    db.refresh(report)
    log.info("Heartbeat for %s: %s", user.email, headline)
    return report


def run_all_due(db: Session) -> list[HeartbeatReport]:
    """Called once a minute by the worker."""
    reports: list[HeartbeatReport] = []
    users = db.scalars(select(User).where(User.is_active == True)).all()  # noqa: E712
    for user in users:
        row = db.scalars(
            select(UserSettings).where(UserSettings.user_id == user.id)
        ).first()
        if not row:
            continue
        if not due(row, latest(db, user.id)):
            continue
        try:
            reports.append(run(db, user, row.autonomy_level))
        except Exception:
            log.exception("Heartbeat failed for %s", user.email)
            db.rollback()
    return reports
