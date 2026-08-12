"""Schedules — standing instructions the agent runs on a timer.

The distinction from an automation rule matters: an automation fires a fixed
list of tool calls, cheaply and predictably. A schedule runs a *prompt* through
the whole agent, which is far more capable and correspondingly more expensive.
Keeping them separate means you can have a hundred automations firing all day
and three schedules, rather than accidentally paying agent prices for a
notification.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notification, Schedule, User, utcnow

log = logging.getLogger("aura.schedules")

WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


# ---------------------------------------------------------------------------
# Cron
# ---------------------------------------------------------------------------


def cron_matches(cron: str, now: datetime | None = None) -> bool:
    """Minimal 5-field matcher: minute hour day month weekday.

    Supports '*', exact values, comma lists, a-b ranges and */n steps. Enough
    for the schedules people actually write; swap in croniter if you need more.
    """
    now = now or datetime.now(timezone.utc)
    parts = (cron or "").split()
    if len(parts) != 5:
        return False

    fields = [now.minute, now.hour, now.day, now.month, (now.weekday() + 1) % 7]

    def field_matches(pattern: str, value: int) -> bool:
        if pattern == "*":
            return True
        for token in pattern.split(","):
            token = token.strip()
            if token.startswith("*/"):
                try:
                    step = int(token[2:])
                    if step > 0 and value % step == 0:
                        return True
                except ValueError:
                    continue
            elif "-" in token:
                lo, _, hi = token.partition("-")
                try:
                    if int(lo) <= value <= int(hi):
                        return True
                except ValueError:
                    continue
            elif token.isdigit() and int(token) == value:
                return True
        return False

    return all(field_matches(p, v) for p, v in zip(parts, fields))


def describe(cron: str) -> str:
    """A readable rendering of a cron expression, for the UI."""
    parts = (cron or "").split()
    if len(parts) != 5:
        return cron or "—"
    minute, hour, day, month, weekday = parts

    if minute.startswith("*/"):
        return f"every {minute[2:]} minutes"
    if hour == "*" and minute.isdigit():
        return f"hourly at :{int(minute):02d}"

    time_part = (
        f"{int(hour):02d}:{int(minute):02d}"
        if hour.isdigit() and minute.isdigit()
        else f"{hour}:{minute}"
    )

    if weekday == "1-5":
        return f"weekdays at {time_part}"
    if weekday == "0,6" or weekday == "6,0":
        return f"weekends at {time_part}"
    if weekday.isdigit():
        return f"every {WEEKDAY_NAMES[(int(weekday) - 1) % 7].capitalize()} at {time_part}"
    if day.isdigit() and month == "*":
        return f"monthly on the {int(day)}{_ordinal(int(day))} at {time_part}"
    return f"daily at {time_part}"


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def parse_natural(text: str) -> tuple[str, str]:
    """Turn 'every weekday at 8am' into a cron plus a tidy name.

    Deliberately heuristic and offline — a schedule is created once, so paying
    for a model call to parse it is poor value, and a wrong cron that fires at
    3am is worse than one the user has to correct in a visible field.
    """
    lowered = text.lower().strip()

    hour, minute = 8, 0
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", lowered)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = match.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        hour = min(23, max(0, hour))

    if re.search(r"\bevery (\d+) minutes?\b", lowered):
        step = int(re.search(r"\bevery (\d+) minutes?\b", lowered).group(1))
        return f"*/{max(1, min(59, step))} * * * *", text[:80]
    if "hourly" in lowered or "every hour" in lowered:
        return f"{minute} * * * *", text[:80]

    weekday = "*"
    if "weekday" in lowered or "working day" in lowered:
        weekday = "1-5"
    elif "weekend" in lowered:
        weekday = "0,6"
    else:
        for index, name in enumerate(WEEKDAY_NAMES):
            if name in lowered:
                weekday = str((index + 1) % 7)
                break

    day = "*"
    if "month" in lowered and weekday == "*":
        day = "1"

    return f"{minute} {hour} {day} * {weekday}", text[:80]


# ---------------------------------------------------------------------------
# CRUD + execution
# ---------------------------------------------------------------------------


def create(
    db: Session,
    user: User,
    prompt: str,
    natural_language: str = "",
    name: str = "",
    cron: str = "",
    deliver_to: str = "notification",
) -> Schedule:
    parsed_cron, parsed_name = parse_natural(natural_language or prompt)
    row = Schedule(
        user_id=user.id,
        name=(name or parsed_name or prompt[:80]).strip(),
        prompt=prompt.strip(),
        natural_language=natural_language.strip(),
        cron=cron or parsed_cron,
        timezone=user.timezone or "UTC",
        deliver_to=deliver_to,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def listing(db: Session, user_id: str) -> list[Schedule]:
    return list(
        db.scalars(
            select(Schedule)
            .where(Schedule.user_id == user_id)
            .order_by(Schedule.created_at.desc())
        ).all()
    )


def run_one(db: Session, user: User, schedule: Schedule) -> dict:
    """Run the schedule's prompt through the full agent and deliver the result."""
    from app.agent.coordinator import persist_exchange, run_agent
    from app.deps import get_user_settings
    from app.models import Conversation

    convo = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id, Conversation.title == f"⏱ {schedule.name}")
        .limit(1)
    ).first()
    if not convo:
        convo = Conversation(user_id=user.id, title=f"⏱ {schedule.name}")
        db.add(convo)
        db.commit()
        db.refresh(convo)

    tier = get_user_settings(db, user).autonomy_level
    result = run_agent(
        db, user, convo, schedule.prompt, autonomy_level=tier, trigger="schedule"
    )
    persist_exchange(db, convo, schedule.prompt, result)

    schedule.last_run_at = utcnow()
    schedule.last_result = result.text[:4000]
    schedule.run_count += 1

    if schedule.deliver_to in ("notification", "email"):
        db.add(
            Notification(
                user_id=user.id,
                title=schedule.name[:300],
                body=result.text[:1500],
                level="urgent" if result.pending_actions else "info",
                link=f"/chat?c={convo.id}",
            )
        )
    db.commit()

    return {
        "schedule": schedule.name,
        "text": result.text,
        "skills": [r["code"] for r in result.skill_runs],
        "pending": len(result.pending_actions),
        "conversation_id": convo.id,
    }


def run_due(db: Session, now: datetime | None = None) -> list[dict]:
    """Called once a minute by the worker."""
    now = now or datetime.now(timezone.utc)
    rows = db.scalars(
        select(Schedule).where(Schedule.enabled == True)  # noqa: E712
    ).all()

    results: list[dict] = []
    for schedule in rows:
        if not cron_matches(schedule.cron, now):
            continue
        last = schedule.last_run_at
        if last:
            last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
            if now - last < timedelta(seconds=90):
                continue  # already fired inside this minute
        user = db.get(User, schedule.user_id)
        if not user or not user.is_active:
            continue
        try:
            results.append(run_one(db, user, schedule))
        except Exception:
            log.exception("Schedule %s failed", schedule.name)
            db.rollback()
    return results
