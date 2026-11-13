"""The task loop: understand, recall, plan, act through the gate, verify, report (spec 20 sections 5-6).

`coordinator.iter_agent` starts a `TaskRun`; `coordinator.iter_resume` continues one. Both drive
`TaskRun.segment()`, a generator of progress events that returns an `AgentResult`.

How a turn runs
  * Before the first model call the owner's message is read for preferences (owner chat only) and
    similar past work and the preferences for this kind of task are recalled (pure database work).
  * A `WorkItem` is created lazily, at the first model response that calls a real tool or
    `aura_plan`. A turn answered without tools costs what it always cost and leaves no row.
  * Every tool call goes through `permissions.decide`. `ask` parks the call as a PendingAction and
    the run waits; `deny` returns the reason to the model; `allow` runs it, and a code verifier
    reads back what changed. The bookkeeping tools are handled here and never reach the gate.
  * The run state is checkpointed to `work_items.loop_state` before and after every call, so a run
    can stop at an approval, a budget or a crash and be resumed by `claim_for_resume` +
    `iter_resume`. The approved tool runs inside the decision request; `on_pending_resolved`
    (called by permissions/approvals.py) stores its result, and the resumed model sees it.
  * Finishing applies the outcome table (5.5), adds missing report sections in code, and closes the
    item through `workmemory.finalise`.

Segments, leases and stop are spec 6.3 to 6.6. The worker that continues `ready` items on its own
(`resume_due`) belongs to the continuation package; `recover_stale` is here because it is the same
state machine.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import permissions
from app.agent import skills as skill_registry
from app.agent import verifiers
from app.agent.prompts import build_system_prompt
from app.config import settings
from app.llm import ToolCall, ToolSpec
from app.models import (
    ActivityLog,
    Conversation,
    Message,
    PendingAction,
    Preference,
    UsageRecord,
    User,
    WorkItem,
    WorkStep,
    utcnow,
)
from app.permissions import approvals, catalogue
from app.services import google as google_service
from app.services import identity as identity_service
from app.services import preferences as preference_service
from app.services import redaction, tasktypes
from app.services import usage as usage_service
from app.services import vault as vault_service
from app.services import workmemory

log = logging.getLogger("aura.taskloop")

# Bookkeeping tools: handled here, never in tools.REGISTRY, never gated.
BOOKKEEPING_NAMES = ("aura_plan", "aura_progress", "aura_check", "aura_outcome", "aura_note_preference")

LEASE_GRACE_SECONDS = 60
STATE_VERSION = 1
STATE_MAX_CHARS = 300_000
OLD_RESULT_CHARS = 1_500
TOOL_MESSAGE_CHARS = 8_000
IMAGE_PLACEHOLDER = "[screenshot taken earlier; take a new one if you need it]"

STOPPED_TEXT = "Stopped. Nothing further was done."
WORSE = {"done": 0, "partly_done": 1, "failed": 2}


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class AgentResult:
    """What one segment of a run produced (the whole turn, for a run that never parks)."""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    skill_runs: list[dict[str, Any]] = field(default_factory=list)
    memories_used: list[str] = field(default_factory=list)
    pending_actions: list[PendingAction] = field(default_factory=list)
    steps: int = 0
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    capped: bool = False
    work_item_id: str | None = None
    work_status: str = ""
    preferences_noted: list[dict[str, str]] = field(default_factory=list)


class Busy(Exception):
    """The item cannot be continued now: someone else holds it, it is not ready, or it is finished."""

    def __init__(self, status: str, work_item_id: str = "") -> None:
        super().__init__(f"work item is {status}")
        self.status = status
        self.work_item_id = work_item_id


class NotFound(Exception):
    """No such item for this user (the API answers 404)."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def get_provider():
    """The model provider. Looked up through the coordinator so a test (or a deployment) that
    replaces `coordinator.get_provider` replaces it for the loop too."""
    from app.agent import coordinator

    return coordinator.get_provider()


def _coord():
    from app.agent import coordinator

    return coordinator


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    dt = _aware(dt)
    return dt.isoformat() if dt else None


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _lease_until() -> datetime:
    return utcnow() + timedelta(seconds=settings.segment_seconds + LEASE_GRACE_SECONDS)


def _error_flag(payload: Any) -> bool:
    return isinstance(payload, dict) and bool(payload.get("error"))


def _system_of(tool: str) -> str:
    from app import plugins

    plugin = plugins.plugin_for_tool(tool)
    return plugin.id if plugin is not None else ""


# ---------------------------------------------------------------------------
# Bookkeeping tool specs (about 450 tokens in all)
# ---------------------------------------------------------------------------


def bookkeeping_specs(*, preferences: bool) -> list[ToolSpec]:
    types = list(tasktypes.TYPES)
    specs = [
        ToolSpec(
            "aura_plan",
            "Set out a short plan for work with more than one step. Call it once, first.",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 120},
                    "task_type": {"type": "string", "enum": types},
                    "steps": {"type": "array", "items": {"type": "string", "maxLength": 160},
                              "minItems": 1, "maxItems": 12},
                },
                "required": ["title", "task_type", "steps"],
            },
        ),
        ToolSpec(
            "aura_progress",
            "Mark a plan step as you work through it.",
            {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "minimum": 0},
                    "status": {"type": "string", "enum": ["doing", "done", "skipped", "failed"]},
                    "note": {"type": "string", "maxLength": 200},
                },
                "required": ["index", "status"],
            },
        ),
        ToolSpec(
            "aura_check",
            "Record how you checked that something you changed is really so.",
            {
                "type": "object",
                "properties": {
                    "passed": {"type": "boolean"},
                    "method": {"type": "string", "maxLength": 200},
                    "evidence": {"type": "string", "maxLength": 300},
                },
                "required": ["passed", "method"],
            },
        ),
        ToolSpec(
            "aura_outcome",
            "Say how the assignment ended, just before your report.",
            {
                "type": "object",
                "properties": {
                    "outcome": {"type": "string", "enum": ["done", "partly_done", "failed"]},
                    "how": {"type": "string", "maxLength": 800},
                },
                "required": ["outcome"],
            },
        ),
    ]
    if preferences:
        specs.append(
            ToolSpec(
                "aura_note_preference",
                "Keep a lasting preference the user just stated in their own message. "
                "`quote` must be their exact words.",
                {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "maxLength": 300},
                        "task_type": {"type": "string", "enum": [*types, "general"]},
                        "quote": {"type": "string", "maxLength": 300},
                    },
                    "required": ["text", "task_type", "quote"],
                },
            )
        )
    return specs


# ---------------------------------------------------------------------------
# Loop state (spec 6.1)
# ---------------------------------------------------------------------------


def _call_to_dict(call: ToolCall | dict) -> dict:
    if isinstance(call, dict):
        return {"id": call.get("id", ""), "name": call.get("name", ""), "arguments": call.get("arguments") or {}}
    return {"id": call.id, "name": call.name, "arguments": call.arguments}


def _dump_message(m: dict) -> dict:
    out = dict(m)
    if m.get("tool_calls"):
        out["tool_calls"] = [_call_to_dict(c) for c in m["tool_calls"]]
    content = m.get("content")
    if isinstance(content, list):  # an image part never goes into the saved state
        out["content"] = [
            {"type": "text", "text": IMAGE_PLACEHOLDER} if isinstance(p, dict) and p.get("type") == "image" else p
            for p in content
        ]
    return out


def _load_message(m: dict) -> dict:
    out = dict(m)
    if m.get("tool_calls"):
        out["tool_calls"] = [
            ToolCall(id=str(c.get("id", "")), name=str(c.get("name", "")), arguments=dict(c.get("arguments") or {}))
            for c in m["tool_calls"]
        ]
    return out


@dataclass
class LoopState:
    trigger: str = "chat"
    origin_ref: str = ""
    approval_mode: str | None = None
    conversation_id: str | None = None
    owner_message: str = ""
    turn_type: str = "other"
    messages: list[dict] = field(default_factory=list)  # no system message
    recall_block: str = ""
    open_step: dict | None = None
    steps_total: int = 0
    segment: int = 0
    reviewer: dict = field(default_factory=lambda: {"streak": 0, "total": 0})
    model_outcome: dict | None = None
    preferences_noted: list[str] = field(default_factory=list)
    usage_ids: list[str] = field(default_factory=list)
    tokens: dict = field(default_factory=lambda: {"input": 0, "output": 0, "cost": 0.0})

    def dump(self) -> dict:
        messages = [_dump_message(m) for m in self.messages]
        data = {
            "v": STATE_VERSION, "trigger": self.trigger, "origin_ref": self.origin_ref,
            "approval_mode": self.approval_mode, "conversation_id": self.conversation_id,
            "owner_message": self.owner_message, "turn_type": self.turn_type, "messages": messages,
            "recall_block": self.recall_block, "open_step": self.open_step, "steps_total": self.steps_total,
            "segment": self.segment, "reviewer": dict(self.reviewer), "model_outcome": self.model_outcome,
            "preferences_noted": list(self.preferences_noted), "usage_ids": list(self.usage_ids),
            "tokens": dict(self.tokens),
        }
        # A deep copy through JSON: the saved value must not share objects with the live state, or
        # SQLAlchemy sees "no change" when a nested dict was edited in place.
        text = _dumps(data)
        data = json.loads(text)
        if len(text) > STATE_MAX_CHARS:
            # Cut the tool results older than the last few messages; the model has already used them.
            for m in data["messages"][:-6]:
                if m.get("role") == "tool" and isinstance(m.get("content"), str) and len(m["content"]) > OLD_RESULT_CHARS:
                    m["content"] = m["content"][:OLD_RESULT_CHARS]
        return data

    @classmethod
    def load(cls, data: dict | None) -> "LoopState":
        data = data or {}
        st = cls()
        for key in ("trigger", "origin_ref", "approval_mode", "conversation_id", "owner_message", "turn_type",
                    "recall_block", "open_step", "steps_total", "segment", "model_outcome"):
            if key in data:
                setattr(st, key, data[key])
        st.messages = [_load_message(m) for m in data.get("messages") or []]
        st.reviewer = {"streak": 0, "total": 0, **(data.get("reviewer") or {})}
        st.preferences_noted = list(data.get("preferences_noted") or [])
        st.usage_ids = list(data.get("usage_ids") or [])
        st.tokens = {"input": 0, "output": 0, "cost": 0.0, **(data.get("tokens") or {})}
        return st


# ---------------------------------------------------------------------------
# Outcome (spec 5.5)
# ---------------------------------------------------------------------------


def classify_outcome(
    *, wrote: bool, w_ok: int, w_bad: int, r_err: int, r_all: int, v_fail: int,
    hint: str | None, has_text: bool, model: str | None = None,
) -> str:
    """The outcome table. `hint` is why the run ended early (stopped, step_cap, spend_cap,
    model_error) or None; `model` is what the model said with `aura_outcome`, if anything. The
    model can only make the result worse; `stopped` always wins."""
    if hint == "stopped":
        return "stopped"
    if not wrote:
        if r_all > 0 and r_err == r_all:
            rule = "failed"
        elif hint in ("model_error", "spend_cap") and not has_text:
            rule = "failed"
        else:
            rule = "done"
    elif w_ok > 0 and w_bad == 0 and v_fail == 0 and hint is None:
        rule = "done"
    elif w_ok > 0:
        rule = "partly_done"
    else:
        rule = "failed"
    if model in WORSE and WORSE[model] > WORSE[rule]:
        return model
    return rule


def step_counts(db: Session, item: WorkItem) -> dict:
    """W_ok, W_bad, R_err, R_all, V_fail over the item's steps."""
    w_ok = w_bad = r_err = r_all = 0
    wrote = False
    for s in workmemory.steps_of(db, item.id):
        if s.kind == "tool":
            if catalogue.is_read_only(s.tool_name):
                r_all += 1
                if s.status in ("error", "denied", "interrupted"):
                    r_err += 1
            else:
                wrote = True
                if s.status == "ok":
                    w_ok += 1
                else:  # error, denied, interrupted, or never settled
                    w_bad += 1
        elif s.kind == "reviewer_block":
            if s.tool_name and not catalogue.is_read_only(s.tool_name):
                wrote = True
                w_bad += 1
        elif s.kind == "approval":
            wrote = True
            if s.status in ("rejected", "expired", "waiting"):
                w_bad += 1
    checks = (item.verification or {}).get("checks") or []
    v_fail = sum(1 for c in checks if not c.get("passed"))
    return {"wrote": wrote, "w_ok": w_ok, "w_bad": w_bad, "r_err": r_err, "r_all": r_all, "v_fail": v_fail}


def _has_section(text: str, label: str) -> bool:
    return any(line.strip().lower().startswith(label.lower()) for line in text.splitlines())


def report_footer(db: Session, item: WorkItem, text: str, noted_ids: list[str]) -> str:
    """Append the report sections the model left out: Checked, Needs you, Noted (spec 5.5 step 1)."""
    extra: list[str] = []
    if not _has_section(text, "Checked:"):
        checks = (item.verification or {}).get("checks") or []
        if checks:
            bits = [f"{c.get('method', '')}: {c.get('evidence', '')}".strip(": ") +
                    ("" if c.get("passed") else " (failed)") for c in checks[:3]]
            extra.append("Checked: " + "; ".join(bits))
    if not _has_section(text, "Needs you:"):
        needs = []
        for s in workmemory.steps_of(db, item.id):
            if s.kind == "approval" and s.status in ("expired", "rejected"):
                label = skill_registry.activity_label(s.tool_name, s.arguments or {})
                needs.append(f"{label} ({'declined' if s.status == 'rejected' else 'not answered in time'})")
        if needs:
            extra.append("Needs you: " + "; ".join(needs[:3]))
    if not _has_section(text, "Noted:") and noted_ids:
        rows = [db.get(Preference, pid) for pid in noted_ids]
        texts = [r.text for r in rows if r is not None]
        if texts:
            extra.append("Noted: " + "; ".join(texts))
    if not extra:
        return text
    return (text.rstrip() + "\n\n" + "\n".join(extra)).strip()


def finalise_item(
    db: Session, user: User, item: WorkItem, state: LoopState, *, hint: str | None, text: str, has_text: bool,
) -> tuple[str, str]:
    """Close the item: decide the status, complete the report, store it. Returns (status, report)."""
    counts = step_counts(db, item)
    model = (state.model_outcome or {}).get("outcome")
    status = classify_outcome(hint=hint, has_text=has_text, model=model, **counts)
    report = report_footer(db, item, text, state.preferences_noted)
    how = (state.model_outcome or {}).get("how") or None
    workmemory.finalise(
        db, item, status=status, report=report, summary=how,
        input_tokens=int(state.tokens.get("input", 0)), output_tokens=int(state.tokens.get("output", 0)),
        cost_usd=float(state.tokens.get("cost", 0.0)), steps_total=state.steps_total,
    )
    return status, item.report


def hint_text(hint: str | None, state: LoopState, db: Session, user: User) -> str:
    if hint == "step_cap":
        return (f"I've reached the step limit for one task ({settings.max_task_steps} steps). "
                "Here is where it stands.")
    if hint == "stopped":
        return STOPPED_TEXT
    return ""


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


class TaskRun:
    """One run of the loop for a work item (or a turn that has not become one yet)."""

    def __init__(self, db: Session, user: User, conversation: Conversation | None, state: LoopState,
                 item: WorkItem | None = None, *, token: str = "", budget_trigger: str = "chat",
                 resumed: bool = False) -> None:
        self.db = db
        self.user = user
        self.conversation = conversation
        self.state = state
        self.item = item
        self.token = token or secrets.token_hex(16)
        self.budget_trigger = budget_trigger
        self.resumed = resumed
        self.result = AgentResult()
        self.recall: workmemory.Recall | None = None
        self.first_events: list[dict] = []
        self.final_text = ""
        self.model_text = False
        self.error = ""
        self._started = time.perf_counter()
        self._events: list[dict] = []

    # -- construction --------------------------------------------------

    @classmethod
    def start(cls, db: Session, user: User, conversation: Conversation, user_message: str,
              trigger: str = "chat", origin_ref: str = "", approval_mode: str | None = None) -> "TaskRun":
        coord = _coord()
        from app import plugins

        systems = plugins.assigned_systems(db, user.id)
        turn_type = tasktypes.classify(user_message)
        state = LoopState(
            trigger=trigger, origin_ref=origin_ref or "", approval_mode=approval_mode,
            conversation_id=conversation.id, owner_message=user_message, turn_type=turn_type, segment=1,
        )
        run = cls(db, user, conversation, state, budget_trigger=trigger)

        # What the owner says about how they want things done, before anything else (so it applies now).
        captured = preference_service.capture(db, user, conversation, user_message, trigger, turn_type,
                                              systems=systems)
        for c in captured:
            state.preferences_noted.append(c.preference.id)
            run.first_events.append({**c.event(), "work_item_id": None})
            run.result.preferences_noted.append({"id": c.preference.id, "text": c.preference.text})

        run.recall = workmemory.build_recall(
            db, user, user_message, turn_type, systems=systems, conversation=conversation,
            noted=[c.preference.text for c in captured],
        )
        state.recall_block = run.recall.block
        run.result.memories_used = list(run.recall.memory_ids)
        counts = run.recall.counts()
        if any(counts.values()):
            run.first_events.insert(0, {
                "type": "recall", **counts, "work_item_id": None,
                "recalled": {
                    "past_work": [{"id": w.id, "title": w.title, "outcome": w.outcome,
                                   "finished_at": _iso(w.finished_at)} for w in run.recall.work],
                    "preferences": [{"id": p.id, "task_type": p.task_type, "text": p.text}
                                    for p in run.recall.preferences],
                },
            })
        state.messages = coord._history(db, conversation) + [{"role": "user", "content": user_message}]
        return run

    @classmethod
    def resume(cls, db: Session, user: User, item: WorkItem, *, token: str, background: bool = False) -> "TaskRun":
        """Continue a claimed item (`claim_for_resume` has already set it `running`)."""
        state = LoopState.load(item.loop_state)
        conversation = db.get(Conversation, item.conversation_id) if item.conversation_id else None
        run = cls(db, user, conversation, state, item, token=token,
                  budget_trigger="resume" if background else state.trigger, resumed=True)
        run.result.work_item_id = item.id
        run._settle_open_step()
        return run

    # -- resume: results for the step that was open --------------------

    def _settle_open_step(self) -> None:
        db, item, st = self.db, self.item, self.state
        step = st.open_step
        interrupted = False
        if step:
            for e in step.get("calls") or []:
                if e.get("content") is not None:
                    continue
                pa_id = e.get("pending_action_id")
                if pa_id:
                    pa = db.get(PendingAction, pa_id)
                    if pa is not None:
                        approvals.expire_if_due(db, pa)
                        db.refresh(pa)
                    e["content"] = _answer_for(pa)
                else:  # a crash between the checkpoint and the result
                    e["content"] = {"status": "interrupted",
                                    "message": "This may or may not have run. Check before trying again."}
                    interrupted = True
                    workmemory.add_step(
                        db, item, kind="tool", status="interrupted", step_no=step.get("step_no", 0),
                        tool_name=e.get("name", ""), tool_call_id=e.get("id", ""), system=_system_of(e.get("name", "")),
                        arguments=e.get("arguments") or {}, summary="Interrupted before it finished",
                    )
                e["state"] = "done"
            self._append_tool_messages()
            st.open_step = None
        workmemory.add_step(db, item, kind="note", step_no=0,
                            summary="Carried on after an interruption" if interrupted else "Carried on")
        self._checkpoint()

    # -- persistence ---------------------------------------------------

    def _checkpoint(self) -> None:
        if self.item is None:
            return
        self.item.loop_state = self.state.dump()
        self.item.steps_total = self.state.steps_total
        self.db.commit()

    def _gate_ctx(self) -> permissions.GateContext:
        st = self.state
        return permissions.build_context(
            self.db, self.user, st.trigger,
            conversation_id=st.conversation_id, work_item_id=self.item.id if self.item else None,
            instruction=st.owner_message, origin_id=st.origin_ref, approval_mode=st.approval_mode,
            reviewer_streak=int(st.reviewer.get("streak", 0)), reviewer_total=int(st.reviewer.get("total", 0)),
            run_key=self.item.id if self.item else self.token,
        )

    def _system_prompt(self) -> dict:
        db, user = self.db, self.user
        assistant = identity_service.get_or_create(db, user)
        system = build_system_prompt(
            user_name=user.name, user_email=user.email, timezone=user.timezone,
            persona=identity_service.persona_prompt(assistant),
            mode_label=permissions.mode_label(db, user),
            recall_block=self.state.recall_block,
            vault_block=vault_service.prompt_block(db, user.id),
            google_connected=google_service.is_connected(db, user.id),
        )
        return {"role": "system", "content": system}

    def _stop_requested(self) -> bool:
        if self.item is None:
            return False
        self.db.refresh(self.item, ["stop_requested_at"])
        return self.item.stop_requested_at is not None

    def _ensure_item(self) -> Generator[dict, None, None]:
        if self.item is not None:
            return
        db, st = self.db, self.state
        item = workmemory.create_item(
            db, self.user, st.owner_message, trigger=st.trigger, origin_ref=st.origin_ref,
            conversation=self.conversation, task_type=st.turn_type,
            lease_owner=self.token, lease_until=_lease_until(),
        )
        if self.recall is not None:
            workmemory.record_recall(db, item, self.recall)
        if st.usage_ids:
            db.execute(update(UsageRecord).where(UsageRecord.id.in_(st.usage_ids)).values(work_item_id=item.id))
            db.commit()
            st.usage_ids = []
        self.item = item
        self.result.work_item_id = item.id
        yield self._task_event()

    def _task_event(self) -> dict:
        item = self.item
        return {"type": "task", "work_item_id": item.id, "title": item.title, "task_type": item.task_type,
                "status": item.status, "resumed": self.resumed}

    def _plan_event(self) -> dict:
        return {"type": "plan", "work_item_id": self.item.id, "steps": list(self.item.plan or [])}

    # -- tool messages -------------------------------------------------

    def _append_tool_messages(self) -> None:
        step = self.state.open_step
        if not step:
            return
        images: list[dict] = []
        for e in step["calls"]:
            self.state.messages.append({
                "role": "tool", "tool_call_id": e["id"], "name": e["name"],
                "content": _dumps(e.get("content"))[:TOOL_MESSAGE_CHARS],
            })
            image = self._images.pop(e["id"], None) if hasattr(self, "_images") else None
            if image and image.get("data"):
                images.append({"role": "user", "content": [
                    {"type": "text", "text": f"[{e['name']}] what it captured:"},
                    {"type": "image", "media_type": image.get("media_type", "image/png"), "data": image["data"]},
                ]})
        self.state.messages.extend(images)

    # -- the segment ---------------------------------------------------

    def segment(self) -> Generator[dict, None, AgentResult]:
        try:
            return (yield from self._segment())
        except (GeneratorExit, Exception):
            # The consumer went away, or something broke: never leave a live lease behind.
            self._abandon()
            raise

    def _abandon(self) -> None:
        """The consumer went away mid-run (the page closed or the fetch was aborted). Park the item
        so it can be continued, or stopped, instead of leaving a live lease behind."""
        item = self.item
        if item is None:
            return
        try:
            self.db.rollback()
            self._checkpoint()
            self.db.execute(
                update(WorkItem)
                .where(WorkItem.id == item.id, WorkItem.status == "running", WorkItem.lease_owner == self.token)
                .values(status="ready", wait_reason="interrupted", ready_at=utcnow(), lease_until=None, lease_owner="")
            )
            self.db.commit()
        except Exception:
            log.exception("could not park an abandoned run")
            self.db.rollback()

    def _segment(self) -> Generator[dict, None, AgentResult]:
        db, user, st = self.db, self.user, self.state
        coord = _coord()
        res = self.result
        self._images: dict[str, dict] = {}
        t0 = time.monotonic()
        seg_steps = 0

        if self.item is not None:
            yield {**self._task_event(), "status": "running", "resumed": True}
        for ev in self.first_events:
            yield ev
        self.first_events = []

        provider = get_provider()
        specs = coord._available_specs(db, user)
        learning = st.trigger == "chat" and workmemory.memory_allowed(db, user.id, st.conversation_id)
        specs = specs + bookkeeping_specs(preferences=learning)
        cheap_model = settings.background_model if settings.background_model and st.trigger != "chat" else None

        while True:
            if self._stop_requested():
                return (yield from self._finish("stopped"))
            if seg_steps >= settings.max_agent_steps:
                return (yield from self._park("segment_budget"))
            if st.steps_total >= settings.max_task_steps:
                return (yield from self._finish("step_cap"))
            if seg_steps > 0 and time.monotonic() - t0 > settings.segment_seconds:
                return (yield from self._park("segment_time"))

            # Checked before the call, never after: refusing to start is recoverable.
            try:
                usage_service.check(db, user.id, trigger=self.budget_trigger)
            except usage_service.SpendCapReached as capped:
                log.warning("Spend cap hit for %s (%s)", user.email, self.budget_trigger)
                res.capped = True
                yield {"type": "capped", "value": True}
                self.final_text = (
                    f"I've stopped here — today's spend cap of ${capped.state.cap_usd:.2f} has been reached "
                    f"(${capped.state.spent_usd:.2f} used across {capped.state.calls} calls). "
                    "Nothing was left half-done. Raise `DAILY_SPEND_CAP_USD` or wait for the daily reset."
                )
                return (yield from self._finish("spend_cap"))

            yield {"type": "status", "value": "thinking", "step": st.steps_total + 1}
            try:
                response = provider.complete([self._system_prompt(), *st.messages], specs, model=cheap_model)
            except Exception as exc:
                log.exception("Model call failed")
                self.error = redaction.scrub(db, user.id, str(exc))[:1000]
                self.final_text = (
                    "I couldn't reach the model just now. Your data is untouched — "
                    f"try again in a moment.\n\n`{self.error[:300]}`"
                )
                return (yield from self._finish("model_error"))

            row = usage_service.record(db, user.id, response.usage, trigger=self.budget_trigger,
                                       work_item_id=self.item.id if self.item else None)
            if row is not None and self.item is None:
                st.usage_ids.append(row.id)
            st.tokens["input"] += response.usage.input_tokens
            st.tokens["output"] += response.usage.output_tokens
            st.tokens["cost"] += usage_service.estimate_cost(response.usage)
            res.input_tokens += response.usage.input_tokens
            res.output_tokens += response.usage.output_tokens
            res.cost_usd += usage_service.estimate_cost(response.usage)
            seg_steps += 1
            st.steps_total += 1
            res.steps = seg_steps

            if not response.tool_calls:
                self.final_text = response.text or "Done."
                self.model_text = True
                return (yield from self._finish(None))

            calls = list(response.tool_calls)
            if self.item is None and any(c.name not in BOOKKEEPING_NAMES or c.name == "aura_plan" for c in calls):
                yield from self._ensure_item()
            st.messages.append({"role": "assistant", "content": response.text, "tool_calls": calls})
            st.open_step = {
                "step_no": st.steps_total,
                "calls": [{"id": c.id, "name": c.name, "arguments": c.arguments, "state": "open",
                           "content": None, "pending_action_id": None} for c in calls],
            }
            self._checkpoint()

            for entry, call in zip(st.open_step["calls"], calls):
                if self._stop_requested():
                    entry["content"] = {"status": "not_run", "message": "Stopped by the user."}
                    entry["state"] = "done"
                    continue
                if call.name in BOOKKEEPING_NAMES:
                    entry["content"] = yield from self._bookkeeping(call)
                else:
                    yield from self._act(entry, call)
                if entry["state"] != "waiting":
                    entry["state"] = "done"
                self._checkpoint()

            waiting = [e for e in st.open_step["calls"] if e["state"] == "waiting"]
            if waiting:
                return (yield from self._park("approval"))
            self._append_tool_messages()
            st.open_step = None
            self._checkpoint()

    # -- acting on one call --------------------------------------------

    def _act(self, entry: dict, call: ToolCall) -> Generator[dict, None, None]:
        db, user, st, item, res = self.db, self.user, self.state, self.item, self.result
        coord = _coord()
        skill = skill_registry.skill_for_tool(call.name)
        system = _system_of(call.name)
        step_no = st.open_step["step_no"]

        ctx = self._gate_ctx()
        decision = permissions.decide(db, user, call.name, call.arguments, ctx)
        if decision.source == "reviewer":
            if decision.kind == "allow":
                st.reviewer["streak"] = 0
            else:
                st.reviewer["streak"] = int(st.reviewer.get("streak", 0)) + 1
                st.reviewer["total"] = int(st.reviewer.get("total", 0)) + 1

        payload: Any
        if decision.kind == "deny":
            payload = {
                "status": "blocked",
                "error": decision.reason,  # what clients of the old refusal shape read
                "reason": decision.reason,
                "reason_code": decision.reason_code,
                "message": "Not run. Try another acceptable way, or explain to the user.",
            }
            if decision.source == "reviewer":
                payload["reviewer"] = True
                workmemory.add_step(db, item, kind="reviewer_block", step_no=step_no, tool_name=call.name,
                                    tool_call_id=call.id, system=system, arguments=call.arguments,
                                    summary=decision.reason,
                                    detail={"decision_source": decision.source, "reason": decision.reason})
                yield {"type": "reviewer_block", "work_item_id": item.id, "tool": call.name,
                       "reason": decision.reason, "streak": st.reviewer["streak"], "total": st.reviewer["total"]}
            else:
                s = workmemory.add_step(db, item, kind="tool", status="denied", step_no=step_no,
                                        tool_name=call.name, tool_call_id=call.id, system=system,
                                        arguments=call.arguments, summary=decision.reason,
                                        detail={"decision_source": decision.source, "reason": decision.reason})
                yield {"type": "step_finished", "work_item_id": item.id, "step_no": step_no, "seq": s.seq,
                       "tool": call.name, "ok": False, "summary": decision.reason[:200],
                       "code": skill.code if skill else ""}
            entry["content"] = payload

        elif decision.kind == "ask":
            action = approvals.queue_action(db, user, ctx, call, decision, call.id)
            if action.work_item_id != item.id:
                # The same unattended request is already waiting for the user from another run; this
                # run does not wait on a card it does not own.
                payload = {"status": "already_pending",
                           "message": "The same action is already waiting for the user's approval from an "
                                      "earlier run. Do not retry it; mention it under 'Needs you'."}
                entry["content"] = payload
            else:
                s = workmemory.add_step(
                    db, item, kind="approval", status="waiting", step_no=step_no, tool_name=call.name,
                    tool_call_id=call.id, system=system, arguments=call.arguments,
                    summary=skill_registry.activity_label(call.name, call.arguments),
                    detail={"pending_action_id": action.id, "decision_source": decision.source,
                            "reason": decision.reason},
                )
                entry["pending_action_id"] = action.id
                entry["state"] = "waiting"
                res.pending_actions.append(action)
                payload = {"status": "awaiting_approval", "approval_id": action.id, "reason": decision.reason,
                           "message": f"{call.name} is queued and waiting for the user to approve it.",
                           "preview": action.preview, "pending_action_id": action.id}
                yield {"type": "approval_needed", "work_item_id": item.id, "pending_action_id": action.id,
                       "tool_name": action.tool_name, "skill_code": action.skill_code, "preview": action.preview,
                       "expires_at": _iso(action.expires_at)}

        else:  # allow
            label = skill_registry.activity_label(call.name, call.arguments)
            s = workmemory.add_step(db, item, kind="tool", status="started", step_no=step_no, tool_name=call.name,
                                    tool_call_id=call.id, system=system, arguments=call.arguments, summary=label,
                                    detail={"decision_source": decision.source})
            yield {"type": "step_started", "work_item_id": item.id, "step_no": step_no, "seq": s.seq,
                   "tool": call.name, "system": system, "label": label}
            started = time.perf_counter()
            try:
                payload = coord.execute_tool(db, user, call.name, call.arguments, trigger=st.trigger,
                                             work_item_id=item.id, decision=decision)
            except PermissionError as exc:  # the seal or the arguments no longer match: nothing ran
                payload = {"error": str(exc)}
            duration = int((time.perf_counter() - started) * 1000)

            # An image a tool produced (a screenshot) travels separately from its result: both
            # providers take an image on a user turn, while only one takes it inside a tool result.
            if isinstance(payload, dict) and isinstance(payload.get("image"), dict):
                self._images[call.id] = payload["image"]
                payload = {k: v for k, v in payload.items() if k != "image"}

            ok = not _error_flag(payload)
            workmemory.note_tool(db, item, call.name, system)
            summary = skill_registry.summarise(call.name, payload)
            workmemory.finish_step(db, s, "ok" if ok else "error", summary, {"duration_ms": duration})

            if ok and not catalogue.is_read_only(call.name):
                v = verifiers.check(db, user, call.name, call.arguments, payload)
                if v:
                    if isinstance(payload, dict):
                        payload = {**payload, "verification": v}
                    workmemory.add_check(db, item, v["passed"], v["method"], v["evidence"], step_no)
                    yield {"type": "verification", "work_item_id": item.id, "passed": v["passed"],
                           "method": v["method"], "evidence": v["evidence"]}
            yield {"type": "step_finished", "work_item_id": item.id, "step_no": step_no, "seq": s.seq,
                   "tool": call.name, "ok": ok, "summary": summary, "code": skill.code if skill else ""}
            if skill:
                run = {"code": skill.code, "name": skill.name, "summary": summary, "ok": ok, "tool": call.name}
                res.skill_runs.append(run)
            entry["content"] = payload

        res.tool_calls.append({
            "name": call.name, "skill_code": skill.code if skill else "",
            "arguments": call.arguments, "result": payload,
        })

    # -- bookkeeping tools ---------------------------------------------

    def _bookkeeping(self, call: ToolCall) -> Generator[dict, None, dict]:
        db, st = self.db, self.state
        args = call.arguments if isinstance(call.arguments, dict) else {}
        item = self.item
        name = call.name

        if name == "aura_note_preference":
            out = preference_service.note_from_tool(
                db, self.user, self.conversation, st.owner_message,
                text=str(args.get("text") or ""), task_type=str(args.get("task_type") or "general"),
                quote=str(args.get("quote") or ""), trigger=st.trigger,
            )
            if out.get("saved"):
                pref = db.get(Preference, out["id"])
                if pref is not None:
                    if pref.id not in st.preferences_noted:
                        st.preferences_noted.append(pref.id)
                        self.result.preferences_noted.append({"id": pref.id, "text": pref.text})
                    yield {"type": "preference_noted", "id": pref.id, "text": pref.text,
                           "task_type": pref.task_type, "superseded_id": out.get("superseded_id"),
                           "work_item_id": item.id if item else None}
            return out

        if item is None:
            return {"ok": False, "error": "Start the work first; there is no assignment to record this against yet."}

        if name == "aura_plan":
            steps = args.get("steps")
            if not isinstance(steps, list) or not [s for s in steps if str(s).strip()]:
                return {"ok": False, "error": "steps must be a list of one to twelve short steps"}
            plan = workmemory.set_plan(db, item, str(args.get("title") or ""), str(args.get("task_type") or "") or None,
                                       [str(s) for s in steps])
            workmemory.add_step(db, item, kind="plan", step_no=st.open_step["step_no"] if st.open_step else 0,
                                summary=item.title, detail={"steps": [p["text"] for p in plan]})
            yield self._task_event()
            yield self._plan_event()
            return {"ok": True}

        if name == "aura_progress":
            row = workmemory.update_plan_step(db, item, args.get("index"), str(args.get("status") or ""),
                                              str(args.get("note") or ""))
            if row is None:
                return {"ok": False, "error": "unknown plan step or status"}
            yield self._plan_event()
            return {"ok": True}

        if name == "aura_check":
            passed = args.get("passed")
            if not isinstance(passed, bool):
                return {"ok": False, "error": "passed must be true or false"}
            method = str(args.get("method") or "")[:200]
            evidence = str(args.get("evidence") or "")[:300]
            workmemory.add_check(db, item, passed, method, evidence, st.open_step["step_no"] if st.open_step else 0)
            yield {"type": "verification", "work_item_id": item.id, "passed": passed, "method": method,
                   "evidence": evidence}
            return {"ok": True}

        if name == "aura_outcome":
            outcome = args.get("outcome")
            if outcome not in WORSE:
                return {"ok": False, "error": "outcome must be done, partly_done or failed"}
            st.model_outcome = {"outcome": outcome, "how": str(args.get("how") or "")[:800]}
            return {"ok": True}

        return {"ok": False, "error": f"unknown bookkeeping tool {name}"}

    # -- leaving the segment -------------------------------------------

    def _park(self, reason: str) -> Generator[dict, None, AgentResult]:
        db, st, res = self.db, self.state, self.result
        if self.item is None:
            yield from self._ensure_item()
        item = self.item
        waiting = [e for e in (st.open_step or {}).get("calls", []) if e["state"] == "waiting"]
        if reason == "approval":
            lines = [f"- {skill_registry.activity_label(e['name'], e['arguments'])}" for e in waiting]
            self.final_text = ("I need your go-ahead before I continue:\n" + "\n".join(lines) +
                               "\n\nEverything else in this step is done.")
            status = "waiting"
        else:
            self.final_text = (f"Still working — I've done {st.steps_total} steps so far and will carry on.")
            status = "ready"
        self._checkpoint()
        db.execute(
            update(WorkItem).where(WorkItem.id == item.id).values(
                status=status, wait_reason=reason, ready_at=utcnow() if status == "ready" else None,
                lease_until=None, lease_owner="", updated_at=utcnow(),
            )
        )
        db.commit()
        db.refresh(item)
        if reason == "approval":
            ids = [e["pending_action_id"] for e in waiting if e.get("pending_action_id")]
            expiries = [
                _aware(db.get(PendingAction, i).expires_at) for i in ids
                if db.get(PendingAction, i) is not None and db.get(PendingAction, i).expires_at is not None
            ]
            yield {"type": "waiting", "work_item_id": item.id, "pending_action_ids": ids,
                   "expires_at": _iso(min(expiries)) if expiries else None}
        else:
            yield {"type": "continue", "work_item_id": item.id, "reason": reason}
        return self._result(status)

    def _finish(self, hint: str | None) -> Generator[dict, None, AgentResult]:
        db, user, st, res = self.db, self.user, self.state, self.result
        if self.item is None:
            # A turn answered without tools: no item, the text is the model's answer.
            res.text = self.final_text or hint_text(hint, st, db, user) or "Done."
            return self._result("")
        item = self.item
        if hint == "stopped":
            _reject_pending(db, item, actor_note="stop")
        text = self.final_text or hint_text(hint, st, db, user) or "Done."
        if hint == "step_cap" and self.final_text:
            text = f"{self.final_text}\n\n{hint_text(hint, st, db, user)}"
        if self.error:
            item.error = self.error
        status, report = finalise_item(db, user, item, st, hint=hint, text=text, has_text=self.model_text)
        self.final_text = report
        yield {"type": "report", "work_item_id": item.id, "status": status, "title": item.title,
               "outcome": item.outcome, "verification": (item.verification or {}).get("status", "none")}
        return self._result(status)

    def _result(self, status: str) -> AgentResult:
        res = self.result
        res.text = self.final_text
        res.work_status = status
        res.latency_ms = int((time.perf_counter() - self._started) * 1000)
        res.work_item_id = self.item.id if self.item else None
        return res


# ---------------------------------------------------------------------------
# Answers for a resumed run
# ---------------------------------------------------------------------------


def _answer_for(pa: PendingAction | None) -> dict:
    """What the model is told about a parked call (spec 6.3)."""
    if pa is None:
        return {"status": "expired", "message": "The approval request is gone, so it was not run."}
    if pa.status == "approved":
        return {"status": "approved_and_run", "result": pa.result}
    if pa.status == "rejected":
        return {"status": "declined_by_user",
                "message": "The user said no. Don't retry it; offer another way or stop."}
    if pa.status == "refused":
        return {"status": "refused", "message": (pa.reason or "It was approved but is no longer allowed.")[:300]}
    return {"status": "expired", "message": "Nobody answered in time, so it was not run."}


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def start_run(db: Session, user: User, conversation: Conversation, user_message: str, trigger: str = "chat",
              origin_ref: str = "", approval_mode: str | None = None) -> Generator[dict, None, AgentResult]:
    run = TaskRun.start(db, user, conversation, user_message, trigger, origin_ref, approval_mode)
    return (yield from run.segment())


def claim_for_resume(db: Session, user: User, work_item_id: str, *, background: bool = False) -> str:
    """Take the lease on a `ready` item (spec 6.3). Returns the lease token. Raises NotFound for an
    item that is not this user's and Busy for one that is not ready or is held."""
    item = db.get(WorkItem, work_item_id)
    if item is None or item.user_id != user.id:
        raise NotFound(work_item_id)
    return _claim(db, item, background=background)


def _claim(db: Session, item: WorkItem, *, background: bool = False) -> str:
    approvals.expire_due(db, item.user_id)
    token = secrets.token_hex(16)
    now = utcnow()
    n = db.execute(
        update(WorkItem)
        .where(WorkItem.id == item.id, WorkItem.status == "ready",
               (WorkItem.lease_until.is_(None)) | (WorkItem.lease_until < now))
        .values(status="running", lease_owner=token, lease_until=_lease_until(), wait_reason="",
                segments=WorkItem.segments + 1, updated_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    db.refresh(item)
    if n != 1:
        raise Busy(item.status, item.id)
    return token


def iter_resume(db: Session, user: User, work_item_id: str, *, token: str | None = None,
                background: bool = False) -> Generator[dict, None, AgentResult]:
    """Continue the same run after an approval, a denial, an expiry or a budget pause."""
    if token is None:
        token = claim_for_resume(db, user, work_item_id, background=background)
    item = db.get(WorkItem, work_item_id)
    run = TaskRun.resume(db, user, item, token=token, background=background)
    return (yield from run.segment())


# ---------------------------------------------------------------------------
# The approvals hook (spec 6.2)
# ---------------------------------------------------------------------------


def on_pending_resolved(db: Session, action: PendingAction, result: Any) -> dict | None:
    """Called by permissions/approvals.py once a parked action has its final status.

    Stores the run result on the PendingAction, settles its approval step (and records the tool step
    for an approved run), and, when nothing of this item is still waiting, marks the item `ready`.
    Idempotent: the status change is one conditional UPDATE. Returns `{"id", "status"}`.
    """
    if not action.work_item_id:
        return None
    item = db.get(WorkItem, action.work_item_id)
    if item is None:
        return None
    uid = item.user_id
    if result is not None and action.result is None:
        action.result = redaction.scrub_json(db, uid, result if isinstance(result, dict) else {"result": result},
                                             max_str=2000)
    step = db.scalars(
        select(WorkStep).where(WorkStep.work_item_id == item.id, WorkStep.kind == "approval")
        .order_by(WorkStep.seq.desc())
    ).all()
    approval = next((s for s in step if (s.detail or {}).get("pending_action_id") == action.id), None)
    label = skill_registry.activity_label(action.tool_name, action.arguments or {})
    if approval is not None and approval.status == "waiting":
        if action.status == "approved":
            workmemory.finish_step(db, approval, "approved", label)
        elif action.status == "expired":
            workmemory.finish_step(db, approval, "expired", label + " (not answered in time)")
        else:  # rejected, refused
            workmemory.finish_step(db, approval, "rejected", label + " (declined)" if action.status == "rejected"
                                   else label + " (no longer allowed)")
        if action.status == "approved" and isinstance(result, dict):
            ok = not _error_flag(result)
            ts = workmemory.add_step(
                db, item, kind="tool", status="ok" if ok else "error", step_no=approval.step_no,
                tool_name=action.tool_name, tool_call_id=action.tool_call_id or "", system=approval.system,
                arguments=action.arguments or {}, summary=skill_registry.summarise(action.tool_name, result),
                detail={"pending_action_id": action.id, "decision_source": "approval"},
            )
            workmemory.note_tool(db, item, action.tool_name, approval.system)
            if ok and not catalogue.is_read_only(action.tool_name):
                user = db.get(User, uid)
                v = verifiers.check(db, user, action.tool_name, action.arguments or {}, result) if user else None
                if v:
                    workmemory.add_check(db, item, v["passed"], v["method"], v["evidence"], approval.step_no)
    db.commit()

    unresolved = db.scalar(
        select(PendingAction.id).where(
            PendingAction.work_item_id == item.id, PendingAction.status.in_(("pending", "resolving"))
        ).limit(1)
    )
    if unresolved is None:
        now = utcnow()
        db.execute(
            update(WorkItem)
            .where(WorkItem.id == item.id, WorkItem.status == "waiting")
            .values(status="ready", wait_reason="approval", ready_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        db.commit()
    db.refresh(item)
    return {"id": item.id, "status": item.status}


# ---------------------------------------------------------------------------
# Stop, stale leases (spec 6.5)
# ---------------------------------------------------------------------------


def _reject_pending(db: Session, item: WorkItem, actor_note: str = "stop") -> int:
    """Reject every pending approval of the item and settle its approval steps."""
    now = utcnow()
    rows = db.scalars(
        select(PendingAction).where(PendingAction.work_item_id == item.id,
                                    PendingAction.status.in_(("pending", "resolving")))
    ).all()
    n = 0
    for pa in rows:
        won = db.execute(
            update(PendingAction)
            .where(PendingAction.id == pa.id, PendingAction.status.in_(("pending", "resolving")))
            .values(status="rejected", decision="reject", resolved_at=now, resolved_by="user", updated_at=now,
                    result={"status": "rejected"})
        ).rowcount
        if won:
            n += 1
            db.add(ActivityLog(user_id=item.user_id, actor="user", action=f"{actor_note}:{pa.tool_name}",
                               target=(pa.preview or "")[:300], status="rejected", work_item_id=item.id))
    for s in workmemory.steps_of(db, item.id):
        if s.kind == "approval" and s.status == "waiting":
            s.status = "rejected"
    db.commit()
    return n


def stop(db: Session, user: User, item: WorkItem) -> dict:
    """The Stop button. A running segment is asked to stop at its next check; a waiting or ready
    item is closed at once. A finished item is left alone."""
    if item.user_id != user.id:
        raise NotFound(item.id)
    db.refresh(item)
    now = utcnow()
    live = item.status == "running" and (_aware(item.lease_until) or now) > now
    if live:
        item.stop_requested_at = now
        db.commit()
        return {"id": item.id, "status": item.status}
    if item.status not in ("running", "waiting", "ready"):
        return {"id": item.id, "status": item.status}

    state = LoopState.load(item.loop_state)
    item.stop_requested_at = now
    _reject_pending(db, item)
    status, _ = finalise_item(db, user, item, state, hint="stopped", text=STOPPED_TEXT, has_text=False)
    if item.conversation_id:
        convo = db.get(Conversation, item.conversation_id)
        if convo is not None:
            db.add(Message(conversation_id=convo.id, role="assistant", content=STOPPED_TEXT,
                           meta={"work_item_id": item.id, "work_status": "stopped", "segment": item.segments}))
            convo.updated_at = utcnow()
            db.commit()
    return {"id": item.id, "status": item.status}


def recover_stale(db: Session) -> int:
    """A `running` item whose lease ran out belonged to a process that died: make it `ready` again
    so it can be continued (the open call is then marked interrupted). Returns how many."""
    now = utcnow()
    n = db.execute(
        update(WorkItem)
        .where(WorkItem.status == "running", WorkItem.lease_until.is_not(None), WorkItem.lease_until < now)
        .values(status="ready", wait_reason="interrupted", ready_at=now, lease_until=None, lease_owner="",
                updated_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    return n


def running_item(db: Session, conversation_id: str) -> WorkItem | None:
    """The item of this conversation that a live segment is working on, if any (spec 6.6)."""
    now = utcnow()
    for item in db.scalars(
        select(WorkItem).where(WorkItem.conversation_id == conversation_id, WorkItem.status == "running")
    ).all():
        if item.lease_until is not None and _aware(item.lease_until) > now:
            return item
    return None
