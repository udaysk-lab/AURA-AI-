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
from app.agent import autonomy
from app.agent import skills as skill_registry
from app.agent import tools as tool_registry
from app.agent.prompts import build_system_prompt
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
    ctx = ToolContext(db=db, user=user, decision=decision)
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


def _prelude(
    db: Session, user: User, user_message: str, tier: str
) -> tuple[list[dict], list[str]]:
    memory_block, memory_ids = memory_service.build_context_block(db, user.id, user_message)
    assistant = identity_service.get_or_create(db, user)

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
    return lesson


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def iter_agent(
    db: Session,
    user: User,
    conversation: Conversation,
    user_message: str,
    autonomy_level: str = "conservative",
    trigger: str = "chat",
    origin_id: str = "",
    approval_mode: str | None = None,
) -> Generator[dict, None, AgentResult]:
    """The agent loop, reporting as it goes.

    Yields progress events while it works — which tool is running now, what a
    finished one found, what was queued for approval — and returns the
    AgentResult when it's done. `run_agent` drains it; `stream_agent` forwards
    every event to the browser as it happens, so a turn that takes sixteen
    steps (look at the screen, click, look again) shows each one live instead
    of a spinner and then everything at once.

    `trigger` drives both the skill-run log and the spend budget: background
    work is capped at a fraction of the daily allowance so it can never starve
    the interactive budget the user actually notices.
    """
    started = time.perf_counter()
    tier = autonomy.normalise_tier(autonomy_level)
    result = AgentResult()

    # Only the signed-in owner's own chat teaches AURA anything: a schedule's stored prompt and,
    # above all, a message that came in through a channel never become learned notes or
    # preferences (decisions 7 and 11).
    result.learned = maybe_learn(db, user, conversation, user_message) if trigger == "chat" else ""
    if result.learned:
        yield {"type": "learned", "value": result.learned}

    messages, result.memories_used = _prelude(db, user, user_message, tier)
    if result.memories_used:
        yield {"type": "memory", "count": len(result.memories_used)}
    messages += _history(db, conversation)
    messages.append({"role": "user", "content": user_message})

    provider = get_provider()
    specs = _available_specs(db, user)
    # One gate context per run. `legacy_tier` keeps the 1.0 tier in force until the
    # legacy data migration has turned it into a permission mode and rules (WP11).
    gate_ctx = permissions.build_context(
        db, user, trigger,
        conversation_id=conversation.id, instruction=user_message,
        origin_id=origin_id, approval_mode=approval_mode, legacy_tier=tier,
    )
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
            yield {"type": "capped", "value": True}
            result.text = (
                f"I've stopped here — today's spend cap of "
                f"${capped.state.cap_usd:.2f} has been reached "
                f"(${capped.state.spent_usd:.2f} used across {capped.state.calls} calls). "
                "Nothing was left half-done. Raise `DAILY_SPEND_CAP_USD` or wait for "
                "the daily reset."
            )
            break

        yield {"type": "status", "value": "thinking", "step": step + 1}
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

            decision = permissions.decide(db, user, call.name, call.arguments, gate_ctx)
            if decision.source == "reviewer":
                if decision.kind == "allow":
                    gate_ctx.reviewer_streak = 0
                else:
                    gate_ctx.reviewer_streak += 1
                    gate_ctx.reviewer_total += 1

            if decision.kind == "ask":
                action = approvals.queue_action(db, user, gate_ctx, call, decision)
                result.pending_actions.append(action)
                yield {
                    "type": "pending_action",
                    "id": action.id,
                    "tool_name": action.tool_name,
                    "skill_code": action.skill_code,
                    "preview": action.preview,
                    "card": action.card,
                    "options": list(action.options or []),
                    "always_user": bool(action.always_user),
                    "reason": action.reason,
                    "conversation_id": action.conversation_id,
                }
                payload: Any = {
                    "status": "awaiting_approval",
                    "approval_id": action.id,
                    "reason": decision.reason,
                    "message": (
                        f"{call.name} is queued and waiting for the user to approve it. "
                        "Tell them what is pending, then stop."
                    ),
                    "preview": action.preview,
                    "pending_action_id": action.id,
                }
            elif decision.kind == "deny":
                payload = {
                    "status": "blocked",
                    "error": decision.reason,  # what clients of the old refusal shape read
                    "reason": decision.reason,
                    "reason_code": decision.reason_code,
                    "message": "Not run. Try another acceptable way, or explain to the user.",
                }
                if decision.source == "reviewer":
                    payload["reviewer"] = True
            else:
                yield {
                    "type": "tool_start",
                    "name": call.name,
                    "code": skill.code if skill else "",
                    "label": skill_registry.activity_label(call.name, call.arguments),
                }
                payload = execute_tool(
                    db, user, call.name, call.arguments, trigger=trigger, decision=decision
                )
                if skill:
                    run = {
                        "code": skill.code,
                        "name": skill.name,
                        "summary": skill_registry.summarise(call.name, payload),
                        "ok": not (isinstance(payload, dict) and payload.get("error")),
                        "tool": call.name,
                    }
                    result.skill_runs.append(run)
                    yield {"type": "skill", **run}

            # An image a tool produced (a screenshot) travels separately from
            # its tool result: both providers take an image on a user turn,
            # while only one takes it inside a tool result. Lift it out before
            # the payload goes anywhere else, so a megabyte of base64 isn't
            # carried through the result object, the stream and the HTTP
            # response for the sake of one model call.
            image = None
            if isinstance(payload, dict) and isinstance(payload.get("image"), dict):
                image = payload["image"]
                payload = {k: v for k, v in payload.items() if k != "image"}

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
            if image and image.get("data"):
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"[{call.name}] what it captured:"},
                            {
                                "type": "image",
                                "media_type": image.get("media_type", "image/png"),
                                "data": image["data"],
                            },
                        ],
                    }
                )
    else:
        result.text = result.text or (
            "I ran out of steps on that one. Here's where I got to — ask me to continue "
            "if you want me to keep going."
        )

    if not result.text:
        result.text = "Done."

    result.latency_ms = int((time.perf_counter() - started) * 1000)
    return result


def run_agent(
    db: Session,
    user: User,
    conversation: Conversation,
    user_message: str,
    autonomy_level: str = "conservative",
    trigger: str = "chat",
    origin_id: str = "",
    approval_mode: str | None = None,
) -> AgentResult:
    """The whole turn, without the running commentary."""
    events = iter_agent(
        db, user, conversation, user_message,
        autonomy_level=autonomy_level, trigger=trigger,
        origin_id=origin_id, approval_mode=approval_mode,
    )
    while True:
        try:
            next(events)
        except StopIteration as finished:
            return finished.value


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
    """Yield progress events for SSE, as they happen.

    Each tool call is announced when it starts (`tool_start`, with a plain
    label like "Reading your inbox") and reported when it finishes (`skill`).
    The answer itself still arrives in chunks once it's complete: the provider
    adapters make non-streaming calls so tool use behaves identically across
    providers. Token streaming would only change OpenAIProvider and
    AnthropicProvider.
    """
    yield {"type": "status", "value": "thinking"}

    events = iter_agent(
        db, user, conversation, user_message,
        autonomy_level=autonomy_level, trigger=trigger,
    )
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
