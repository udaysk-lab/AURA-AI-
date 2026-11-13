"""Parked approvals: queueing, answering, expiry (spec 10 section 6).

A call the gate answers with `ask` is parked as a PendingAction (`queue_action`). The user
answers it from the web app, a paired phone or the terminal (`resolve`); if nobody does, it
lapses (`expire_due`). Three properties matter here:

* Answering is idempotent. The first answer claims the row with one conditional UPDATE
  (`pending` -> `resolving`); a second click gets a 409 and changes nothing.
* The gate looks again before anything runs (`gate.decide_approved`): a block rule added, a
  folder disconnected or arguments edited since the card was shown win over the click.
* What a click may do depends on who clicked: a paired phone or the keyless terminal login can
  say "once" or "no", never "for 10 minutes", "always" or anything that widens access.

The approved tool runs inside the answering request. The task loop's hook
(`agent.taskloop.on_pending_resolved`) is told the result and stores it; this module never writes
`PendingAction.result` and never runs a model segment.
"""

from __future__ import annotations

import hashlib
import importlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import timeutil
from app.config import settings
from app.llm import ToolCall
from app.models import (
    ActivityLog,
    DeviceAppBlock,
    DeviceAppGrant,
    DeviceFolder,
    Notification,
    PendingAction,
    Schedule,
    AutomationRule,
    User,
    utcnow,
)
from app.permissions import access, catalogue, events, gate, match
from app.permissions import rules as rules_store

log = logging.getLogger("aura.approvals")

WINDOW_MINUTES = 10  # the fixed length of a "for 10 minutes" answer
RESOLVING_STALE = timedelta(minutes=5)  # a claim older than this was abandoned (a crash)
SPECIAL_CARDS = ("folder_access", "app_grant", "delete_permission")
ORIGINS = ("chat", "schedule", "automation", "inbound")
UNATTENDED = ("schedule", "automation", "inbound")
# Parked approvals of these origins are not re-created while an identical one still waits: a
# rule that fires every minute must not pile up a card (and a notification) per firing.
DEDUPE_ORIGINS = ("schedule", "automation")
TIERS = ("view", "click", "full")
FULL_OPTIONS = ("once", "window", "always", "reject")
ONCE_OPTIONS = ("once", "reject")
SPECIAL_OPTIONS = ("approve", "reject")


# ---------------------------------------------------------------------------
# Who is answering, and errors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Caller:
    """Who is answering a card (spec 6.3).

    session     the owner's signed-in session (web app on the hosted site, or the local app)
    remote      a paired phone or other device
    system      the server itself (expiry)
    """

    kind: str = "session"
    device_id: str = ""

    @property
    def resolved_by(self) -> str:
        if self.kind == "remote":
            return f"remote:{self.device_id}"[:40]
        return self.kind

    @property
    def restricted(self) -> bool:
        return self.kind == "remote"


class ApprovalError(Exception):
    """An answer that cannot be taken. The API turns it into `status_code` and a JSON body."""

    def __init__(self, status_code: int, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.extra = extra

    def body(self) -> dict:
        return {"detail": self.detail, **self.extra}


class _Refused(Exception):
    """Raised while preparing an approved call: it cannot be carried out any more."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _words(tool: str) -> str:
    return tool.replace("_", " ").strip()


def is_special(action: PendingAction) -> bool:
    return (action.card or "tool") in SPECIAL_CARDS


def default_options(action: PendingAction) -> tuple[str, ...]:
    """The answers a card offers. Rows written before options were recorded (empty list)
    get the set their kind of card would have had."""
    stored = tuple(action.options or ())
    if stored:
        return stored
    if is_special(action):
        return SPECIAL_OPTIONS
    if action.always_user:
        return ONCE_OPTIONS
    return FULL_OPTIONS


# ---------------------------------------------------------------------------
# The sentence on the card: what "always" would allow
# ---------------------------------------------------------------------------

_VERB = {
    "send_email": "sending email",
    "archive_email": "archiving email",
    "create_event": "creating calendar events",
    "delete_event": "deleting calendar events",
    "delete_task": "deleting tasks",
    "run_command": "running commands",
    "run_in_background": "running commands in the background",
    "stop_process": "stopping processes",
    "kill_process": "ending processes",
    "write_file": "writing files",
    "edit_file": "editing files",
    "make_directory": "creating folders",
    "move_file": "moving files",
    "open_app": "opening apps",
    "close_app": "closing apps",
    "set_clipboard": "changing the clipboard",
    "mouse_click": "clicking with the mouse",
    "scroll_screen": "scrolling the screen",
    "type_text": "typing",
    "press_keys": "pressing keys",
    "fetch_url": "fetching web pages",
    "send_process_input": "sending input to a running command",
}


def _path_phrase(pattern: str) -> str:
    if pattern.endswith(("/**", "\\**")):
        return f"in {pattern[:-3] or os.sep} and the folders below it"
    tail = os.path.split(pattern)[1]
    if any(ch in tail for ch in "*?["):
        return f"for files matching {pattern}"
    return f"for {pattern}"


def rule_text(draft: gate.RuleDraft | None, *, lead: str = "Always allow") -> str:
    """One sentence saying exactly what the rule stored by "always" would allow."""
    if draft is None:
        return ""
    verb = _VERB.get(draft.tool) or f"using {_words(draft.tool)}"
    groups: dict[str, list[str]] = {}
    for kind, pattern in draft.items:
        groups.setdefault(kind, []).append(pattern)
    parts: list[str] = []
    for kind, patterns in groups.items():
        if kind == "recipient":
            joined = ", ".join(patterns)
            parts.append(f"inviting {joined}" if draft.tool == "create_event" else f"to {joined}")
        elif kind == "path":
            parts.extend(_path_phrase(p) for p in patterns)
        elif kind == "command":
            for p in patterns:
                parts.append(f"for exactly `{p[1:]}`" if p.startswith("=") else f"for commands starting with `{p}`")
        elif kind == "domain":
            parts.append("for " + ", ".join(f"{p} and its subdomains" for p in patterns))
        elif kind == "app":
            parts.append("for " + ", ".join(patterns))
        else:
            parts.append("for " + ", ".join(patterns))
    text = f"{lead} {verb}"
    if parts:
        text += " " + " and ".join(parts)
    if draft.applies_to.startswith("schedule:"):
        text += ", only in that schedule"
    elif draft.applies_to.startswith("automation:"):
        text += ", only in that automation"
    return text


def _draft_json(draft: gate.RuleDraft) -> dict:
    return {
        "system": draft.system, "tool": draft.tool, "applies_to": draft.applies_to, "effect": draft.effect,
        "items": [{"target_kind": k, "pattern": p} for k, p in draft.items],
    }


def _draft_from_json(data: Any) -> gate.RuleDraft | None:
    try:
        return gate.RuleDraft(
            system=str(data["system"]), tool=str(data["tool"]), applies_to=str(data.get("applies_to") or ""),
            items=tuple((str(i["target_kind"]), str(i["pattern"])) for i in data.get("items") or ()),
            effect="allow",
        )
    except (KeyError, TypeError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def _find_waiting(db: Session, user: User, call: ToolCall, digest: str, origin: str, origin_id: str) -> PendingAction | None:
    now = utcnow()
    rows = db.scalars(
        select(PendingAction).where(
            PendingAction.user_id == user.id, PendingAction.status == "pending",
            PendingAction.tool_name == call.name, PendingAction.args_digest == digest,
            PendingAction.origin == origin, PendingAction.origin_id == origin_id,
        )
    ).all()
    for row in rows:
        expires = _aware(row.expires_at)
        if expires is None or expires > now:
            return row
    return None


def _notify(db: Session, user: User, action: PendingAction, ctx) -> None:
    """Somebody has to hear about a card nobody is watching (schedule, automation, inbound)."""
    what = _words(action.tool_name)
    if action.origin == "schedule":
        row = db.get(Schedule, action.origin_id) if action.origin_id else None
        who = f"Your schedule “{row.name}”" if row else "A schedule"
    elif action.origin == "automation":
        row = db.get(AutomationRule, action.origin_id) if action.origin_id else None
        who = f"Your automation “{row.name}”" if row else "An automation"
    else:
        who = "A message you sent in"
    when = _aware(action.expires_at)
    expires = f" It lapses on {when.astimezone(timeutil.user_zone(user)):%d %b at %H:%M}." if when else ""
    link = f"/chat?c={action.conversation_id}" if action.conversation_id else "/dashboard"
    db.add(
        Notification(
            user_id=user.id, level="urgent", link=link,
            title=f"Approval needed: {what}"[:300],
            body=f"{who} is waiting for you to approve “{what}”.{expires}",
        )
    )
    db.commit()


def queue_action(
    db: Session,
    user: User,
    ctx: gate.GateContext,
    call: ToolCall,
    decision: gate.Decision,
    tool_call_id: str = "",
    *,
    preview_prefix: str = "",
) -> PendingAction:
    """Park a call the gate answered with `ask` (spec 6.1).

    Fills every card column, sets the expiry from the trigger, writes `approval_created` and, for
    schedules, automations and inbound messages, an urgent notification. `tool_call_id` is the
    loop's own bookkeeping, kept on the row so a resumed run can match the answer to the call.
    """
    from app.agent import skills as skill_registry
    from app.agent import tools as tool_registry

    origin = ctx.trigger if ctx.trigger in ORIGINS else "chat"
    origin_id = ctx.origin_id or ""
    if origin in DEDUPE_ORIGINS:
        waiting = _find_waiting(db, user, call, decision.args_digest, origin, origin_id)
        if waiting is not None:
            return waiting

    card = decision.card or "tool"
    skill = skill_registry.skill_for_tool(call.name)
    preview = preview_prefix + tool_registry.build_preview(
        call.name, call.arguments, timeutil.user_zone(user), db=db, user_id=user.id
    )
    preview = f"{preview}\n\nWhy I'm asking: {decision.reason}"
    options = list(SPECIAL_OPTIONS) if card in SPECIAL_CARDS else list(decision.options)
    payload = dict(decision.card_payload or {})
    draft = decision.proposed_rule
    if draft is not None and "always" in options:
        payload["rule_text"] = rule_text(draft)
        payload["window_text"] = rule_text(draft, lead=f"For {WINDOW_MINUTES} minutes, allow")
        payload["rule_draft"] = _draft_json(draft)
        payload["window_minutes"] = WINDOW_MINUTES
    action = PendingAction(
        user_id=user.id,
        conversation_id=ctx.conversation_id,
        tool_name=call.name,
        arguments=call.arguments,
        preview=preview,
        skill_code=skill.code if skill else "",
        work_item_id=ctx.work_item_id,
        tool_call_id=(tool_call_id or "")[:64],
        origin=origin,
        origin_id=origin_id,
        expires_at=gate.expiry_for(ctx, card),
        card=card,
        card_payload=payload,
        options=options,
        always_user=bool(decision.always_user or card in SPECIAL_CARDS),
        reason_code=decision.reason_code,
        reason=decision.reason,
        args_digest=decision.args_digest,
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    try:
        events.append(
            db, user.id, "approval_created", actor="aura", tool=call.name, outcome="ask",
            reason_code=decision.reason_code, run_key=ctx.run_key,
            detail={"pending_action_id": action.id, "digest": decision.args_digest, "options": options,
                    "card": card, "origin": origin, "origin_id": origin_id},
        )
    except Exception:  # the card exists and can be answered; the log is best effort here
        log.exception("could not write approval_created")
    if origin in UNATTENDED:
        try:
            _notify(db, user, action, ctx)
        except Exception:
            log.exception("could not notify about a parked approval")
            db.rollback()
    return action


# ---------------------------------------------------------------------------
# Hook into the task loop
# ---------------------------------------------------------------------------


def _hook(db: Session, action: PendingAction, result: Any) -> dict | None:
    """Tell the task loop an approval reached a final status. Returns `{"id","status"}` or None.

    The loop module may not exist yet; a failure inside it never undoes the user's answer."""
    if not action.work_item_id:
        return None
    try:
        mod = importlib.import_module("app.agent.taskloop")
    except ModuleNotFoundError as exc:
        if exc.name != "app.agent.taskloop":
            raise
        return None
    fn = getattr(mod, "on_pending_resolved", None)
    if fn is None:
        return None
    try:
        out = fn(db, action, result)
        db.commit()
        return out
    except Exception:
        log.exception("taskloop.on_pending_resolved failed for %s", action.id)
        db.rollback()
        return None


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def _expire_one(db: Session, action: PendingAction) -> bool:
    """Turn one overdue pending row into `expired`; only the caller that wins the UPDATE reports it."""
    now = utcnow()
    won = db.execute(
        update(PendingAction)
        .where(PendingAction.id == action.id, PendingAction.status == "pending")
        .values(status="expired", resolved_at=now, resolved_by="system", updated_at=now)
    ).rowcount
    db.commit()
    db.refresh(action)
    if won != 1:
        return False
    try:
        events.append(
            db, action.user_id, "approval_expired", actor="system", tool=action.tool_name, outcome="expired",
            reason_code=action.reason_code, run_key=action.work_item_id or "",
            detail={"pending_action_id": action.id, "origin": action.origin},
        )
    except Exception:
        log.exception("could not write approval_expired")
    _hook(db, action, None)
    return True


def expire_if_due(db: Session, action: PendingAction) -> bool:
    expires = _aware(action.expires_at)
    if action.status == "pending" and expires is not None and expires <= utcnow():
        return _expire_one(db, action)
    return False


def expire_due(db: Session, user_id: str | None = None) -> int:
    """Expire every overdue pending approval (worker, cron, and lazily when cards are listed).

    A `resolving` claim older than five minutes belonged to a request that died: it is settled as
    `refused` so the run waiting on it can carry on. Returns how many rows changed."""
    now = utcnow()
    changed = 0
    q = select(PendingAction).where(
        PendingAction.status == "pending", PendingAction.expires_at.is_not(None), PendingAction.expires_at <= now
    )
    if user_id:
        q = q.where(PendingAction.user_id == user_id)
    for action in db.scalars(q).all():
        if expire_if_due(db, action):
            changed += 1

    q = select(PendingAction).where(
        PendingAction.status == "resolving", PendingAction.updated_at <= now - RESOLVING_STALE
    )
    if user_id:
        q = q.where(PendingAction.user_id == user_id)
    for action in db.scalars(q).all():
        won = db.execute(
            update(PendingAction)
            .where(PendingAction.id == action.id, PendingAction.status == "resolving")
            .values(status="refused", resolved_at=now, resolved_by="system", updated_at=now,
                    reason="Answering this was interrupted, so it was not run.")
        ).rowcount
        db.commit()
        db.refresh(action)
        if won == 1:
            changed += 1
            _log_resolved(db, action, "refused", actor="system", reason_code="INTERRUPTED")
            _hook(db, action, {"status": "refused", "reason": action.reason})
    return changed


# ---------------------------------------------------------------------------
# Who may answer what
# ---------------------------------------------------------------------------


def stored_answer(action: PendingAction, answer: str) -> str:
    """Validate the answer against the card; `approve` (special cards) is stored as `once`."""
    options = default_options(action)
    if is_special(action):
        if answer in ("approve", "once") and ("approve" in options or "once" in options):
            return "once"
        if answer == "reject":
            return "reject"
        raise ApprovalError(422, "This question can be answered yes or no only.")
    if answer not in options:
        raise ApprovalError(422, f"That answer is not offered here. Choose one of: {', '.join(options)}.")
    return answer


def check_caller(action: PendingAction, answer: str, caller: Caller) -> None:
    """Spec 6.3. Saying no is always allowed; anything that approves is limited by who is asking."""
    if not caller.restricted or answer == "reject":
        return
    if is_special(action) or action.always_user:
        raise ApprovalError(403, "Answer this on the computer.")
    if answer in ("window", "always"):
        raise ApprovalError(403, "Open AURA on the computer to allow this for longer.")


def _already(action: PendingAction) -> ApprovalError:
    if action.status == "expired":
        text = "This approval has expired. Ask AURA to try again."
    elif action.status == "resolving":
        text = "This is being answered right now."
    else:
        text = "This was already answered."
    return ApprovalError(409, text, status=action.status, decision=action.decision or "")


# ---------------------------------------------------------------------------
# Special cards: connect a folder, allow an app, allow deletes in a folder
# ---------------------------------------------------------------------------


path_key = access.path_key  # sha256 of the folded realpath: how a connected folder is recognised (spec 2.5)


def _card_folder(action: PendingAction) -> str:
    raw = (action.card_payload or {}).get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise _Refused("The card does not name a folder.")
    try:
        return match.normalize_path(raw)
    except match.BadArgs as exc:
        raise _Refused(f"The folder cannot be read: {exc}") from exc


def _apply_special_card(db: Session, user: User, action: PendingAction) -> list:
    """Make the change the card asked for. Returns undo steps, used if the re-check then refuses."""
    if not settings.device_control_enabled:
        raise _Refused("Device control is off on this install.")
    undo: list = []
    card = action.card
    payload = action.card_payload or {}

    if card in ("folder_access", "delete_permission"):
        real = _card_folder(action)
        key = path_key(real)
        row = db.scalars(
            select(DeviceFolder).where(DeviceFolder.user_id == user.id, DeviceFolder.path_key == key)
        ).first()
        if card == "folder_access" or (card == "delete_permission" and (row is None or row.revoked_at is not None)):
            connect_delete = card == "delete_permission"
            if card == "delete_permission" and not payload.get("connect"):
                raise _Refused("That folder is no longer connected.")
            if not os.path.isdir(real):
                raise _Refused("That folder does not exist.")
            if not access.within_ceiling(real):
                raise _Refused("That folder is outside the limit this install sets.")
            if os.path.dirname(real) == real:
                raise _Refused("A whole drive cannot be connected.")
            if row is None:
                row = DeviceFolder(user_id=user.id, path=real, path_key=key, can_delete=connect_delete, source="request")
                db.add(row)
                db.flush()
                undo.append(lambda r=row: _undo_folder(db, user, r, revoke=True))
            elif row.revoked_at is not None:
                row.revoked_at, row.can_delete, row.source = None, connect_delete, "request"
                db.flush()
                undo.append(lambda r=row: _undo_folder(db, user, r, revoke=True))
            else:
                return undo  # already connected: nothing to add
            events.append(db, user.id, "folder_added", actor="user", tool=action.tool_name, target=real,
                          outcome="allow", detail={"folder_id": row.id, "via": "approval_card",
                                                   "can_delete": connect_delete,
                                                   "pending_action_id": action.id})
        else:
            if row is None or row.revoked_at is not None:
                raise _Refused("That folder is no longer connected.")
            if not row.can_delete:
                row.can_delete = True
                db.flush()
                undo.append(lambda r=row: _undo_delete(db, user, r))
                events.append(db, user.id, "folder_changed", actor="user", tool=action.tool_name, target=real,
                              outcome="allow", detail={"folder_id": row.id, "can_delete": True,
                                                       "pending_action_id": action.id})
        return undo

    # app_grant
    from app.device import appnames

    app_id = appnames.canonical(str(payload.get("app_id") or ""))
    tier = str(payload.get("tier") or "")
    if not app_id or tier not in TIERS:
        raise _Refused("The card does not name an app and a level.")
    if (appnames.is_terminal(app_id) or appnames.is_ide(app_id)) and tier == "full":
        raise _Refused("Terminals and code editors can be allowed to click and scroll only.")
    for block in db.scalars(select(DeviceAppBlock).where(DeviceAppBlock.user_id == user.id)).all():
        hit = app_id == block.app_id if block.match == "app" else block.app_id in app_id
        if hit:
            raise _Refused("That app is on your blocked list.")
    row = db.scalars(
        select(DeviceAppGrant).where(DeviceAppGrant.user_id == user.id, DeviceAppGrant.app_id == app_id)
    ).first()
    name = str(payload.get("display_name") or app_id)[:200]
    if row is None:
        row = DeviceAppGrant(user_id=user.id, app_id=app_id, display_name=name, tier=tier)
        db.add(row)
        db.flush()
        undo.append(lambda r=row: _undo_grant(db, user, r, None, None))
    else:
        before = (row.tier, row.revoked_at)
        row.tier, row.revoked_at, row.display_name = tier, None, name
        db.flush()
        undo.append(lambda r=row, b=before: _undo_grant(db, user, r, b[0], b[1]))
    events.append(db, user.id, "app_granted", actor="user", tool=action.tool_name, target=app_id, outcome="allow",
                  detail={"tier": tier, "via": "approval_card", "pending_action_id": action.id})
    return undo


def _undo_folder(db: Session, user: User, row: DeviceFolder, *, revoke: bool) -> None:
    row.revoked_at = utcnow()
    db.commit()
    events.append(db, user.id, "folder_removed", actor="system", target=row.path, outcome="deny",
                  detail={"folder_id": row.id, "why": "the approval was refused at the re-check"})


def _undo_delete(db: Session, user: User, row: DeviceFolder) -> None:
    row.can_delete = False
    db.commit()
    events.append(db, user.id, "folder_changed", actor="system", target=row.path, outcome="deny",
                  detail={"folder_id": row.id, "can_delete": False,
                          "why": "the approval was refused at the re-check"})


def _undo_grant(db: Session, user: User, row: DeviceAppGrant, tier: str | None, revoked_at) -> None:
    if tier is None:
        row.revoked_at = utcnow()
    else:
        row.tier, row.revoked_at = tier, revoked_at
    db.commit()
    events.append(db, user.id, "app_revoked", actor="system", target=row.app_id, outcome="deny",
                  detail={"why": "the approval was refused at the re-check"})


# ---------------------------------------------------------------------------
# Resolving
# ---------------------------------------------------------------------------


def _claim(db: Session, user: User, action: PendingAction) -> bool:
    now = utcnow()
    n = db.execute(
        update(PendingAction)
        .where(PendingAction.id == action.id, PendingAction.user_id == user.id, PendingAction.status == "pending")
        .values(status="resolving", updated_at=now)
    ).rowcount
    db.commit()
    db.refresh(action)
    return n == 1


def _log_resolved(db: Session, action: PendingAction, status: str, *, actor: str, reason_code: str = "",
                  extra: dict | None = None) -> None:
    try:
        events.append(
            db, action.user_id, "approval_resolved", actor=actor, tool=action.tool_name, outcome=status,
            reason_code=reason_code or action.reason_code, run_key=action.work_item_id or "",
            detail={"pending_action_id": action.id, "decision": action.decision, "resolved_by": action.resolved_by,
                    "rule_id": action.rule_id, "card": action.card, **(extra or {})},
        )
    except Exception:
        log.exception("could not write approval_resolved")


def _settle(db: Session, user: User, action: PendingAction, status: str, answer: str, caller: Caller,
            *, reason: str = "", rule_id: str | None = None) -> None:
    """Write the final status (`approved` is written before the tool runs, the rest here)."""
    action.status = status
    action.decision = answer
    action.resolved_at = utcnow()
    action.resolved_by = caller.resolved_by
    if rule_id:
        action.rule_id = rule_id
    if status == "refused" and reason:
        payload = dict(action.card_payload or {})
        payload["asked_because"] = action.reason
        action.card_payload = payload
        action.reason = reason[:2000]
    db.commit()


def _payload_for(db: Session, action: PendingAction, body: dict, hook_result: Any) -> dict:
    out = dict(body)
    out["decision"] = action.decision
    out["work_item"] = _hook(db, action, hook_result)
    return out


def resolve(db: Session, user: User, action_id: str, answer: str, caller: Caller | None = None) -> dict:
    """Take the user's answer to a parked call (spec 6.4). Raises ApprovalError for 404/403/409/422."""
    caller = caller or Caller()
    action = db.get(PendingAction, action_id)
    if action is None or action.user_id != user.id:
        raise ApprovalError(404, "Action not found")
    expire_if_due(db, action)
    if action.status != "pending":
        raise _already(action)
    stored = stored_answer(action, answer)
    check_caller(action, stored, caller)
    if not _claim(db, user, action):
        raise _already(action)

    if stored == "reject":
        _settle(db, user, action, "rejected", "reject", caller)
        db.add(ActivityLog(user_id=user.id, actor="user", action=f"reject:{action.tool_name}",
                           target=action.preview[:300], status="rejected"))
        db.commit()
        _log_resolved(db, action, "rejected", actor="user")
        return _payload_for(db, action, {"status": "rejected",
                                         "message": f"Cancelled — {action.tool_name} was not run."},
                            {"status": "rejected"})

    return _approve(db, user, action, stored, caller)


def _create_rules(db: Session, user: User, action: PendingAction, answer: str) -> tuple[list, str]:
    """The rules a "for 10 minutes" / "always" answer stores. Returns (rules, note)."""
    draft = _draft_from_json((action.card_payload or {}).get("rule_draft"))
    if draft is None:
        draft = gate.proposed_rule_for(db, user, action)
    if draft is None:
        return [], "This kind of action can only be allowed one time, so no rule was saved."
    policy = catalogue.policy_for(action.tool_name)
    expires = utcnow() + timedelta(minutes=WINDOW_MINUTES) if answer == "window" else None
    lead = f"For {WINDOW_MINUTES} minutes, allow" if answer == "window" else "Always allow"
    text = rule_text(draft, lead=lead)
    made = []
    try:
        for kind, pattern in draft.items or (("", ""),):
            made.append(rules_store.add(
                db, user, effect="allow", system=draft.system, tool=draft.tool, target_kind=kind, pattern=pattern,
                applies_to=draft.applies_to, source="card", expires_at=expires, note=text, actor="user",
                dialect=gate._dialect(policy) if policy else "posix",
            ))
    except match.PatternError as exc:
        log.warning("card rule not saved: %s", exc)
        for r in made:  # keep it all-or-nothing: never a half-saved rule set
            rules_store.revoke(db, user, r.id, actor="system")
        return [], "The call was allowed once, but the rule could not be saved."
    return made, ""


def _approve(db: Session, user: User, action: PendingAction, answer: str, caller: Caller) -> dict:
    from app.agent.coordinator import execute_tool

    undo: list = []
    try:
        if is_special(action):
            undo = _apply_special_card(db, user, action)
        d = gate.decide_approved(db, user, action)
        if d.kind != "allow":
            raise _Refused(d.reason)
    except _Refused as exc:
        for step in reversed(undo):
            step()
        return _refused(db, user, action, answer, caller, str(exc))
    except Exception:
        log.exception("re-checking approval %s failed", action.id)
        for step in reversed(undo):
            try:
                step()
            except Exception:
                log.exception("undo failed")
        return _refused(db, user, action, answer, caller, "Something went wrong while checking this, so it was not run.")

    made: list = []
    note = ""
    if action.card == "tool" and answer in ("window", "always"):
        made, note = _create_rules(db, user, action, answer)

    _settle(db, user, action, "approved", answer, caller, rule_id=made[0].id if made else None)
    try:
        outcome = execute_tool(
            db, user, action.tool_name, action.arguments or {}, trigger=action.origin or "chat",
            work_item_id=action.work_item_id, decision=d,
        )
    except PermissionError as exc:  # the seal or the arguments no longer match: nothing ran
        return _refused(db, user, action, answer, caller, str(exc))

    db.add(ActivityLog(
        user_id=user.id, actor="user", action=f"approve:{action.tool_name}", target=action.preview[:300],
        detail={"decision": answer, "result": str(outcome)[:500]},
    ))
    db.commit()
    _log_resolved(db, action, "approved", actor="user",
                  extra={"rules": [r.id for r in made], "executed": True})

    body = dict(outcome) if isinstance(outcome, dict) else {"result": outcome}
    if isinstance(body.get("image"), dict):  # a screenshot is not carried through the answer
        body.pop("image")
        body["image_omitted"] = True
    stored_result = dict(body)
    body.setdefault("status", "approved")
    if made:
        r = made[0]
        body["rule"] = {
            "id": r.id, "tool": r.tool, "effect": r.effect, "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "text": (r.note or ""), "count": len(made),
        }
    if note:
        body["note"] = note
    return _payload_for(db, action, body, stored_result)


def _refused(db: Session, user: User, action: PendingAction, answer: str, caller: Caller, reason: str) -> dict:
    _settle(db, user, action, "refused", answer, caller, reason=reason)
    _log_resolved(db, action, "refused", actor="user", reason_code="REFUSED", extra={"why": reason[:300]})
    return _payload_for(
        db, action,
        {"status": "refused", "message": f"It was approved, but is no longer allowed: {reason}", "reason": reason},
        {"status": "refused", "reason": reason},
    )
