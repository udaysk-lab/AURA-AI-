"""The skill catalogue.

A *tool* is a function the model can call. A *skill* is the unit the **user**
thinks in: a named, toggleable capability with a code, a category, a track
record, and notes it has learned about how this particular person likes it done.

Skills group tools. Turning off "Inbox Cleanup" removes `archive_email` and
`send_email` from the model's toolset entirely — it can't call what it can't
see, which is a stronger guarantee than asking it not to.

Codes follow Vellum-style `XX##` so activity lines stay scannable:
    [SKILL·EM01] Processed 47 emails · 3 flagged · 12 awaiting reply
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SkillRun, UserSkill, utcnow


@dataclass(frozen=True)
class Skill:
    code: str
    name: str
    category: str
    description: str
    tools: tuple[str, ...]
    # Skills the heartbeat may run unattended, and the lowest autonomy tier at
    # which it will do so.
    proactive: bool = False
    autonomy_floor: str = "conservative"
    default_enabled: bool = True
    summarize: Callable[[Any], str] | None = field(default=None, compare=False)


# ---------------------------------------------------------------------------
# Summarisers — turn a raw tool result into one scannable line
# ---------------------------------------------------------------------------


def _n(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0


def _sum_inbox(result: Any) -> str:
    if not isinstance(result, dict):
        return "Reviewed the inbox"
    parts = [f"{result.get('total', 0)} emails scanned"]
    if result.get("unread"):
        parts.append(f"{result['unread']} unread")
    if result.get("urgent"):
        parts.append(f"{result['urgent']} flagged")
    if result.get("needs_reply"):
        parts.append(f"{result['needs_reply']} awaiting reply")
    return "Processed " + " · ".join(parts)


def _sum_search_email(result: Any) -> str:
    return f"Searched inbox · {_n(result)} matches"


def _sum_draft(result: Any) -> str:
    if isinstance(result, dict) and result.get("subject"):
        return f"Drafted reply · {result['subject'][:60]} · awaiting your review"
    return "Drafted a reply"


def _sum_cleanup(result: Any) -> str:
    if isinstance(result, dict):
        return result.get("message", "Cleaned up the inbox")[:200]
    return "Cleaned up the inbox"


def _sum_events(result: Any) -> str:
    return f"Read calendar · {_n(result)} events in window"


def _sum_create_event(result: Any) -> str:
    if isinstance(result, dict):
        return result.get("message", "Created an event")[:200]
    return "Created an event"


def _sum_slots(result: Any) -> str:
    return f"Found {_n(result)} open slots"


def _sum_conflicts(result: Any) -> str:
    count = _n(result)
    return "No double-bookings found" if count == 0 else f"Found {count} double-booking(s)"


def _sum_prep(result: Any) -> str:
    if not isinstance(result, dict):
        return "Prepared a meeting brief"
    title = (result.get("event") or {}).get("title", "meeting")
    return (
        f"Briefed: {title} · {_n(result.get('related_emails'))} emails · "
        f"{_n(result.get('relevant_memories'))} memories · agenda drafted"
    )


def _sum_task_write(result: Any) -> str:
    if isinstance(result, dict):
        return result.get("message", "Updated a task")[:200]
    return "Updated a task"


def _sum_task_list(result: Any) -> str:
    return f"Ranked {_n(result)} open tasks by urgency"


def _sum_memory_save(result: Any) -> str:
    if isinstance(result, dict) and result.get("content"):
        return f"Remembered: {result['content'][:110]}"
    return "Stored a memory"


def _sum_memory_search(result: Any) -> str:
    return f"Recalled {_n(result)} relevant memories"


def _sum_contacts(result: Any) -> str:
    return f"Looked up {_n(result)} contacts"


def _sum_briefing(result: Any) -> str:
    if not isinstance(result, dict):
        return "Compiled the daily briefing"
    return (
        f"Briefing ready · {_n(result.get('meetings'))} meetings · "
        f"{_n(result.get('tasks_due'))} tasks due · "
        f"{_n(result.get('urgent_emails'))} urgent emails"
    )


def _sum_notify(result: Any) -> str:
    return "Raised a notification"


def _sum_sync(result: Any) -> str:
    if isinstance(result, dict):
        return result.get("message", "Synced the workspace")[:200]
    return "Synced the workspace"


def _sum_research(result: Any) -> str:
    if not isinstance(result, dict):
        return "Researched a topic"
    if result.get("error"):
        return str(result["error"])[:180]
    return (
        f"Researched \"{str(result.get('query', ''))[:50]}\" · "
        f"{_n(result.get('sources'))} sources · summary ready"
    )


def _sum_person_brief(result: Any) -> str:
    if not isinstance(result, dict):
        return "Compiled a briefing"
    return (
        f"Briefed {result.get('subject', 'contact')} · "
        f"{_n(result.get('recent_emails'))} emails · "
        f"{_n(result.get('sources'))} sources"
    )


def _sum_doc_search(result: Any) -> str:
    return f"Searched documents · {_n(result)} passages matched"


def _sum_doc_summary(result: Any) -> str:
    if isinstance(result, dict) and result.get("title"):
        return f"Summarised: {result['title'][:70]}"
    return "Summarised a document"


def _sum_delegate(result: Any) -> str:
    if isinstance(result, dict):
        return result.get("message", "Delegated a task")[:200]
    return "Delegated a task"


def _sum_delegations(result: Any) -> str:
    return f"Reviewed {_n(result)} outstanding delegation(s)"


def _sum_focus(result: Any) -> str:
    if not isinstance(result, dict):
        return "Checked your focus blocks"
    intrusions = _n(result.get("intrusions"))
    return (
        "Focus blocks clear" if not intrusions
        else f"{intrusions} meeting(s) landed in protected time · alternatives proposed"
    )


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

CATALOG: dict[str, Skill] = {
    s.code: s
    for s in [
        Skill(
            code="EM01",
            name="Inbox Triage",
            category="Email",
            description="Reads new mail, scores urgency, extracts action items and tells you what actually matters.",
            tools=("summarize_inbox", "search_emails", "get_email"),
            proactive=True,
            autonomy_floor="strict",
            summarize=_sum_inbox,
        ),
        Skill(
            code="EM02",
            name="Reply Drafting",
            category="Email",
            description="Writes replies in your voice, using what it knows about your tone and relationships.",
            tools=("draft_reply",),
            proactive=True,
            autonomy_floor="conservative",
            summarize=_sum_draft,
        ),
        Skill(
            code="EM03",
            name="Inbox Cleanup",
            category="Email",
            description="Sends and archives. Always confirms first unless you grant standing permission.",
            tools=("send_email", "archive_email"),
            autonomy_floor="full",
            summarize=_sum_cleanup,
        ),
        Skill(
            code="CA01",
            name="Schedule Management",
            category="Calendar",
            description="Reads your calendar and books time, keeping your working hours intact.",
            tools=("list_events", "create_event", "delete_event"),
            proactive=True,
            autonomy_floor="relaxed",
            summarize=_sum_create_event,
        ),
        Skill(
            code="CA02",
            name="Availability Finding",
            category="Calendar",
            description="Finds genuinely free slots, not just gaps between blocks.",
            tools=("find_free_time",),
            proactive=True,
            autonomy_floor="strict",
            summarize=_sum_slots,
        ),
        Skill(
            code="CA03",
            name="Conflict Watch",
            category="Calendar",
            description="Notices double-bookings before you walk into them.",
            tools=("detect_conflicts",),
            proactive=True,
            autonomy_floor="strict",
            summarize=_sum_conflicts,
        ),
        Skill(
            code="MP01",
            name="Meeting Prep",
            category="Meetings",
            description="Builds a brief before every meeting: who's coming, recent history, an agenda.",
            tools=("prepare_meeting",),
            proactive=True,
            autonomy_floor="conservative",
            summarize=_sum_prep,
        ),
        Skill(
            code="TK01",
            name="Task Capture",
            category="Tasks",
            description="Turns commitments buried in email and conversation into tracked tasks.",
            tools=("create_task", "complete_task", "delete_task"),
            proactive=True,
            autonomy_floor="conservative",
            summarize=_sum_task_write,
        ),
        Skill(
            code="TK02",
            name="Priority Triage",
            category="Tasks",
            description="Reorders your list by what's actually urgent, not what you typed first.",
            tools=("list_tasks",),
            proactive=True,
            autonomy_floor="strict",
            summarize=_sum_task_list,
        ),
        Skill(
            code="MM01",
            name="Memory Keeping",
            category="Memory",
            description="Notices durable preferences and decisions, and recalls them when they're relevant.",
            tools=("save_memory", "search_memory"),
            proactive=True,
            autonomy_floor="strict",
            summarize=_sum_memory_save,
        ),
        Skill(
            code="CT01",
            name="Relationship Tracking",
            category="People",
            description="Keeps track of who matters, what they do, and when you last spoke.",
            tools=("list_contacts",),
            summarize=_sum_contacts,
        ),
        Skill(
            code="BR01",
            name="Daily Briefing",
            category="Planning",
            description="One compiled view of your day: meetings, deadlines, urgent mail, suggested focus.",
            tools=("get_daily_briefing",),
            proactive=True,
            autonomy_floor="strict",
            summarize=_sum_briefing,
        ),
        Skill(
            code="NT01",
            name="Notifications",
            category="Planning",
            description="Interrupts you only when something genuinely warrants it.",
            tools=("notify",),
            proactive=True,
            autonomy_floor="strict",
            summarize=_sum_notify,
        ),
        Skill(
            code="SY01",
            name="Workspace Sync",
            category="System",
            description="Keeps the local mirror of Gmail and Calendar current.",
            tools=("sync_google",),
            proactive=True,
            autonomy_floor="strict",
            summarize=_sum_sync,
        ),
        # --- Unlocked by the Research plugin ------------------------------
        Skill(
            code="RS01",
            name="Web Research",
            category="Research",
            description="Searches the web and compiles what it finds into a usable answer, with sources.",
            tools=("web_research",),
            summarize=_sum_research,
        ),
        Skill(
            code="RS02",
            name="Briefing Pack",
            category="Research",
            description="Builds a dossier on a person or company from the web plus your own inbox and contacts.",
            tools=("brief_subject",),
            proactive=True,
            autonomy_floor="relaxed",
            summarize=_sum_person_brief,
        ),
        # --- Unlocked by the Documents plugin -----------------------------
        Skill(
            code="DC01",
            name="Document Q&A",
            category="Documents",
            description="Answers questions from your uploaded files by finding the passages that actually apply.",
            tools=("search_documents", "summarize_document"),
            summarize=_sum_doc_search,
        ),
        Skill(
            code="DC02",
            name="Document Compare",
            category="Documents",
            description="Diffs two documents and tells you what materially changed, not just what differs.",
            tools=("compare_documents",),
            summarize=_sum_doc_summary,
        ),
        # --- Unlocked by the Delegation plugin ----------------------------
        Skill(
            code="DL01",
            name="Delegation",
            category="People",
            description="Records what you handed to whom, with the context they need and a date.",
            tools=("delegate_task", "list_delegations"),
            summarize=_sum_delegate,
        ),
        Skill(
            code="DL02",
            name="Chase-ups",
            category="People",
            description="Notices when something you delegated has gone quiet and drafts the nudge.",
            tools=("chase_delegation",),
            proactive=True,
            autonomy_floor="conservative",
            summarize=_sum_delegations,
        ),
        # --- Unlocked by the Focus Guard plugin ---------------------------
        Skill(
            code="FG01",
            name="Focus Guard",
            category="Planning",
            description="Watches the hours you protect and flags anything that lands in them, with alternatives.",
            tools=("check_focus_blocks",),
            proactive=True,
            autonomy_floor="strict",
            summarize=_sum_focus,
        ),
    ]
}

TOOL_TO_SKILL: dict[str, str] = {
    tool: skill.code for skill in CATALOG.values() for tool in skill.tools
}

CATEGORIES = [
    "Email",
    "Calendar",
    "Meetings",
    "Tasks",
    "Memory",
    "People",
    "Research",
    "Documents",
    "Planning",
    "System",
]


def skill_for_tool(tool_name: str) -> Skill | None:
    code = TOOL_TO_SKILL.get(tool_name)
    return CATALOG.get(code) if code else None


def search_result_summarizer(tool_name: str) -> Callable[[Any], str] | None:
    """A few tools want a different line from their skill's default."""
    overrides: dict[str, Callable[[Any], str]] = {
        "search_emails": _sum_search_email,
        "get_email": lambda r: f"Read: {(r or {}).get('subject', 'an email')[:80]}"
        if isinstance(r, dict)
        else "Read an email",
        "list_events": _sum_events,
        "search_memory": _sum_memory_search,
        "summarize_inbox": _sum_inbox,
    }
    return overrides.get(tool_name)


def summarise(tool_name: str, result: Any) -> str:
    override = search_result_summarizer(tool_name)
    if override:
        try:
            return override(result)
        except Exception:
            pass
    skill = skill_for_tool(tool_name)
    if skill and skill.summarize:
        try:
            return skill.summarize(result)
        except Exception:
            pass
    if isinstance(result, dict) and result.get("message"):
        return str(result["message"])[:200]
    return f"Ran {tool_name}"


# ---------------------------------------------------------------------------
# Per-user state
# ---------------------------------------------------------------------------


def unlocked_codes(db: Session, user_id: str) -> set[str]:
    """Skill codes made available by the user's installed plugins."""
    from app import plugins

    return plugins.unlocked_skill_codes(db, user_id)


def ensure_user_skills(db: Session, user_id: str) -> list[UserSkill]:
    """Create UserSkill rows for every unlocked skill. Safe to call per request."""
    unlocked = unlocked_codes(db, user_id)
    existing = {
        row.code: row
        for row in db.scalars(select(UserSkill).where(UserSkill.user_id == user_id)).all()
    }
    created = False
    for code in unlocked:
        skill = CATALOG.get(code)
        if skill and code not in existing:
            row = UserSkill(user_id=user_id, code=code, enabled=skill.default_enabled)
            db.add(row)
            existing[code] = row
            created = True
    if created:
        db.commit()
    # Rows for uninstalled plugins are kept (so history survives) but not returned.
    return [row for code, row in existing.items() if code in unlocked]


def enabled_codes(db: Session, user_id: str) -> set[str]:
    """Enabled *and* unlocked. A skill needs both to reach the model."""
    rows = ensure_user_skills(db, user_id)
    return {r.code for r in rows if r.enabled}


def enabled_tools(db: Session, user_id: str) -> set[str]:
    """Tool names the model is allowed to see for this user."""
    codes = enabled_codes(db, user_id)
    return {tool for code in codes for tool in CATALOG[code].tools}


def get_user_skill(db: Session, user_id: str, code: str) -> UserSkill | None:
    return db.scalars(
        select(UserSkill).where(UserSkill.user_id == user_id, UserSkill.code == code)
    ).first()


def learned_notes_block(db: Session, user_id: str, codes: set[str] | None = None) -> str:
    """Corrections the user has given, formatted for the system prompt.

    This is the mechanism behind "learns how you like things done" — a
    correction on a skill is stored against that skill and replayed every time
    the skill is in scope.
    """
    rows = db.scalars(select(UserSkill).where(UserSkill.user_id == user_id)).all()
    lines: list[str] = []
    for row in rows:
        if codes and row.code not in codes:
            continue
        for note in row.learned_notes or []:
            skill = CATALOG.get(row.code)
            lines.append(f"- [{skill.name if skill else row.code}] {note}")
    if not lines:
        return ""
    return "Corrections this user has given you (follow them):\n" + "\n".join(lines[:20])


def teach(db: Session, user_id: str, code: str, note: str) -> UserSkill | None:
    row = get_user_skill(db, user_id, code)
    if not row:
        ensure_user_skills(db, user_id)
        row = get_user_skill(db, user_id, code)
    if not row:
        return None
    notes = list(row.learned_notes or [])
    if note not in notes:
        notes.append(note)
    row.learned_notes = notes[-10:]  # keep the most recent guidance
    db.commit()
    db.refresh(row)
    return row


def record_run(
    db: Session,
    user_id: str,
    tool_name: str,
    result: Any,
    trigger: str = "chat",
    duration_ms: int = 0,
) -> SkillRun | None:
    """Log a skill execution and update the skill's track record."""
    skill = skill_for_tool(tool_name)
    if not skill:
        return None

    failed = isinstance(result, dict) and bool(result.get("error"))
    run = SkillRun(
        user_id=user_id,
        code=skill.code,
        trigger=trigger,
        summary=summarise(tool_name, result)[:500],
        status="error" if failed else "success",
        detail={"tool": tool_name},
        duration_ms=duration_ms,
    )
    db.add(run)

    row = get_user_skill(db, user_id, skill.code)
    if row:
        row.run_count += 1
        if not failed:
            row.success_count += 1
        row.last_run_at = utcnow()
    db.commit()
    db.refresh(run)
    return run


def recent_runs(db: Session, user_id: str, limit: int = 40) -> list[SkillRun]:
    return list(
        db.scalars(
            select(SkillRun)
            .where(SkillRun.user_id == user_id)
            .order_by(SkillRun.created_at.desc())
            .limit(limit)
        ).all()
    )


def activity_line(run: SkillRun) -> str:
    """The scannable log format: [SKILL·EM01] Processed 47 emails · 3 flagged"""
    return f"[SKILL·{run.code}] {run.summary}"


def stats(db: Session, user_id: str) -> dict:
    rows = ensure_user_skills(db, user_id)
    total_runs = sum(r.run_count for r in rows)
    return {
        "total": len(CATALOG),
        "enabled": sum(1 for r in rows if r.enabled),
        "total_runs": total_runs,
        "most_used": max(
            (r for r in rows if r.run_count), key=lambda r: r.run_count, default=None
        ),
    }
