"""Task memory: the record of each assignment, and recalling similar past work.

Spec 20-taskloop-memory.md sections 2.1, 2.2, 3, 5.5 (the storing half), 9 and 11. The loop
engine decides *what* happened (status, report text); this module writes it down:

  create_item / set_plan / update_plan_step     the item and its plan
  add_step / finish_step / add_check            the ordered steps and the verification
  note_tool                                     tools_used / systems_used
  finalise                                      redact, type, summarise, embed, close
  recall_work / build_recall                    similar past work, preferences and facts
  edit / set_feedback / delete_item / reset     the owner's controls

Task memory is never compacted, merged or decayed: nothing here deletes a finished item except
the owner's own delete and reset.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Conversation, Preference, User, UserSettings, WorkItem, WorkStep, utcnow
from app.permissions import catalogue
from app.services import memory as memory_service
from app.services import preferences as preference_service
from app.services import redaction, tasktypes

INSTRUCTION_MAX = 4000
TITLE_MAX = 200
TITLE_FROM_INSTRUCTION = 60
SUMMARY_MAX = 1200
OUTCOME_MAX = 300
REPORT_MAX = 4000
STEP_SUMMARY_MAX = 500
PLAN_STEPS_MAX = 12
PLAN_TEXT_MAX = 160
NOTE_MAX = 200

BOOKKEEPING = frozenset(catalogue.CATALOGUE[n].name for n in catalogue.CATALOGUE if catalogue.CATALOGUE[n].loop_internal)

STATUSES = ("running", "waiting", "ready", "done", "partly_done", "failed", "stopped")
OPEN_STATUSES = ("running", "waiting", "ready")
FINISHED_STATUSES = ("done", "partly_done", "failed")
PLAN_STATUSES = ("todo", "doing", "done", "skipped", "failed")
STEP_KINDS = ("plan", "tool", "approval", "reviewer_block", "verification", "note")

# 9.4
CANDIDATES = 300
MIN_SCORE = 0.12
TOP_WORK = 3
LAZY_EMBED = 5
LINE_MAX = 350
# 9.5
CONTEXT_CAP = 4000
FACT_LIMIT = 6
FACT_LINE_MAX = 200

PAST_HEADER = "Past assignments like this one (your own records):"


class ItemBusy(Exception):
    """The item is still running, so it cannot be deleted or reset."""


# ---------------------------------------------------------------------------
# Creating and updating an item
# ---------------------------------------------------------------------------


def _scrub(db: Session, user_id: str, text: str, limit: int) -> str:
    return redaction.scrub(db, user_id, text or "")[:limit]


def title_from(instruction: str) -> str:
    first = next((l.strip() for l in (instruction or "").splitlines() if l.strip()), "")
    return first[:TITLE_FROM_INSTRUCTION] or "Untitled task"


def create_item(
    db: Session,
    user: User,
    instruction: str,
    *,
    trigger: str = "chat",
    origin_ref: str = "",
    conversation: Conversation | None = None,
    task_type: str | None = None,
    lease_owner: str = "",
    lease_until=None,
) -> WorkItem:
    """Insert a `running` item. The instruction is scrubbed and cut to 4 000 characters."""
    clean = _scrub(db, user.id, instruction, INSTRUCTION_MAX)
    now = utcnow()
    item = WorkItem(
        user_id=user.id,
        conversation_id=conversation.id if conversation is not None else None,
        trigger=trigger,
        origin_ref=(origin_ref or "")[:64],
        instruction=clean,
        title=title_from(clean),
        task_type=tasktypes.normalise(task_type) if task_type else tasktypes.classify(clean),
        status="running",
        started_at=now,
        lease_owner=lease_owner,
        lease_until=lease_until,
        segments=1,
        plan=[],
        systems_used=[],
        tools_used=[],
        verification={"status": "none", "checks": []},
        user_feedback={},
        loop_state={},
        recall_used={"work_item_ids": [], "preference_ids": [], "memory_ids": []},
        embedding=[],
        recallable=True,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def set_plan(db: Session, item: WorkItem, title: str | None, task_type: str | None, steps: list[str]) -> list[dict]:
    """`aura_plan`: title, type and a fresh plan with every step `todo`. Returns the plan."""
    steps = [str(s) for s in (steps or []) if str(s).strip()][:PLAN_STEPS_MAX]
    if title and title.strip():
        item.title = _scrub(db, item.user_id, title.strip(), 120)
    if task_type:
        item.task_type = tasktypes.normalise(task_type)
    item.plan = [
        {"index": i, "text": _scrub(db, item.user_id, s.strip(), PLAN_TEXT_MAX), "status": "todo", "note": ""}
        for i, s in enumerate(steps)
    ]
    db.commit()
    return item.plan


def update_plan_step(db: Session, item: WorkItem, index: int, status: str, note: str = "") -> dict | None:
    """`aura_progress`. Returns the step, or None for an unknown index or status."""
    if status not in PLAN_STATUSES or status == "todo":
        return None
    plan = [dict(s) for s in (item.plan or [])]
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(plan):
        return None
    plan[index]["status"] = status
    plan[index]["note"] = _scrub(db, item.user_id, note or "", NOTE_MAX)
    item.plan = plan  # a new list: JSON columns are not mutation-tracked
    db.commit()
    return plan[index]


def note_tool(db: Session, item: WorkItem, tool_name: str, system: str = "") -> None:
    """Record a tool and its system once each, in first-use order. Bookkeeping tools are not listed."""
    if not tool_name or tool_name in BOOKKEEPING:
        return
    changed = False
    if tool_name not in (item.tools_used or []):
        item.tools_used = [*(item.tools_used or []), tool_name]
        changed = True
    if system and system not in (item.systems_used or []):
        item.systems_used = [*(item.systems_used or []), system]
        changed = True
    if changed:
        db.commit()


# ---------------------------------------------------------------------------
# Steps and verification
# ---------------------------------------------------------------------------


def add_step(
    db: Session,
    item: WorkItem,
    *,
    kind: str,
    status: str = "ok",
    step_no: int = 0,
    tool_name: str = "",
    tool_call_id: str = "",
    system: str = "",
    arguments: dict | None = None,
    summary: str = "",
    detail: dict | None = None,
) -> WorkStep:
    """Append a step with the next `seq`. Arguments, summary and detail are scrubbed."""
    if kind not in STEP_KINDS:
        raise ValueError(f"kind must be one of {STEP_KINDS}")
    last = db.scalar(select(func.max(WorkStep.seq)).where(WorkStep.work_item_id == item.id))
    seq = 0 if last is None else last + 1
    step = WorkStep(
        work_item_id=item.id,
        user_id=item.user_id,
        seq=seq,
        step_no=step_no,
        kind=kind,
        tool_name=tool_name[:120],
        tool_call_id=(tool_call_id or "")[:64],
        system=(system or "")[:60],
        arguments=redaction.scrub_json(db, item.user_id, arguments or {}),
        status=status,
        summary=_scrub(db, item.user_id, summary, STEP_SUMMARY_MAX),
        detail=redaction.scrub_json(db, item.user_id, detail or {}),
    )
    db.add(step)
    db.commit()
    db.refresh(step)
    return step


def finish_step(
    db: Session, step: WorkStep, status: str, summary: str | None = None, detail: dict | None = None
) -> WorkStep:
    """Settle a step that was `started` or `waiting`; `detail` is merged into the existing one."""
    step.status = status
    if summary is not None:
        step.summary = _scrub(db, step.user_id, summary, STEP_SUMMARY_MAX)
    if detail:
        step.detail = {**(step.detail or {}), **redaction.scrub_json(db, step.user_id, detail)}
    db.commit()
    return step


def verification_status(checks: list[dict]) -> str:
    """none (no checks) / passed (all) / failed (all failed) / partial (otherwise)."""
    if not checks:
        return "none"
    passed = sum(1 for c in checks if c.get("passed"))
    if passed == len(checks):
        return "passed"
    return "failed" if passed == 0 else "partial"


def add_check(db: Session, item: WorkItem, passed: bool, method: str, evidence: str, step_no: int = 0) -> WorkStep:
    """Append a verification check (`aura_check` or a code verifier) and recompute the status."""
    check = {
        "method": _scrub(db, item.user_id, method, 200),
        "passed": bool(passed),
        "evidence": _scrub(db, item.user_id, evidence, 300),
    }
    checks = [*((item.verification or {}).get("checks") or []), check]
    item.verification = {"status": verification_status(checks), "checks": checks}
    db.commit()
    return add_step(db, item, kind="verification", status="passed" if passed else "failed", step_no=step_no,
                    summary=f"{check['method']}: {check['evidence']}".strip(": "))


def steps_of(db: Session, item_id: str) -> list[WorkStep]:
    return list(db.scalars(select(WorkStep).where(WorkStep.work_item_id == item_id).order_by(WorkStep.seq)).all())


def is_lookup(db: Session, item: WorkItem) -> bool:
    """A lookup (spec 3): every executed tool read-only, no plan, no approval, at most 3 tool calls."""
    if item.plan:
        return False
    tools = 0
    for step in steps_of(db, item.id):
        if step.kind in ("approval", "reviewer_block"):
            return False
        if step.kind == "tool":
            tools += 1
            if not catalogue.is_read_only(step.tool_name):
                return False
    return tools <= 3


# ---------------------------------------------------------------------------
# Finalising
# ---------------------------------------------------------------------------


def index_text(item: WorkItem) -> str:
    return f"{item.title}\n{item.task_type}\n{(item.instruction or '')[:600]}\n{(item.summary or '')[:600]}"


def memory_allowed(db: Session, user_id: str, conversation_id: str | None) -> bool:
    """False when the user paused memory or the item's chat has memory off."""
    settings = db.scalars(select(UserSettings).where(UserSettings.user_id == user_id)).first()
    if settings is not None and settings.memory_paused:
        return False
    if conversation_id:
        convo = db.get(Conversation, conversation_id)
        if convo is not None and convo.memory_off:
            return False
    return True


def default_summary(db: Session, item: WorkItem) -> str:
    """"How it was done" when the model gave none: plan steps with their state, then
    `tool -> summary` per finished tool step (at most 8 lines), joined by '; '."""
    parts = [f"{s.get('text', '')} ({s.get('status', 'todo')})" for s in (item.plan or [])]
    tool_lines = [
        f"{s.tool_name} -> {s.summary or s.status}"
        for s in steps_of(db, item.id)
        if s.kind == "tool" and s.status in ("ok", "error", "denied", "interrupted")
    ]
    return "; ".join(parts + tool_lines[:8])


def finalise(
    db: Session,
    item: WorkItem,
    *,
    status: str,
    report: str,
    summary: str | None = None,
    outcome: str | None = None,
    error: str = "",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
    steps_total: int | None = None,
    embed: bool = True,
) -> WorkItem:
    """Close an item: scrub and cap the text, settle the type, decide recallability, embed.

    `status` is the loop's verdict (done / partly_done / failed / stopped). `report` is the
    final text, footer included. The loop state and the lease are cleared. Commits.
    """
    if status not in ("done", "partly_done", "failed", "stopped"):
        raise ValueError("finalise needs a terminal status")
    uid = item.user_id
    report = _scrub(db, uid, report, REPORT_MAX)
    item.report = report
    first_line = next((l.strip() for l in report.splitlines() if l.strip()), "")
    item.outcome = _scrub(db, uid, outcome if outcome is not None else first_line, OUTCOME_MAX)
    item.summary = _scrub(db, uid, summary if summary else default_summary(db, item), SUMMARY_MAX)
    item.error = _scrub(db, uid, error or item.error or "", 1000)
    if item.task_type in ("", "other"):
        item.task_type = tasktypes.from_tools(list(item.tools_used or []))
    if input_tokens is not None:
        item.input_tokens = input_tokens
    if output_tokens is not None:
        item.output_tokens = output_tokens
    if cost_usd is not None:
        item.cost_usd = cost_usd
    if steps_total is not None:
        item.steps_total = steps_total
    item.status = status
    item.wait_reason = ""
    item.finished_at = utcnow()
    item.lease_until = None
    item.lease_owner = ""
    item.loop_state = {}
    item.recallable = not is_lookup(db, item) and memory_allowed(db, uid, item.conversation_id)
    item.embedding = memory_service.embed_text(index_text(item)) if (embed and item.recallable) else []
    db.commit()
    db.refresh(item)
    return item


# ---------------------------------------------------------------------------
# Recall
# ---------------------------------------------------------------------------


@dataclass
class ScoredWork:
    item: WorkItem
    score: float


def _aware_ts(value) -> float:
    if value is None:
        return 0.0
    return (value if value.tzinfo else value.replace(tzinfo=utcnow().tzinfo)).timestamp()


def score_item(item: WorkItem, query: str, q_vec: list[float], task_type: str) -> float:
    if q_vec and item.embedding:
        base = memory_service.cosine(q_vec, list(item.embedding))
    else:
        base = memory_service.lexical(query, index_text(item))
    if base <= 0:
        # The type, status and feedback adjustments rank work that already resembles the request.
        # Alone they would put any finished item of the same type into the prompt (0.15 + 0.05
        # clears the 0.12 floor), so unrelated work is never recalled.
        return 0.0
    score = base
    if task_type and item.task_type == task_type:
        score += 0.15
    if item.status == "done":
        score += 0.05
    rating = (item.user_feedback or {}).get("rating")
    if rating == "up":
        score += 0.05
    elif rating == "down":
        score -= 0.10
    return score


def recall_work(
    db: Session,
    user_id: str,
    query: str,
    task_type: str,
    exclude_id: str | None = None,
    *,
    memory_off: bool = False,
    q_vec: list[float] | None = None,
    limit: int = TOP_WORK,
) -> list[ScoredWork]:
    """The best matches among the user's finished, recallable items (newest 300 considered).

    Skipped for a chat with memory off. Items without an embedding are embedded lazily (at most
    five per pass) when the provider can embed; lexical scoring is used whenever either side
    lacks a vector.
    """
    if memory_off or not (query or "").strip():
        return []
    stmt = (
        select(WorkItem)
        .where(WorkItem.user_id == user_id, WorkItem.recallable.is_(True), WorkItem.status.in_(FINISHED_STATUSES))
        .order_by(WorkItem.finished_at.desc(), WorkItem.id)
        .limit(CANDIDATES)
    )
    if exclude_id:
        stmt = stmt.where(WorkItem.id != exclude_id)
    items = list(db.scalars(stmt).all())
    if not items:
        return []
    if q_vec is None:
        q_vec = memory_service.embed_text(query)
    if q_vec:
        lazily = 0
        for it in items:
            if not it.embedding and lazily < LAZY_EMBED:
                it.embedding = memory_service.embed_text(index_text(it))
                lazily += 1
        if lazily:
            db.commit()
    scored = [ScoredWork(it, score_item(it, query, q_vec, task_type)) for it in items]
    scored = [s for s in scored if s.score >= MIN_SCORE]
    scored.sort(key=lambda s: (-s.score, -_aware_ts(s.item.finished_at)))
    return scored[:limit]


def _status_label(status: str) -> str:
    return status.replace("_", " ")


def format_line(item: WorkItem) -> str:
    """One recalled assignment, at most 350 characters."""
    when = (item.finished_at or item.created_at)
    date = when.strftime("%Y-%m-%d") if when else "----"
    head = f"- [{date} · {item.task_type} · {_status_label(item.status)}] "
    note = ((item.user_feedback or {}).get("note") or "").strip()
    tail = f' User feedback: "{note[:80]}".' if note and (item.user_feedback or {}).get("rating") == "down" else ""
    how = (item.summary or item.outcome or "").strip().replace("\n", " ")
    body = f"{item.title} — {how}" if how else item.title
    room = LINE_MAX - len(head) - len(tail)
    if len(body) > room:
        body = body[: max(0, room - 1)].rstrip() + "…"
    return head + body + tail


@dataclass
class Recall:
    """Everything recalled for a turn, and the prompt block built from it."""

    block: str = ""
    work: list[WorkItem] = field(default_factory=list)
    preferences: list[Preference] = field(default_factory=list)
    memory_ids: list[str] = field(default_factory=list)

    def counts(self) -> dict:
        return {"work_items": len(self.work), "preferences": len(self.preferences), "memories": len(self.memory_ids)}

    def used(self) -> dict:
        """The value stored in WorkItem.recall_used."""
        return {
            "work_item_ids": [w.id for w in self.work],
            "preference_ids": [p.id for p in self.preferences],
            "memory_ids": list(self.memory_ids),
        }


def build_recall(
    db: Session,
    user: User,
    query: str,
    task_type: str,
    *,
    systems: set[str] | None = None,
    conversation: Conversation | None = None,
    exclude_id: str | None = None,
    noted: list[str] | None = None,
    cap: int = CONTEXT_CAP,
) -> Recall:
    """Past work, preferences and facts for one turn, within `cap` characters (spec 9.5).

    Nothing at all for a chat with memory off. Over the cap, drop facts (lowest score first),
    then past work (lowest first), then general preferences (oldest first); lines for the
    task's own type go last.
    """
    if conversation is not None and conversation.memory_off:
        return Recall()
    q_vec = memory_service.embed_text(query) if (query or "").strip() else []
    work = recall_work(db, user.id, query, task_type, exclude_id, q_vec=q_vec)
    pref_rows = preference_service.select_rows(db, user.id, task_type, systems)
    pref_text, pref_kept = preference_service.format_block(pref_rows, task_type, noted)
    facts = memory_service.search(db, user.id, query, limit=FACT_LIMIT) if (query or "").strip() else []

    work_items = [w.item for w in work]  # best first
    work_lines = [format_line(i) for i in work_items]
    fact_lines = [f"- [{f.memory.kind}] {f.memory.content}"[:FACT_LINE_MAX] for f in facts]  # best first
    prefs = list(pref_kept)

    def compose() -> str:
        parts = []
        if work_lines:
            parts.append(PAST_HEADER + "\n" + "\n".join(work_lines))
        if prefs:
            parts.append(preference_service.format_block(prefs, task_type, noted, max_lines=10**6, max_chars=10**9)[0])
        if fact_lines:
            parts.append("What you know about this user (retrieved for this request):\n" + "\n".join(fact_lines))
        return "\n\n".join(parts)

    block = compose()
    while len(block) > cap and (fact_lines or work_lines or prefs):
        if fact_lines:
            fact_lines.pop()
            facts.pop()
        elif work_lines:
            work_lines.pop()
            work_items.pop()
        else:
            general = [p for p in prefs if p.task_type == "general"]
            prefs.remove(general[-1] if general else prefs[-1])
        block = compose()
    if len(block) > cap:  # only the "noted" line can be left over
        block = block[:cap]

    preference_service.mark_used(db, prefs)
    return Recall(block=block, work=work_items, preferences=prefs, memory_ids=[f.memory.id for f in facts])


def record_recall(db: Session, item: WorkItem, recall: Recall) -> None:
    item.recall_used = recall.used()
    db.commit()


def resolve_recall(db: Session, item: WorkItem) -> dict:
    """`recall_used` for the API: ids resolved to the lists the task panel shows. Rows that have
    since been deleted are left out."""
    used = item.recall_used or {}
    past: list[dict] = []
    for wid in used.get("work_item_ids") or []:
        w = db.get(WorkItem, wid)
        if w is not None and w.user_id == item.user_id:
            past.append({"id": w.id, "title": w.title, "outcome": w.outcome, "finished_at": w.finished_at})
    prefs: list[dict] = []
    for pid in used.get("preference_ids") or []:
        p = db.get(Preference, pid)
        if p is not None and p.user_id == item.user_id:
            prefs.append({"id": p.id, "task_type": p.task_type, "text": p.text})
    return {"past_work": past, "preferences": prefs}


# ---------------------------------------------------------------------------
# The owner's controls (spec 11)
# ---------------------------------------------------------------------------


def reembed(db: Session, item: WorkItem) -> None:
    item.embedding = memory_service.embed_text(index_text(item)) if item.recallable else []


def edit(
    db: Session,
    item: WorkItem,
    *,
    title: str | None = None,
    task_type: str | None = None,
    summary: str | None = None,
    recallable: bool | None = None,
) -> WorkItem:
    """PATCH: title, type, summary, recallable. Re-embeds when anything the index reads changed."""
    uid = item.user_id
    if title is not None:
        if not title.strip():
            raise ValueError("A title cannot be empty")
        item.title = _scrub(db, uid, title.strip(), TITLE_MAX)
    if task_type is not None:
        item.task_type = tasktypes.normalise(task_type)
    if summary is not None:
        item.summary = _scrub(db, uid, summary, SUMMARY_MAX)
    if recallable is not None:
        item.recallable = bool(recallable)
    reembed(db, item)
    db.commit()
    db.refresh(item)
    return item


def set_feedback(db: Session, user: User, item: WorkItem, rating: str, note: str = "") -> WorkItem:
    """Up / down with an optional note (500 characters). A down note also becomes a correction
    preference, origin `feedback`."""
    if rating not in ("up", "down"):
        raise ValueError("rating must be up or down")
    note = _scrub(db, user.id, (note or "").strip(), 500)
    item.user_feedback = {"rating": rating, "note": note, "at": utcnow().isoformat()}
    db.commit()
    if rating == "down" and note:
        preference_service.feedback_note(db, user, item, note)
    db.refresh(item)
    return item


def delete_item(db: Session, item: WorkItem) -> None:
    """Remove the item and its steps (links on other rows go to NULL). The caller stops a
    waiting / ready item first; a running one raises ItemBusy."""
    if item.status == "running":
        raise ItemBusy(item.id)
    db.delete(item)
    db.commit()


def reset(db: Session, user_id: str) -> int:
    """Delete every item that is not running, waiting or ready; returns how many."""
    rows = db.scalars(
        select(WorkItem).where(WorkItem.user_id == user_id, WorkItem.status.notin_(OPEN_STATUSES))
    ).all()
    for row in rows:
        db.delete(row)
    db.commit()
    return len(rows)
