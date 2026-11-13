"""The coordinator agent.

One loop: retrieve memory -> build prompt -> call model -> dispatch skills ->
feed results back -> repeat until it answers or the step budget runs out.

The coordinator orchestrates; it doesn't know what any individual tool does.
Capability lives in agent/tools.py, is grouped for the user in agent/skills.py,
and is gated by permissions/gate.py (every call goes through permissions.decide).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import permissions
from app.agent import skills as skill_registry
from app.agent import tools as tool_registry
from app.agent import taskloop
from app.agent.taskloop import AgentResult  # noqa: F401  (re-exported)
from app.agent.tools import ToolContext
from app.config import settings
from app.llm import get_provider
from app.models import (
    ActivityLog,
    Conversation,
    Message,
    PendingAction,
    User,
    utcnow,
)
from app.permissions import access, approvals
from app.services import google as google_service
from app.services import identity as identity_service
from app.services import memory as memory_service
from app.services import usage as usage_service
from app.services import vault as vault_service

log = logging.getLogger("aura.agent")

HISTORY_LIMIT = 20

# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


def execute_tool(
    db: Session,
    user: User,
    name: str,
    args: dict,
    trigger: str = "chat",
    log_skill: bool = True,
    *,
    work_item_id: str | None = None,
    decision: permissions.Decision,
) -> Any:
    """Run a tool by name, but only with the gate's sealed `allow` for exactly this call.

    This is the only function that calls a tool handler. It raises PermissionError
    for a missing, forged, refused or mismatched decision (wrong tool, wrong
    arguments, wrong user), so no code path can run a tool the gate did not allow.
    """
    if decision is None or not permissions.is_sealed(decision):
        raise PermissionError("no decision: a tool only runs with the permission gate's allow")
    if decision.kind != "allow":
        raise PermissionError(f"the gate did not allow {name} ({decision.reason_code})")
    if decision.tool != name:
        raise PermissionError("the decision is for a different tool")
    if decision.args_digest != permissions.args_digest(args if args is not None else {}):
        raise PermissionError("the arguments changed after the decision")
    if decision.user_id and decision.user_id != user.id:
        raise PermissionError("the decision is for a different user")

    entry = tool_registry.REGISTRY.get(name)
    if not entry:
        return {"error": f"Unknown tool: {name}"}

    started = time.perf_counter()
    ctx = ToolContext(db=db, user=user, decision=decision, work_item_id=work_item_id)
    try:
        # The user's connected folders, read now, for exactly this call (permissions/access.py).
        with access.bound(db, user):
            result = entry.handler(ctx, args or {})
    except Exception as exc:
        log.exception("Tool %s failed", name)
        db.rollback()
        db.add(
            ActivityLog(
                user_id=user.id, actor="agent", action=name,
                status="error", detail={"error": str(exc), "arguments": args},
                work_item_id=work_item_id,
            )
        )
        db.commit()
        result = {"error": f"{name} failed: {exc}"}

    if log_skill:
        skill_registry.record_run(
            db, user.id, name, result,
            trigger=trigger,
            duration_ms=int((time.perf_counter() - started) * 1000),
            work_item_id=work_item_id,
        )
    return result


def run_gated(
    db: Session,
    user: User,
    name: str,
    args: dict,
    ctx: permissions.GateContext,
    *,
    trigger: str | None = None,
    log_skill: bool = True,
) -> Any:
    """decide() then execute_tool(), for callers that never park a call for approval
    (the heartbeat, a user's own button). A call the gate does not allow comes back as
    {"error", "blocked": True, "reason_code"} and nothing runs."""
    decision = permissions.decide(db, user, name, args, ctx)
    if decision.kind != "allow":
        return {"error": decision.reason, "blocked": True, "reason_code": decision.reason_code}
    return execute_tool(db, user, name, args, trigger or ctx.trigger, log_skill,
                        work_item_id=ctx.work_item_id, decision=decision)


# ---------------------------------------------------------------------------
# Conversation assembly
# ---------------------------------------------------------------------------


# How much of an earlier turn's tool work is replayed into the next turn.
REPLAY_CALLS = 6
REPLAY_ARGS = 160


def _replay_note(meta: dict | None) -> str:
    """A compact record of what an earlier answer did, for the next turn.

    Earlier turns used to reach the model as their final text only. So after
    "add a task to call Priya" the model had no idea a task now existed, or
    what it was called, and "actually make that urgent" had nothing to refer
    to. The calls and their one-line outcomes ride along with the answer they
    produced — as text, which every provider accepts in any position.
    """
    if not meta:
        return ""
    calls = meta.get("tool_calls") or []
    runs = meta.get("skill_runs") or []
    pending = meta.get("pending_action_ids") or []
    if not calls:
        return ""
    runs = [r for r in runs if isinstance(r, dict)]
    # Runs name their tool (newer messages); older ones line up by position
    # only when every call produced a run.
    by_position = len(runs) == len(calls) and not any("tool" in r for r in runs)
    lines = []
    for i, call in enumerate(calls[:REPLAY_CALLS]):
        args = json.dumps(call.get("arguments") or {}, default=str, ensure_ascii=False)
        if len(args) > REPLAY_ARGS:
            args = args[:REPLAY_ARGS] + "…"
        outcome = ""
        if by_position:
            outcome = runs[i].get("summary", "")
        else:
            for j, run in enumerate(runs):
                if run.get("tool") == call.get("name"):
                    outcome = runs.pop(j).get("summary", "")
                    break
        lines.append(f"- {call.get('name')}({args})" + (f" → {outcome}" if outcome else ""))
    if len(calls) > REPLAY_CALLS:
        lines.append(f"- …and {len(calls) - REPLAY_CALLS} more")
    if pending:
        lines.append(f"- {len(pending)} action(s) were queued for the user's approval")
    return "\n\n[Tools I used for this answer:\n" + "\n".join(lines) + "]"


def _history(db: Session, conversation: Conversation) -> list[dict]:
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(HISTORY_LIMIT)
    ).all()
    out = []
    for m in reversed(rows):
        if m.role not in ("user", "assistant") or not m.content:
            continue
        content = m.content
        if m.role == "assistant":
            content += _replay_note(m.meta)
        out.append({"role": m.role, "content": content})
    return out


def _last_assistant_message(db: Session, conversation: Conversation) -> str:
    row = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id, Message.role == "assistant")
        .order_by(Message.created_at.desc())
        .limit(1)
    ).first()
    return row.content if row else ""


def _available_specs(db: Session, user: User):
    """Only skills the user has enabled reach the model."""
    allowed = skill_registry.enabled_tools(db, user.id)
    return [spec for spec in tool_registry.tool_specs() if spec.name in allowed]


# ---------------------------------------------------------------------------
# Main loop (the engine lives in agent/taskloop.py)
# ---------------------------------------------------------------------------


def iter_agent(
    db: Session,
    user: User,
    conversation: Conversation,
    user_message: str,
    trigger: str = "chat",
    origin_id: str = "",
    approval_mode: str | None = None,
) -> Generator[dict, None, AgentResult]:
    """Start the task loop for one owner message, reporting as it goes (spec 20 section 5.1)."""
    return (yield from taskloop.start_run(
        db, user, conversation, user_message, trigger, origin_id, approval_mode))


def iter_resume(
    db: Session, user: User, work_item_id: str, *, token: str | None = None, background: bool = False
) -> Generator[dict, None, AgentResult]:
    """Continue a paused work item: one more segment of the same run."""
    return (yield from taskloop.iter_resume(db, user, work_item_id, token=token, background=background))


def _drain(events: Generator[dict, None, AgentResult]) -> AgentResult:
    while True:
        try:
            next(events)
        except StopIteration as finished:
            return finished.value


def run_agent(
    db: Session,
    user: User,
    conversation: Conversation,
    user_message: str | None,
    trigger: str = "chat",
    origin_id: str = "",
    approval_mode: str | None = None,
    work_item_id: str | None = None,
) -> AgentResult:
    """The whole turn (or one more segment of `work_item_id`), without the running commentary."""
    if work_item_id:
        return _drain(iter_resume(db, user, work_item_id))
    return _drain(iter_agent(db, user, conversation, user_message, trigger=trigger,
                             origin_id=origin_id, approval_mode=approval_mode))


def persist_exchange(
    db: Session,
    conversation: Conversation,
    user_message: str | None,
    result: AgentResult,
) -> Message:
    """Write the user turn (unless this segment continues a run) and the assistant turn."""
    if user_message is not None:
        db.add(Message(conversation_id=conversation.id, role="user", content=user_message))

    assistant = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=result.text,
        meta={
            "tool_calls": [
                {"name": c["name"], "skill_code": c.get("skill_code", ""),
                 "arguments": c["arguments"]}
                for c in result.tool_calls
            ],
            "skill_runs": result.skill_runs,
            "memories_used": result.memories_used,
            "steps": result.steps,
            "latency_ms": result.latency_ms,
            "tokens": result.input_tokens + result.output_tokens,
            "cost_usd": round(result.cost_usd, 5),
            "capped": result.capped,
            "pending_action_ids": [a.id for a in result.pending_actions],
            "work_item_id": result.work_item_id,
            "work_status": result.work_status,
            "preferences_noted": result.preferences_noted,
        },
    )
    db.add(assistant)

    if user_message is not None and conversation.title in ("", "New conversation"):
        conversation.title = user_message.strip().split("\n")[0][:60] or "New conversation"
    conversation.updated_at = utcnow()
    db.commit()
    db.refresh(assistant)

    # The preferences this turn captured now know which message they came from.
    ids = [p["id"] for p in result.preferences_noted]
    if ids and user_message is not None:
        from sqlalchemy import update

        from app.models import Preference

        last_user = db.scalars(
            select(Message).where(Message.conversation_id == conversation.id, Message.role == "user")
            .order_by(Message.created_at.desc()).limit(1)
        ).first()
        if last_user is not None:
            db.execute(update(Preference).where(Preference.id.in_(ids), Preference.origin_message_id.is_(None))
                       .values(origin_message_id=last_user.id))
            db.commit()
    return assistant


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


def stream_agent(
    db: Session,
    user: User,
    conversation: Conversation,
    user_message: str | None,
    trigger: str = "chat",
    *,
    work_item_id: str | None = None,
    token: str | None = None,
) -> Iterator[dict]:
    """Yield progress events for SSE (spec 20 section 8), then the answer in chunks, then `done`.

    With `work_item_id` this continues a paused run (`token` is the lease the route already took).
    The answer arrives once complete: the provider adapters make non-streaming calls so tool use
    behaves identically across providers.
    """
    yield {"type": "status", "value": "thinking"}

    if work_item_id:
        events = iter_resume(db, user, work_item_id, token=token)
    else:
        events = iter_agent(db, user, conversation, user_message, trigger=trigger)
    while True:
        try:
            event = next(events)
        except StopIteration as finished:
            result: AgentResult = finished.value
            break
        if event.get("type") == "status" and event.get("step") == 1:
            continue  # already said "thinking" above
        yield event

    text = result.text
    chunk = 48
    for i in range(0, len(text), chunk):
        yield {"type": "delta", "value": text[i : i + chunk]}

    message = persist_exchange(db, conversation, user_message, result)
    yield {
        "type": "done",
        "conversation_id": conversation.id,
        "message_id": message.id,
        "latency_ms": result.latency_ms,
        "steps": result.steps,
        "tokens": result.input_tokens + result.output_tokens,
        "cost_usd": round(result.cost_usd, 5),
        "work_item_id": result.work_item_id,
        "work_status": result.work_status or None,
    }


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


def resolve_pending(
    db: Session, user: User, action: PendingAction, decision: str, minutes: int = 10
) -> dict:
    """Answer a parked call as the signed-in owner. Kept for callers that hold a PendingAction;
    the work is permissions/approvals.resolve (the API calls that directly, with who is asking).
    `minutes` is ignored: a "for 10 minutes" answer is always ten minutes. A refused answer
    comes back as {"error", "status_code", ...} as it always did."""
    try:
        return approvals.resolve(db, user, action.id, decision, approvals.Caller())
    except approvals.ApprovalError as exc:
        return {"error": exc.detail, "status_code": exc.status_code, **exc.extra}
