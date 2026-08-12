"""The coordinator agent.

One loop: retrieve memory -> build prompt -> call model -> dispatch skills ->
feed results back -> repeat until it answers or the step budget runs out.

The coordinator orchestrates; it doesn't know what any individual tool does.
Capability lives in agent/tools.py, is grouped for the user in agent/skills.py,
and is gated by agent/autonomy.py.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import autonomy
from app.agent import skills as skill_registry
from app.agent import tools as tool_registry
from app.agent.prompts import build_system_prompt
from app.agent.tools import ToolContext
from app.config import settings
from app.llm import ToolCall, get_provider
from app.models import (
    ActivityLog,
    Conversation,
    Message,
    PendingAction,
    User,
    utcnow,
)
from app.services import google as google_service
from app.services import identity as identity_service
from app.services import memory as memory_service
from app.services import usage as usage_service
from app.services import vault as vault_service

log = logging.getLogger("aura.agent")

HISTORY_LIMIT = 20

# Phrases that mark a turn as a correction rather than a new request. Cheap and
# deterministic — the model is only consulted when one of these fires.
CORRECTION_MARKERS = re.compile(
    r"\b(no,|not like that|don't do that|stop doing|that's wrong|wrong again|"
    r"i said|i told you|next time|from now on|actually,? (i|please)|"
    r"never (do|send|schedule|book)|always (do|send|use|ask))\b",
    re.IGNORECASE,
)

SKILL_HINT_TO_CODE = {
    "email": "EM02",
    "calendar": "CA01",
    "tasks": "TK01",
    "memory": "MM01",
    "meetings": "MP01",
    "general": "MM01",
}


@dataclass
class AgentResult:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    skill_runs: list[dict[str, Any]] = field(default_factory=list)
    memories_used: list[str] = field(default_factory=list)
    pending_actions: list[PendingAction] = field(default_factory=list)
    steps: int = 0
    latency_ms: int = 0
    learned: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    capped: bool = False


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
) -> Any:
    """Run a tool by name. Used by the agent loop, approvals, heartbeat and automations."""
    entry = tool_registry.REGISTRY.get(name)
    if not entry:
        return {"error": f"Unknown tool: {name}"}

    started = time.perf_counter()
    ctx = ToolContext(db=db, user=user)
    try:
        result = entry.handler(ctx, args or {})
    except Exception as exc:
        log.exception("Tool %s failed", name)
        db.rollback()
        db.add(
            ActivityLog(
                user_id=user.id, actor="agent", action=name,
                status="error", detail={"error": str(exc), "arguments": args},
            )
        )
        db.commit()
        result = {"error": f"{name} failed: {exc}"}

    if log_skill:
        skill_registry.record_run(
            db, user.id, name, result,
            trigger=trigger,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    return result


def _queue_action(
    db: Session, user: User, conversation_id: str | None, call: ToolCall
) -> PendingAction:
    skill = skill_registry.skill_for_tool(call.name)
    action = PendingAction(
        user_id=user.id,
        conversation_id=conversation_id,
        tool_name=call.name,
        arguments=call.arguments,
        preview=tool_registry.build_preview(call.name, call.arguments),
        skill_code=skill.code if skill else "",
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


# ---------------------------------------------------------------------------
# Conversation assembly
# ---------------------------------------------------------------------------


def _history(db: Session, conversation: Conversation) -> list[dict]:
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(HISTORY_LIMIT)
    ).all()
    return [
        {"role": m.role, "content": m.content}
        for m in reversed(rows)
        if m.role in ("user", "assistant") and m.content
    ]


def _last_assistant_message(db: Session, conversation: Conversation) -> str:
    row = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id, Message.role == "assistant")
        .order_by(Message.created_at.desc())
        .limit(1)
    ).first()
    return row.content if row else ""


def _prelude(
    db: Session, user: User, user_message: str, tier: str
) -> tuple[list[dict], list[str]]:
    memory_block, memory_ids = memory_service.build_context_block(db, user.id, user_message)
    assistant = identity_service.refresh_stage(db, user)

    # The vault contributes key *names* only — never values. See services/vault.py.
    learned = skill_registry.learned_notes_block(db, user.id)
    vault_block = vault_service.prompt_block(db, user.id)
    if vault_block:
        learned = f"{learned}\n{vault_block}" if learned else vault_block

    system = build_system_prompt(
        user_name=user.name,
        user_email=user.email,
        timezone=user.timezone,
        persona=identity_service.persona_prompt(assistant),
        autonomy=tier,
        memory_block=memory_block,
        learned_block=learned,
        google_connected=google_service.is_connected(db, user.id),
    )
    return [{"role": "system", "content": system}], memory_ids


def _available_specs(db: Session, user: User):
    """Only skills the user has enabled reach the model."""
    allowed = skill_registry.enabled_tools(db, user.id)
    return [spec for spec in tool_registry.tool_specs() if spec.name in allowed]


# ---------------------------------------------------------------------------
# Learning from corrections
# ---------------------------------------------------------------------------


def maybe_learn(db: Session, user: User, conversation: Conversation, message: str) -> str:
    """If this turn is a correction, store the lesson against the right skill."""
    if not CORRECTION_MARKERS.search(message):
        return ""

    previous = _last_assistant_message(db, conversation)
    provider = get_provider()

    lesson, hint = "", "general"
    if provider.name != "mock":
        from app.agent.prompts import CORRECTION_DETECTOR

        try:
            resp = provider.complete(
                [
                    {"role": "system", "content": "You extract durable lessons. JSON only."},
                    {
                        "role": "user",
                        "content": CORRECTION_DETECTOR.format(
                            previous=previous[:1500] or "(none)", message=message[:1500]
                        ),
                    },
                ]
            )
            match = re.search(r"\{.*\}", resp.text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                if data.get("is_correction") and data.get("lesson"):
                    lesson = str(data["lesson"])[:160]
                    hint = str(data.get("skill_hint", "general"))
        except Exception as exc:
            log.warning("Correction extraction failed: %s", exc)

    if not lesson:
        # Offline fallback: keep the user's own words, trimmed.
        lesson = message.strip()[:160]

    code = SKILL_HINT_TO_CODE.get(hint, "MM01")
    skill_registry.teach(db, user.id, code, lesson)
    memory_service.remember(
        db, user.id, lesson, kind="preference", source="correction", confidence=0.95
    )
    identity_service.record_correction(db, user)
    return lesson


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_agent(
    db: Session,
    user: User,
    conversation: Conversation,
    user_message: str,
    autonomy_level: str = "conservative",
    trigger: str = "chat",
) -> AgentResult:
    """`trigger` drives both the skill-run log and the spend budget: background
    work is capped at a fraction of the daily allowance so it can never starve
    the interactive budget the user actually notices."""
    started = time.perf_counter()
    tier = autonomy.normalise_tier(autonomy_level)
    result = AgentResult()

    result.learned = maybe_learn(db, user, conversation, user_message)

    messages, result.memories_used = _prelude(db, user, user_message, tier)
    messages += _history(db, conversation)
    messages.append({"role": "user", "content": user_message})

    provider = get_provider()
    specs = _available_specs(db, user)
    cheap_model = (
        settings.background_model
        if settings.background_model and trigger != "chat"
        else None
    )

    for step in range(settings.max_agent_steps):
        result.steps = step + 1

        # Checked before the call, never after — refusing to start is
        # recoverable, discovering you're over budget is not.
        try:
            usage_service.check(db, user.id, trigger=trigger)
        except usage_service.SpendCapReached as capped:
            log.warning("Spend cap hit for %s (%s)", user.email, trigger)
            result.capped = True
            result.text = (
                f"I've stopped here — today's spend cap of "
                f"${capped.state.cap_usd:.2f} has been reached "
                f"(${capped.state.spent_usd:.2f} used across {capped.state.calls} calls). "
                "Nothing was left half-done. Raise `DAILY_SPEND_CAP_USD` or wait for "
                "the daily reset."
            )
            break

        try:
            # Background work runs orders of magnitude more often than anyone
            # chats and needs far less judgement, so it gets the cheap model
            # when one is configured.
            response = provider.complete(messages, specs, model=cheap_model)
        except Exception as exc:
            log.exception("Model call failed")
            result.text = (
                "I couldn't reach the model just now. Your data is untouched — "
                f"try again in a moment.\n\n`{exc}`"
            )
            break

        usage_service.record(db, user.id, response.usage, trigger=trigger)
        result.input_tokens += response.usage.input_tokens
        result.output_tokens += response.usage.output_tokens
        result.cost_usd += usage_service.estimate_cost(response.usage)

        if not response.tool_calls:
            result.text = response.text or "Done."
            break

        messages.append(
            {"role": "assistant", "content": response.text, "tool_calls": response.tool_calls}
        )

        for call in response.tool_calls:
            skill = skill_registry.skill_for_tool(call.name)

            if autonomy.requires_approval(db, user.id, call.name, tier):
                action = _queue_action(db, user, conversation.id, call)
                result.pending_actions.append(action)
                payload: Any = {
                    "status": "awaiting_approval",
                    "message": (
                        f"{call.name} is queued and waiting for the user to approve it. "
                        "Tell them what is pending, then stop."
                    ),
                    "pending_action_id": action.id,
                }
            else:
                payload = execute_tool(db, user, call.name, call.arguments, trigger=trigger)
                if skill:
                    result.skill_runs.append(
                        {
                            "code": skill.code,
                            "name": skill.name,
                            "summary": skill_registry.summarise(call.name, payload),
                            "ok": not (isinstance(payload, dict) and payload.get("error")),
                        }
                    )

            result.tool_calls.append(
                {
                    "name": call.name,
                    "skill_code": skill.code if skill else "",
                    "arguments": call.arguments,
                    "result": payload,
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(payload, default=str)[:8000],
                }
            )
    else:
        result.text = result.text or (
            "I ran out of steps on that one. Here's where I got to — ask me to continue "
            "if you want me to keep going."
        )

    if not result.text:
        result.text = "Done."

    identity_service.record_interaction(db, user, actions=len(result.skill_runs))
    result.latency_ms = int((time.perf_counter() - started) * 1000)
    return result


def persist_exchange(
    db: Session,
    conversation: Conversation,
    user_message: str,
    result: AgentResult,
) -> Message:
    """Write the user turn and the assistant turn, and title new conversations."""
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
            "learned": result.learned,
            "tokens": result.input_tokens + result.output_tokens,
            "cost_usd": round(result.cost_usd, 5),
            "capped": result.capped,
            "pending_action_ids": [a.id for a in result.pending_actions],
        },
    )
    db.add(assistant)

    if conversation.title in ("", "New conversation"):
        conversation.title = user_message.strip().split("\n")[0][:60] or "New conversation"
    conversation.updated_at = utcnow()

    db.commit()
    db.refresh(assistant)
    return assistant


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


def stream_agent(
    db: Session,
    user: User,
    conversation: Conversation,
    user_message: str,
    autonomy_level: str = "conservative",
    trigger: str = "chat",
) -> Iterator[dict]:
    """Yield progress events for SSE.

    Note: this streams *skill activity* and then the final text in chunks. It is
    not token-level streaming — the provider adapters use non-streaming calls so
    tool-calling stays identical across providers. Swapping in token streaming
    means changing only OpenAIProvider/AnthropicProvider.
    """
    yield {"type": "status", "value": "thinking"}

    result = run_agent(
        db, user, conversation, user_message,
        autonomy_level=autonomy_level, trigger=trigger,
    )

    if result.capped:
        yield {"type": "capped", "value": True}

    if result.learned:
        yield {"type": "learned", "value": result.learned}

    for run in result.skill_runs:
        yield {"type": "skill", **run}

    if result.memories_used:
        yield {"type": "memory", "count": len(result.memories_used)}

    for action in result.pending_actions:
        yield {
            "type": "pending_action",
            "id": action.id,
            "tool_name": action.tool_name,
            "skill_code": action.skill_code,
            "preview": action.preview,
        }

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
    }


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------

DECISIONS = ("reject", "once", "always", "window")


def resolve_pending(
    db: Session, user: User, action: PendingAction, decision: str, minutes: int = 10
) -> dict:
    """decision: reject | once | always | window

    'always' and 'window' also create a standing grant, so the same action stops
    asking. That is the mechanism by which trust actually accrues.
    """
    if action.status != "pending":
        return {"error": f"Action already {action.status}"}
    if decision not in DECISIONS:
        return {"error": f"Unknown decision: {decision}"}

    if decision == "reject":
        action.status = "rejected"
        action.resolved_at = utcnow()
        db.add(
            ActivityLog(
                user_id=user.id, actor="user", action=f"reject:{action.tool_name}",
                target=action.preview[:300], status="rejected",
            )
        )
        db.commit()
        return {"message": f"Cancelled — {action.tool_name} was not run."}

    action.status = "approved"
    action.resolved_at = utcnow()
    db.commit()

    granted = None
    if decision in ("always", "window"):
        granted = autonomy.grant(
            db, user.id, action.tool_name,
            scope="always" if decision == "always" else "window",
            minutes=minutes,
        )

    outcome = execute_tool(db, user, action.tool_name, action.arguments, trigger="manual")
    db.add(
        ActivityLog(
            user_id=user.id, actor="user", action=f"approve:{action.tool_name}",
            target=action.preview[:300],
            detail={"decision": decision, "result": str(outcome)[:500]},
        )
    )
    db.commit()

    payload = outcome if isinstance(outcome, dict) else {"result": outcome}
    if granted:
        payload["grant"] = {
            "tool_name": granted.tool_name,
            "scope": granted.scope,
            "expires_at": granted.expires_at.isoformat() if granted.expires_at else None,
        }
    return payload
