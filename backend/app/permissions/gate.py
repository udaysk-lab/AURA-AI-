"""The permission gate: one function decides every tool call (spec 10 section 4).

`decide(db, user, tool, args, ctx)` returns a sealed `Decision`: allow, ask or deny.
`coordinator.execute_tool` refuses to run anything without a sealed allow whose tool
and argument digest match the call, so no code path can execute a tool without
passing through here. Only this module holds the seal.

The order below is the spec's and does not change (first terminal result wins):

   0 known tool (and loop bookkeeping)            7 an ask rule matches        -> ask
   1 the tool's system is assigned                8 an allow rule covers it    -> allow
   2 install ceilings (device control, screen)   9 reads and workspace writes -> allow
   3 hard floors                                 10 the mode: skip allows, ask asks,
   4 limits of the trigger (schedule, ...)           auto asks the reviewer
   5 a block rule matches                        11 heartbeat never asks; log it
   6 always needs the user

Nothing the model writes reaches the rules, the mode or the settings: the gate reads
them from the database, and tools have no way to change them.
"""

from __future__ import annotations

import hashlib
import logging
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AutomationRule, PendingAction, Schedule, User, UserSettings, utcnow
from app.permissions import catalogue, events, floors, interim, match, reviewer, rules as rules_store, shellparse, triggers
from app.permissions.catalogue import ALLOW_KINDS, READ_KINDS, ToolPolicy
from app.permissions.match import BadArgs, Target

log = logging.getLogger("aura.permissions")

# The seal's key: made at import, never leaves this module. A Decision carries a MAC of its own
# content under it, so a decision cannot be forged, nor edited (dataclasses.replace) into an allow.
_SEAL = secrets.token_bytes(32)

ALL_OPTIONS = ("once", "window", "always", "reject")
ONCE_ONLY = ("once", "reject")
TRIGGERS = ("chat", "schedule", "automation", "heartbeat", "inbound", "approval", "console", "user_ui")


# ---------------------------------------------------------------------------
# Context and decision
# ---------------------------------------------------------------------------


@dataclass
class GateContext:
    trigger: str  # chat|schedule|automation|heartbeat|inbound|approval|console|user_ui
    conversation_id: str | None = None
    work_item_id: str | None = None
    approval_mode: str | None = None  # schedule / automation only
    instruction: str = ""  # owner-authored text of this item (reviewer input)
    reviewer_streak: int = 0
    reviewer_total: int = 0
    origin_id: str = ""  # schedule / automation id, channel kind
    owner_verified: bool = True
    run_key: str = ""
    present: bool = False  # UI presence at decide time (filled by work package 8)
    # Interim only: the 1.0 autonomy tier of the run, until the legacy data migration (WP11).
    legacy_tier: str | None = None


def build_context(db: Session | None, user: User | None, trigger: str, **kw: Any) -> GateContext:
    if trigger not in TRIGGERS:
        raise ValueError(f"unknown trigger {trigger!r}")
    ctx = GateContext(trigger=trigger, **kw)
    if not ctx.run_key:
        ctx.run_key = ctx.work_item_id or str(uuid.uuid4())
    return ctx


@dataclass(frozen=True)
class RuleDraft:
    """What an approval card's "window" / "always" would store, shown to the user first."""

    system: str
    tool: str
    applies_to: str = ""
    # (target_kind, pattern) pairs; one rule is stored per pair. Empty = the tool as a whole.
    items: tuple[tuple[str, str], ...] = ()
    effect: str = "allow"


@dataclass(frozen=True)
class Decision:
    kind: Literal["allow", "ask", "deny"]
    source: str  # rule | floor | system | mode | reviewer | always_user
    reason_code: str
    reason: str
    tool: str
    args_digest: str
    targets: tuple[Target, ...] = ()
    tool_kind: str = ""
    rule_id: str | None = None
    reviewer: reviewer.ReviewerVerdict | None = None
    always_user: bool = False
    card: str | None = None
    card_payload: dict = field(default_factory=dict)
    options: tuple[str, ...] = ()
    proposed_rule: RuleDraft | None = None
    user_id: str = ""
    _seal: str | None = field(default=None, repr=False, compare=False)


def _mac(d: Decision) -> str:
    body = "\x1f".join(
        str(x) for x in (d.kind, d.source, d.reason_code, d.tool, d.args_digest, d.user_id, d.rule_id, d.always_user)
    )
    return hmac.new(_SEAL, body.encode("utf-8"), "sha256").hexdigest()


def _make(**kw: Any) -> Decision:
    """The only place a Decision is built."""
    d = Decision(**kw)
    object.__setattr__(d, "_seal", _mac(d))
    return d


def is_sealed(decision: object) -> bool:
    """True only for a Decision this module built and nobody has changed since."""
    seal = getattr(decision, "_seal", None)
    return isinstance(decision, Decision) and isinstance(seal, str) and hmac.compare_digest(seal, _mac(decision))


def args_digest(args: Any) -> str:
    return hashlib.sha256(events.canonical(args if args is not None else {}).encode("utf-8")).hexdigest()


def is_read_only(tool_name: str) -> bool:
    return catalogue.is_read_only(tool_name)


def expiry_for(ctx: GateContext, card: str = "tool") -> datetime:
    """When a parked approval lapses (a DECISION of the spec): chat/inbound/automation 24 h,
    schedule 12 h, a folder / app / delete-permission card 30 minutes."""
    if card != "tool":
        return utcnow() + timedelta(minutes=30)
    hours = {"schedule": 12}.get(ctx.trigger, 24)
    return utcnow() + timedelta(hours=hours)


def _settings_row(db: Session, user: User) -> UserSettings | None:
    return user.settings or db.scalars(select(UserSettings).where(UserSettings.user_id == user.id)).first()


def mode_label(db: Session, user: User) -> str:
    mode = interim.stored_mode(db, user)
    if mode == "auto":
        return "Auto" if reviewer.available() else "Auto (acting as Ask first)"
    return {"ask": "Ask first", "skip": "Skip approvals"}[mode]


def effective_mode(db: Session, user: User, ctx: GateContext) -> str:
    """The mode used at step 10 (section 4.4)."""
    if ctx.trigger == "heartbeat":
        return "ask"
    if ctx.trigger in ("schedule", "automation"):
        mode = ctx.approval_mode if ctx.approval_mode in ("ask", "auto") else "ask"
    else:
        mode = interim.stored_mode(db, user, ctx)
    if mode == "auto" and not reviewer.available():
        return "ask"
    return mode


# ---------------------------------------------------------------------------
# Building decisions
# ---------------------------------------------------------------------------


def _deny(source: str, code: str, reason: str, *, tool: str, digest: str, user: User, policy: ToolPolicy | None = None,
          targets: tuple[Target, ...] = (), rule_id: str | None = None, verdict=None) -> Decision:
    return _make(kind="deny", source=source, reason_code=code, reason=reason, tool=tool, args_digest=digest,
                 targets=targets, tool_kind=policy.kind if policy else "", rule_id=rule_id, reviewer=verdict,
                 user_id=user.id)


@dataclass
class _Pre:
    policy: ToolPolicy
    targets: tuple[Target, ...]
    dialect: str
    digest: str
    floor: floors.FloorResult
    rules: list


def _dialect(policy: ToolPolicy) -> str:
    if not any(t.kind == "command" for t in policy.targets):
        return shellparse.POSIX
    try:
        from app.device.runtime import capabilities

        return shellparse.dialect_for_shell_kind(capabilities()["shell"]["kind"])
    except Exception:  # pragma: no cover - capability probe failure
        return shellparse.POSIX


def _head(db: Session, user: User, tool: str, args: Any, ctx: GateContext) -> Decision | _Pre:
    """Steps 0 to 5. A terminal Decision, or the facts later steps need."""
    from app.agent.tools import REGISTRY

    digest = args_digest(args)
    policy = catalogue.CATALOGUE.get(tool)
    if not policy or (tool not in REGISTRY and not policy.loop_internal):
        return _deny("system", "UNKNOWN_TOOL", "That tool does not exist here, so it was not run.", tool=tool,
                     digest=digest, user=user)
    if policy.loop_internal:
        return _make(kind="allow", source="mode", reason_code="DEFAULT_WORKSPACE",
                     reason="AURA's own bookkeeping.", tool=tool, args_digest=digest, tool_kind=policy.kind,
                     user_id=user.id)

    dialect = _dialect(policy)
    try:
        targets = match.extract(policy, args, dialect=dialect)
    except BadArgs as exc:
        return _deny("system", "BAD_ARGS", f"The request could not be read safely: {exc}", tool=tool, digest=digest,
                     user=user, policy=policy)

    def deny(source: str, code: str, reason: str, rule_id: str | None = None) -> Decision:
        return _deny(source, code, reason, tool=tool, digest=digest, user=user, policy=policy, targets=targets,
                     rule_id=rule_id)

    # 1. systems
    from app import plugins
    from app.agent import skills as skill_registry

    if policy.system not in plugins.assigned_systems(db, user.id) or tool not in skill_registry.enabled_tools(db, user.id):
        return deny("system", "SYSTEM_NOT_ASSIGNED",
                    "That tool belongs to a system that is not turned on for you. You can add it in Settings, Systems.")

    # 2. install ceilings
    if policy.system == "device-control" and not settings.device_control_enabled:
        return deny("floor", "INSTALL_OFF", "Device control is off on this install.")
    if policy.kind in ("screen_read", "screen_input") and not settings.device_screen_control:
        return deny("floor", "INSTALL_OFF", "Screen control is turned off on this install.")

    # 3. floors
    fl = floors.check(db, user, policy, targets, ctx)
    if fl.deny:
        return deny("floor", fl.code, fl.reason)

    # 4. trigger limits
    t = triggers.check(policy, ctx)
    if t:
        return deny("floor", t[0], t[1])

    # 5. block rules
    active = rules_store.active(db, user, policy, ctx, dialect)
    applicable = [r for r in active if match.rule_applies_to(r, targets)]
    block = match.first_match("block", applicable, targets, dialect)
    if block:
        return deny("rule", "RULE_BLOCK", "A rule you set blocks this.", block.id)
    return _Pre(policy, targets, dialect, digest, fl, applicable)


# ---------------------------------------------------------------------------
# Proposed rules (what "for 10 minutes" / "always" would store)
# ---------------------------------------------------------------------------


def _propose(policy: ToolPolicy, targets: tuple[Target, ...], ctx: GateContext) -> RuleDraft | None:
    """None means the card may offer only once / reject."""
    if policy.name == "send_process_input":
        return None
    applies = rules_store.scope_of(ctx)
    items: list[tuple[str, str]] = []
    for t in targets:
        if t.kind == "recipient":
            items.append(("recipient", t.value))
        elif t.kind == "command" and t.parsed is not None:
            pats = match.command_card_patterns(t.parsed)
            if not pats:
                return None
            items.extend(("command", p) for p in pats)
        elif t.kind == "url":
            items.append(("domain", t.value))
        elif t.kind == "app" and t.matchable:
            items.append(("app", t.value))
    paths = [t for t in targets if t.kind == "path" and t.matchable]
    if paths:
        chosen = next((t for t in paths if t.role == "write"), paths[0])
        parent = os.path.dirname(chosen.value) or chosen.value
        items.append(("path", os.path.join(parent, "**")))
    return RuleDraft(system=policy.system, tool=policy.name, applies_to=applies, items=tuple(dict.fromkeys(items)))


def _ask(pre: _Pre, ctx: GateContext, source: str, code: str, reason: str, *, rule_id: str | None = None,
         always_user: bool = False, user: User, tool: str) -> Decision:
    draft = None if always_user else _propose(pre.policy, pre.targets, ctx)
    options = ONCE_ONLY if (always_user or draft is None) else ALL_OPTIONS
    return _make(kind="ask", source=source, reason_code=code, reason=reason, tool=tool, args_digest=pre.digest,
                 targets=pre.targets, tool_kind=pre.policy.kind, rule_id=rule_id, always_user=always_user,
                 card="tool", options=options, proposed_rule=draft, user_id=user.id)


def _allow(pre: _Pre, source: str, code: str, reason: str, *, rule_id: str | None = None, user: User, tool: str,
           verdict=None) -> Decision:
    return _make(kind="allow", source=source, reason_code=code, reason=reason, tool=tool, args_digest=pre.digest,
                 targets=pre.targets, tool_kind=pre.policy.kind, rule_id=rule_id, reviewer=verdict, user_id=user.id)


# ---------------------------------------------------------------------------
# decide
# ---------------------------------------------------------------------------


def decide(db: Session, user: User, tool: str, args: Any, ctx: GateContext) -> Decision:
    d = _decide(db, user, tool, args, ctx)
    return _finish(db, user, d, ctx)


def _decide(db: Session, user: User, tool: str, args: Any, ctx: GateContext) -> Decision:
    pre = _head(db, user, tool, args, ctx)
    if isinstance(pre, Decision):
        return pre
    policy = pre.policy

    # 6. always needs the user
    if policy.always_user or pre.floor.always_user:
        why = ("Permanently deleting files always needs your approval."
               if policy.always_user else
               "This command deletes files or touches a protected location, so it needs your approval every time.")
        return _ask(pre, ctx, "always_user", "ALWAYS_USER", why, always_user=True, user=user, tool=tool)

    # 7. ask rules
    ask = match.first_match("ask", pre.rules, pre.targets, pre.dialect)
    if ask:
        return _ask(pre, ctx, "rule", "RULE_ASK", "A rule you set asks before this.", rule_id=ask.id, user=user, tool=tool)

    # 8. allow rules
    cover = match.allow_cover(pre.rules, pre.targets, pre.dialect)
    if cover:
        return _allow(pre, "rule", "RULE_ALLOW", "Allowed by a rule you set.", rule_id=cover[0].id, user=user, tool=tool)

    # 9. defaults for reads and AURA's own workspace
    if policy.kind in ALLOW_KINDS:
        code = "DEFAULT_READ" if policy.kind in READ_KINDS else "DEFAULT_WORKSPACE"
        return _allow(pre, "mode", code, "This only reads, or changes only AURA's own workspace.", user=user, tool=tool)

    # 10. the mode
    mode = effective_mode(db, user, ctx)
    if mode == "skip":
        return _allow(pre, "mode", "MODE_SKIP", "Approvals are skipped in your current mode.", user=user, tool=tool)
    if mode == "ask":
        return _ask(pre, ctx, "mode", "MODE_ASK", "This changes something outside AURA's own workspace.", user=user, tool=tool)
    if ctx.reviewer_streak >= 3 or ctx.reviewer_total >= 20:
        return _ask(pre, ctx, "mode", "REVIEWER_FALLBACK",
                    "The safety reviewer has stopped several actions in this run, so I'm asking you.", user=user, tool=tool)
    try:
        verdict = reviewer.review(db, user, policy, args, pre.targets, ctx)
    except Exception:
        log.exception("reviewer failed")
        verdict = reviewer.ReviewerVerdict(False, "The safety reviewer could not check this.", "REVIEWER_ERROR")
    if not isinstance(verdict, reviewer.ReviewerVerdict):
        verdict = reviewer.ReviewerVerdict(False, "The safety reviewer gave no usable answer.", "REVIEWER_ERROR")
    _log_review(db, user, tool, pre, verdict, ctx)
    if verdict.allow:
        return _allow(pre, "reviewer", "REVIEWER_ALLOW", verdict.reason or "The safety reviewer allowed this.",
                      user=user, tool=tool, verdict=verdict)
    code = "REVIEWER_ERROR" if verdict.code == "REVIEWER_ERROR" else "REVIEWER_BLOCK"
    return _deny("reviewer", code, verdict.reason or "The safety reviewer blocked this.", tool=tool, digest=pre.digest,
                 user=user, policy=policy, targets=pre.targets, verdict=verdict)


def _finish(db: Session, user: User, d: Decision, ctx: GateContext) -> Decision:
    """Step 11: the heartbeat never asks; then write the event."""
    if d.kind == "ask" and ctx.trigger == "heartbeat":
        d = _make(kind="deny", source="floor", reason_code="HEARTBEAT_NO_ASK",
                  reason="AURA's background checks never ask for approval, so this was not done.", tool=d.tool,
                  args_digest=d.args_digest, targets=d.targets, tool_kind=d.tool_kind, rule_id=d.rule_id,
                  user_id=d.user_id)
    policy = catalogue.CATALOGUE.get(d.tool)
    if policy is not None and policy.loop_internal:
        return d
    loggable = d.kind != "allow" or d.tool_kind not in READ_KINDS
    if not loggable:
        return d
    try:
        events.append(
            db, user.id, "decision", actor="aura", tool=d.tool, target=_target_text(d.targets),
            outcome=d.kind, reason_code=d.reason_code, run_key=ctx.run_key,
            detail={"source": d.source, "rule_id": d.rule_id, "trigger": ctx.trigger, "digest": d.args_digest,
                    "always_user": d.always_user, "origin_id": ctx.origin_id},
        )
    except Exception:
        log.exception("could not write the permission event")
        if d.kind == "allow":
            # Nothing that changes anything runs unrecorded.
            return _make(kind="deny", source="system", reason_code="AUDIT_FAILED",
                         reason="The permission log could not be written, so this was not run.", tool=d.tool,
                         args_digest=d.args_digest, targets=d.targets, tool_kind=d.tool_kind, user_id=d.user_id)
    return d


def _target_text(targets: tuple[Target, ...]) -> str:
    parts = []
    for t in targets:
        if t.kind == "pid":
            continue
        parts.append(f"{t.kind}:{t.value if t.kind != 'command' else t.raw}")
    return "; ".join(parts)[:500]


def _log_review(db: Session, user: User, tool: str, pre: _Pre, v: reviewer.ReviewerVerdict, ctx: GateContext) -> None:
    try:
        events.append(
            db, user.id, "review", actor="aura", tool=tool, target=_target_text(pre.targets),
            outcome="allow" if v.allow else "deny", reason_code=v.code or ("REVIEWER_ALLOW" if v.allow else "REVIEWER_BLOCK"),
            run_key=ctx.run_key,
            detail={"digest": pre.digest, "reason": v.reason, "model": v.model, "latency_ms": v.latency_ms,
                    "tokens": v.tokens},
        )
    except Exception:
        log.exception("could not write the review event")


# ---------------------------------------------------------------------------
# Re-check at approval, the user's own console and UI
# ---------------------------------------------------------------------------

_ORIGIN_TRIGGER = {"chat": "chat", "schedule": "schedule", "automation": "automation", "inbound": "inbound"}


def context_for_action(db: Session, user: User, action: PendingAction) -> GateContext:
    """Rebuild the context a parked call was made in (spec 6.4)."""
    trigger = _ORIGIN_TRIGGER.get(action.origin or "chat", "chat")
    approval_mode = None
    if trigger == "schedule" and action.origin_id:
        row = db.get(Schedule, action.origin_id)
        approval_mode = row.approval_mode if row else None
    elif trigger == "automation" and action.origin_id:
        row = db.get(AutomationRule, action.origin_id)
        approval_mode = row.approval_mode if row else None
    return build_context(db, user, trigger, conversation_id=action.conversation_id, work_item_id=action.work_item_id,
                         origin_id=action.origin_id or "", approval_mode=approval_mode)


def decide_approved(db: Session, user: User, action: PendingAction) -> Decision:
    """After the user said yes: steps 0 to 5 again, because things may have changed since the
    card was shown (a folder disconnected, a block rule added). Passing them allows the call."""
    ctx = context_for_action(db, user, action)
    args = action.arguments or {}
    if action.args_digest and action.args_digest != args_digest(args):
        d = _deny("system", "ARGS_CHANGED", "The request changed after it was shown to you, so it was not run.",
                  tool=action.tool_name, digest=args_digest(args), user=user)
        return _finish(db, user, d, ctx)
    pre = _head(db, user, action.tool_name, args, ctx)
    if isinstance(pre, Decision):
        d = pre
        if d.kind == "allow":  # loop bookkeeping never reaches an approval
            d = _deny("system", "UNKNOWN_TOOL", "That cannot be approved.", tool=action.tool_name,
                      digest=args_digest(args), user=user)
    else:
        d = _allow(pre, "rule", "APPROVED_BY_USER", "You approved this.", user=user, tool=action.tool_name)
    return _finish(db, user, d, ctx)


def console_decision(db: Session, user: User, command: str, workdir: str | None = None) -> Decision:
    """The user's own console: steps 0 to 2, the install denylist, and block rules only."""
    from app.agent.tools import REGISTRY  # noqa: F401  (ensures tools are registered)

    ctx = build_context(db, user, "console")
    args = {"command": command, **({"workdir": workdir} if workdir else {})}
    digest = args_digest(args)
    policy = catalogue.CATALOGUE["run_command"]
    from app import plugins
    from app.agent import skills as skill_registry

    def deny(source: str, code: str, reason: str, rid: str | None = None, targets=()) -> Decision:
        return _finish(db, user, _deny(source, code, reason, tool="run_command", digest=digest, user=user,
                                       policy=policy, targets=targets, rule_id=rid), ctx)

    try:
        targets = match.extract(policy, args, dialect=_dialect(policy))
    except BadArgs as exc:
        return deny("system", "BAD_ARGS", f"The command could not be read: {exc}")
    if policy.system not in plugins.assigned_systems(db, user.id) or "run_command" not in skill_registry.enabled_tools(db, user.id):
        return deny("system", "SYSTEM_NOT_ASSIGNED", "Command running is not turned on for you.", targets=targets)
    if not settings.device_control_enabled:
        return deny("floor", "INSTALL_OFF", "Device control is off on this install.", targets=targets)
    fl = floors.env_floor(targets)
    if fl.deny:
        return deny("floor", fl.code, fl.reason, targets=targets)
    applicable = [r for r in rules_store.stored(db, user.id, policy, ctx) if match.rule_applies_to(r, targets)]
    block = match.first_match("block", applicable, targets, _dialect(policy))
    if block:
        return deny("rule", "RULE_BLOCK", "A rule you set blocks this.", block.id, targets)
    d = _make(kind="allow", source="mode", reason_code="USER_CONSOLE", reason="You typed this yourself.",
              tool="run_command", args_digest=digest, targets=targets, tool_kind=policy.kind, user_id=user.id)
    return _finish(db, user, d, ctx)


def ui_decision(db: Session, user: User, tool: str, args: Any) -> Decision:
    """An action the user took with their own click (free slots, meeting brief): steps 0 to 2 and 5,
    for read kinds only."""
    ctx = build_context(db, user, "user_ui")
    pre = _head(db, user, tool, args, ctx)
    if isinstance(pre, Decision):
        return _finish(db, user, pre, ctx)
    if pre.policy.kind not in READ_KINDS:
        d = _deny("system", "UI_NOT_READ_ONLY", "Only reads can be started from a button.", tool=tool,
                  digest=pre.digest, user=user, policy=pre.policy, targets=pre.targets)
    else:
        d = _allow(pre, "mode", "USER_UI", "You asked for this.", user=user, tool=tool)
    return _finish(db, user, d, ctx)
