"""Automation engine.

Users write rules in plain language. The engine compiles them once into a
structured trigger + action list, then executes them deterministically. Compiling
up front (rather than asking a model at trigger time) makes rules auditable,
cheap to run, and predictable — which matters a lot for something firing
unattended against a real inbox.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.autonomy import risk_class
from app.agent.coordinator import execute_tool
from app.agent.prompts import AUTOMATION_COMPILER
from app.llm import get_provider
from app.models import (
    ActivityLog,
    AutomationRule,
    EmailMessage,
    PendingAction,
    Task,
    User,
    utcnow,
)

log = logging.getLogger("aura.automation")

VALID_TRIGGERS = {"email_received", "schedule", "event_cancelled", "task_due", "manual"}


def is_destructive(tool_name: str) -> bool:
    """Automations never fire irreversible actions unattended, whatever the tier."""
    return risk_class(tool_name) == "irreversible"


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def _heuristic_compile(rule: str) -> dict:
    """Offline compiler. Covers the common shapes without a model call."""
    text = rule.lower()
    actions: list[dict] = []

    trigger_type = "manual"
    trigger_config: dict[str, Any] = {}

    time_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text)
    if re.search(r"\bevery (day|weekday|morning|monday|week)\b|\bdaily\b|\beach (morning|day)\b", text):
        hour, minute = 8, 0
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or 0)
            if time_match.group(3) == "pm" and hour < 12:
                hour += 12
        days = "1-5" if "weekday" in text else "*"
        trigger_type = "schedule"
        trigger_config = {"cron": f"{minute} {hour} * * {days}"}
    elif re.search(r"\b(receive|get|arrives?|incoming)\b.*\bemail\b|\bemail\b.*\b(from|arrives)\b", text):
        trigger_type = "email_received"
        sender = re.search(r"from\s+(?:my\s+)?([\w.@+-]+\.[a-z]{2,}|[a-z ]{3,25}?)(?:,|\s+then|\s+create|\s+add|\s+notify|$)", text)
        if sender:
            trigger_config["from_contains"] = sender.group(1).strip()
        if re.search(r"\b(urgent|important)\b", text):
            trigger_config["importance"] = "urgent"
        subject = re.search(r"subject (?:contains|includes|mentions)\s+[\"']?([\w ]+)", text)
        if subject:
            trigger_config["subject_contains"] = subject.group(1).strip()
    elif "cancel" in text:
        trigger_type = "event_cancelled"
    elif re.search(r"\btask\b.*\bdue\b|\bdue\b.*\btask\b", text):
        trigger_type = "task_due"
        trigger_config = {"within_hours": 24}

    if re.search(r"\b(create|add|make)\b.*\btask\b|\bto-?do\b", text):
        actions.append(
            {"tool": "create_task", "arguments": {"title": "{subject}", "priority": "high"}}
        )
    if re.search(r"\b(remind|notify|alert|tell) me\b|\bnotification\b", text):
        actions.append(
            {
                "tool": "notify",
                "arguments": {"title": "{subject}", "body": "From {sender}", "level": "info"},
            }
        )
    if re.search(r"\b(draft|write|prepare)\b.*\b(reply|response)\b", text):
        actions.append({"tool": "draft_reply", "arguments": {}})
    if re.search(r"\bbrief(ing)?\b|\bsummar\w+ my day\b", text):
        actions.append({"tool": "get_daily_briefing", "arguments": {}})
    if re.search(r"\b(next available|free slot|availability)\b", text):
        actions.append({"tool": "find_free_time", "arguments": {"duration_minutes": 30}})
    if not actions:
        actions.append({"tool": "notify", "arguments": {"title": rule[:120], "level": "info"}})

    return {
        "name": rule.strip()[:80],
        "trigger_type": trigger_type,
        "trigger_config": trigger_config,
        "actions": actions,
        "requires_confirmation": any(is_destructive(a["tool"]) for a in actions),
    }


def compile_rule(natural_language: str) -> dict:
    """Compile a plain-language rule, falling back to heuristics offline."""
    provider = get_provider()
    if provider.name != "mock":
        try:
            resp = provider.complete(
                [
                    {"role": "system", "content": "You compile automation rules. Output JSON only."},
                    {"role": "user", "content": AUTOMATION_COMPILER.format(rule=natural_language)},
                ]
            )
            match = re.search(r"\{.*\}", resp.text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                if data.get("trigger_type") in VALID_TRIGGERS and data.get("actions"):
                    data.setdefault("name", natural_language[:80])
                    data.setdefault("trigger_config", {})
                    data["requires_confirmation"] = bool(
                        data.get("requires_confirmation")
                        or any(is_destructive(a.get("tool", "")) for a in data["actions"])
                    )
                    return data
        except Exception as exc:
            log.warning("Model rule compilation failed, using heuristics: %s", exc)
    return _heuristic_compile(natural_language)


def create_rule(db: Session, user: User, natural_language: str, enabled: bool = True) -> AutomationRule:
    compiled = compile_rule(natural_language)
    rule = AutomationRule(
        user_id=user.id,
        name=compiled["name"],
        natural_language=natural_language,
        trigger_type=compiled["trigger_type"],
        trigger_config=compiled.get("trigger_config") or {},
        actions=compiled["actions"],
        enabled=enabled,
        requires_confirmation=compiled.get("requires_confirmation", True),
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


# ---------------------------------------------------------------------------
# Matching + execution
# ---------------------------------------------------------------------------


def _interpolate(value: Any, context: dict) -> Any:
    if isinstance(value, str):
        out = value
        for key, val in context.items():
            out = out.replace("{" + key + "}", str(val))
        return out
    if isinstance(value, dict):
        return {k: _interpolate(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v, context) for v in value]
    return value


def matches_email(rule: AutomationRule, email: EmailMessage) -> bool:
    cfg = rule.trigger_config or {}
    if sender := cfg.get("from_contains"):
        haystack = f"{email.sender} {email.sender_name}".lower()
        if sender.lower() not in haystack:
            return False
    if subject := cfg.get("subject_contains"):
        if subject.lower() not in (email.subject or "").lower():
            return False
    if importance := cfg.get("importance"):
        order = {"low": 0, "normal": 1, "high": 2, "urgent": 3}
        if order.get(email.importance, 1) < order.get(importance, 3):
            return False
    return True


def run_rule(db: Session, user: User, rule: AutomationRule, context: dict | None = None) -> dict:
    """Execute a rule's actions. Destructive actions are queued, never auto-run."""
    context = context or {}
    executed: list[dict] = []
    queued: list[str] = []

    for action in rule.actions or []:
        tool_name = action.get("tool", "")
        args = _interpolate(action.get("arguments") or {}, context)

        # Fill implicit references from the trigger context.
        if tool_name in ("draft_reply", "get_email", "archive_email") and context.get("email_id"):
            args.setdefault("email_id", context["email_id"])

        # An unattended rule never fires an irreversible action on its own.
        if is_destructive(tool_name):
            pending = PendingAction(
                user_id=user.id,
                tool_name=tool_name,
                arguments=args,
                preview=f"[automation: {rule.name}] {tool_name} {json.dumps(args, default=str)[:300]}",
            )
            db.add(pending)
            db.commit()
            queued.append(tool_name)
            continue

        outcome = execute_tool(db, user, tool_name, args, trigger="automation")
        executed.append({"tool": tool_name, "result": outcome})

    rule.last_run_at = utcnow()
    rule.run_count += 1
    db.add(
        ActivityLog(
            user_id=user.id,
            actor="automation",
            action=f"rule:{rule.name}"[:120],
            target=context.get("subject", "")[:300],
            detail={"executed": [e["tool"] for e in executed], "queued": queued},
        )
    )
    db.commit()
    return {"rule": rule.name, "executed": executed, "queued_for_approval": queued}


def on_email_received(db: Session, user: User, email: EmailMessage) -> list[dict]:
    rules = db.scalars(
        select(AutomationRule).where(
            AutomationRule.user_id == user.id,
            AutomationRule.enabled == True,  # noqa: E712
            AutomationRule.trigger_type == "email_received",
        )
    ).all()
    results = []
    for rule in rules:
        if matches_email(rule, email):
            results.append(
                run_rule(
                    db, user, rule,
                    {
                        "subject": email.subject,
                        "sender": email.sender_name or email.sender,
                        "body": (email.body or email.snippet)[:2000],
                        "email_id": email.id,
                    },
                )
            )
    return results


def on_task_due(db: Session, user: User) -> list[dict]:
    rules = db.scalars(
        select(AutomationRule).where(
            AutomationRule.user_id == user.id,
            AutomationRule.enabled == True,  # noqa: E712
            AutomationRule.trigger_type == "task_due",
        )
    ).all()
    if not rules:
        return []

    results = []
    for rule in rules:
        hours = int((rule.trigger_config or {}).get("within_hours", 24))
        cutoff = datetime.now(timezone.utc) + timedelta(hours=hours)
        due = [
            t for t in db.scalars(
                select(Task).where(Task.user_id == user.id, Task.status != "done")
            ).all()
            if t.due_at and (t.due_at if t.due_at.tzinfo else t.due_at.replace(tzinfo=timezone.utc)) <= cutoff
        ]
        for task in due:
            results.append(run_rule(db, user, rule, {"title": task.title, "subject": task.title}))
    return results


def due_schedules(rule: AutomationRule, now: datetime | None = None) -> bool:
    """Minimal cron matcher: minute, hour, day-of-month, month, day-of-week.

    Supports '*', exact values, comma lists and simple a-b ranges. Enough for
    the schedules users actually write; swap in croniter if you need more.
    """
    now = now or datetime.now(timezone.utc)
    cron = (rule.trigger_config or {}).get("cron")
    if not cron:
        return False
    parts = cron.split()
    if len(parts) != 5:
        return False

    fields = [now.minute, now.hour, now.day, now.month, (now.weekday() + 1) % 7]

    def field_matches(pattern: str, value: int) -> bool:
        if pattern == "*":
            return True
        for token in pattern.split(","):
            if "-" in token:
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


def run_due_schedules(db: Session, now: datetime | None = None) -> list[dict]:
    """Call once a minute from a worker/cron to fire scheduled rules."""
    now = now or datetime.now(timezone.utc)
    rules = db.scalars(
        select(AutomationRule).where(
            AutomationRule.enabled == True,  # noqa: E712
            AutomationRule.trigger_type == "schedule",
        )
    ).all()

    results = []
    for rule in rules:
        if not due_schedules(rule, now):
            continue
        last = rule.last_run_at
        if last:
            last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
            if (now - last).total_seconds() < 90:
                continue  # already fired this minute
        user = db.get(User, rule.user_id)
        if user:
            results.append(run_rule(db, user, rule, {"subject": rule.name}))
    return results
