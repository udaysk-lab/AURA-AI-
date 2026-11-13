"""The permissions API (spec 10 section 11): mode, boundaries, catalogue, rules, event log.

Reading is open to the signed-in user. Every change needs presence (`PresentUser`): on a local
install that means a request from this computer, not from a paired device, from a session opened
with the sign-in key, and not from a program AURA started; and every change is written to the
permission log and announced to the user (`changes.record`). Nothing AURA does in a run can reach
these routes: it has no credential and no tool that calls them.

Shapes follow the web client (frontend/lib/api.ts: PermissionState, CatalogueEntry,
PermissionRule, NewPermissionRule, PermissionEvent, EventsVerify).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import settings
from app.deps import CurrentUser, DbSession, PresentUser, get_user_settings
from app.models import PermissionRule, User, utcnow
from app.permissions import catalogue, changes, events, gate, legacy, match, reviewer
from app.permissions import rules as rules_store

log = logging.getLogger("aura.permissions.api")
router = APIRouter(prefix="/api/permissions", tags=["permissions"])

MODE_LABEL_RANK = {"ask": 0, "auto": 1, "skip": 2}
MAX_MINUTES = 60 * 24 * 365


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


class PermissionStateOut(BaseModel):
    mode: Literal["ask", "auto", "skip"]
    mode_label: str
    auto_available: bool
    auto_effective: bool
    reviewer_model: str
    boundaries: str


class ModeIn(BaseModel):
    mode: Literal["ask", "auto", "skip"]


class BoundariesIn(BaseModel):
    text: str = Field(default="", max_length=2000)


class CatalogueEntryOut(BaseModel):
    tool: str
    system: str
    kind: str
    targets: list[str]
    always_user: bool


class RuleOut(BaseModel):
    id: str | None = None
    effect: str
    system: str
    tool: str | None = None
    target_kind: str = ""
    pattern: str = ""
    applies_to: str = ""
    source: str = "user"
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    note: str = ""
    created_at: datetime | None = None


class RuleIn(BaseModel):
    effect: Literal["block", "ask", "allow"]
    system: str = Field(min_length=1, max_length=60)
    tool: str | None = Field(default=None, max_length=120)
    target_kind: Literal["", "path", "command", "recipient", "domain", "app"] = ""
    pattern: str = Field(default="", max_length=1024)
    applies_to: str = Field(default="", max_length=80)
    minutes: int | None = Field(default=None, ge=1, le=MAX_MINUTES)
    note: str = Field(default="", max_length=300)


class RulePatch(BaseModel):
    effect: Literal["block", "ask", "allow"] | None = None
    pattern: str | None = Field(default=None, max_length=1024)
    note: str | None = Field(default=None, max_length=300)
    minutes: int | None = Field(default=None, ge=1, le=MAX_MINUTES)


class EventOut(BaseModel):
    seq: int
    actor: str
    kind: str
    tool: str = ""
    target: str = ""
    outcome: str = ""
    reason_code: str = ""
    run_key: str = ""
    detail: dict = Field(default_factory=dict)
    created_at: datetime


class VerifyOut(BaseModel):
    ok: bool
    checked: int
    first_bad_seq: int | None = None


# ---------------------------------------------------------------------------
# Mode and boundaries
# ---------------------------------------------------------------------------


def _state(db, user: User) -> PermissionStateOut:
    mode = gate.stored_mode(db, user)
    row = get_user_settings(db, user)
    available = reviewer.available()
    return PermissionStateOut(
        mode=mode,
        mode_label=gate.mode_label(db, user),
        auto_available=available,
        auto_effective=mode == "auto" and available,
        reviewer_model=reviewer.model_name() or "",
        boundaries=row.reviewer_boundaries or "",
    )


def _settle_legacy(db, user: User) -> None:
    """The first look after the 1.0 mapping ran: put it in the user's log."""
    legacy.emit_pending_events(db, user)


@router.get("", response_model=PermissionStateOut)
def get_state(user: CurrentUser, db: DbSession) -> PermissionStateOut:
    _settle_legacy(db, user)
    return _state(db, user)


@router.put("/mode", response_model=PermissionStateOut)
def set_mode(payload: ModeIn, user: PresentUser, db: DbSession) -> PermissionStateOut:
    if payload.mode == "auto" and not reviewer.available():
        raise HTTPException(
            status_code=409,
            detail="Auto needs a connected AI model to review each action. The built-in demo model can't do that.",
        )
    row = get_user_settings(db, user)
    old = gate.stored_mode(db, user)
    if payload.mode != old:
        row.permission_mode = payload.mode
        row.updated_at = utcnow()
        db.commit()
        changes.record(
            db, user, "mode_changed", f"mode: {old} to {payload.mode}",
            widens=MODE_LABEL_RANK[payload.mode] > MODE_LABEL_RANK[old], target="mode",
            detail={"from": old, "to": payload.mode},
        )
    return _state(db, user)


@router.put("/boundaries", response_model=PermissionStateOut)
def set_boundaries(payload: BoundariesIn, user: PresentUser, db: DbSession) -> PermissionStateOut:
    text = payload.text.strip()
    row = get_user_settings(db, user)
    if text != (row.reviewer_boundaries or "").strip():
        row.reviewer_boundaries = text
        row.updated_at = utcnow()
        db.commit()
        changes.record(
            db, user, "boundaries_changed", "limits for the safety reviewer changed", target="boundaries",
            detail={"length": len(text)},  # the text itself is the user's; the log keeps its size only
        )
    return _state(db, user)


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


@router.get("/catalogue", response_model=list[CatalogueEntryOut])
def get_catalogue(user: CurrentUser) -> list[CatalogueEntryOut]:
    return [CatalogueEntryOut(**row) for row in catalogue.describe()]


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite hands datetimes back without a zone; the client should always get one."""
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _rule_out(row: PermissionRule) -> RuleOut:
    return RuleOut(
        id=row.id, effect=row.effect, system=row.system, tool=row.tool, target_kind=row.target_kind or "",
        pattern=row.pattern or "", applies_to=row.applies_to or "", source=row.source,
        expires_at=_aware(row.expires_at), revoked_at=_aware(row.revoked_at), note=row.note or "",
        created_at=_aware(row.created_at),
    )


def _install_rules() -> list[RuleOut]:
    """What the install itself blocks (read-only, no id): the environment's command denylist."""
    if not settings.device_control_enabled:
        return []
    return [
        RuleOut(
            id=None, effect="block", system="device-control", tool="run_command", target_kind="command",
            pattern="~" + entry, source="install",
            note="Set by how AURA was installed (DEVICE_COMMAND_DENYLIST). Also covers background commands.",
        )
        for entry in settings.device_command_deny_list
    ]


@router.get("/rules", response_model=list[RuleOut])
def list_rules(
    user: CurrentUser,
    db: DbSession,
    effect: Literal["block", "ask", "allow"] | None = None,
    system: str | None = Query(default=None, max_length=60),
    tool: str | None = Query(default=None, max_length=120),
    include_expired: bool = False,
) -> list[RuleOut]:
    q = select(PermissionRule).where(PermissionRule.user_id == user.id)
    if effect:
        q = q.where(PermissionRule.effect == effect)
    if system:
        q = q.where(PermissionRule.system == system)
    if tool:
        q = q.where(PermissionRule.tool == tool)
    rows = db.scalars(q.order_by(PermissionRule.created_at, PermissionRule.id)).all()
    now = utcnow()
    live = [
        r for r in rows
        if include_expired or (r.revoked_at is None and (r.expires_at is None or _aware(r.expires_at) > now))
    ]
    out = [_rule_out(r) for r in live]
    installed = [
        r for r in _install_rules()
        if (not effect or r.effect == effect) and (not system or r.system == system) and (not tool or r.tool == tool)
    ]
    return out + installed


def _pattern_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _dialect_for(tool: str | None) -> str:
    policy = catalogue.policy_for(tool) if tool else None
    if policy is None:
        from app.permissions import shellparse

        return shellparse.POSIX
    return gate.dialect_for(policy)


@router.post("/rules", response_model=RuleOut)
def add_rule(payload: RuleIn, user: PresentUser, db: DbSession) -> RuleOut:
    if payload.target_kind and not payload.pattern.strip():
        raise HTTPException(status_code=422, detail="Say what the rule is limited to, or leave the target out.")
    if payload.pattern.strip() and not payload.target_kind:
        raise HTTPException(status_code=422, detail="A pattern needs a target kind (path, command, recipient, domain or app).")
    expires = utcnow() + timedelta(minutes=payload.minutes) if payload.minutes else None
    try:
        row = rules_store.add(
            db, user, effect=payload.effect, system=payload.system.strip(), tool=(payload.tool or None),
            target_kind=payload.target_kind, pattern=payload.pattern, applies_to=payload.applies_to,
            source="user", expires_at=expires, note=payload.note, dialect=_dialect_for(payload.tool),
        )
        db.commit()
    except match.PatternError as exc:
        db.rollback()
        raise _pattern_error(exc) from exc
    changes.record(
        db, user, "rule_added", f"{payload.effect} rule for {row.tool or row.system}",
        widens=row.effect == "allow", tool=row.tool or "", log_event=False, detail={"rule_id": row.id},
    )
    return _rule_out(row)


def _own_rule(db, user: User, rule_id: str) -> PermissionRule:
    row = db.get(PermissionRule, rule_id)
    if row is None or row.user_id != user.id or row.revoked_at is not None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return row


@router.patch("/rules/{rule_id}", response_model=RuleOut)
def patch_rule(rule_id: str, payload: RulePatch, user: PresentUser, db: DbSession) -> RuleOut:
    """Change a rule's effect, pattern, note or lifetime.

    The rule is replaced, not edited: the old one is revoked and a new one stored in its place
    (what the web client did with POST + DELETE), so the log shows exactly what was allowed
    before and what is allowed now. The answer is the new rule, with a new id."""
    old = _own_rule(db, user, rule_id)
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=422, detail="Nothing to change.")
    effect = fields.get("effect") or old.effect
    pattern = old.pattern if fields.get("pattern") is None else fields["pattern"]
    if old.target_kind == "" and (fields.get("pattern") or "").strip():
        raise HTTPException(status_code=422, detail="This rule has no target to limit, so it has no pattern to change.")
    if old.target_kind and not (pattern or "").strip():
        raise HTTPException(status_code=422, detail="Say what the rule is limited to.")
    note = old.note if fields.get("note") is None else fields["note"]
    if fields.get("minutes"):
        expires = utcnow() + timedelta(minutes=fields["minutes"])
    else:
        expires = old.expires_at
    try:
        new = rules_store.add(
            db, user, effect=effect, system=old.system, tool=old.tool, target_kind=old.target_kind,
            pattern=pattern, applies_to=old.applies_to, source="user", expires_at=expires, note=note or "",
            dialect=_dialect_for(old.tool),
        )
        rules_store.revoke(db, user, old.id)
        db.commit()
    except match.PatternError as exc:
        db.rollback()
        raise _pattern_error(exc) from exc
    changes.record(
        db, user, "rule_added", f"{effect} rule for {new.tool or new.system} changed",
        widens=effect == "allow", tool=new.tool or "", log_event=False,
        detail={"rule_id": new.id, "replaces": old.id},
    )
    return _rule_out(new)


@router.delete("/rules/{rule_id}")
def revoke_rule(rule_id: str, user: PresentUser, db: DbSession) -> dict:
    row = _own_rule(db, user, rule_id)
    rules_store.revoke(db, user, row.id)
    db.commit()
    changes.record(db, user, "rule_revoked", f"{row.effect} rule for {row.tool or row.system} removed",
                   tool=row.tool or "", log_event=False, detail={"rule_id": row.id})
    return {"revoked": True}


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------


@router.get("/events", response_model=list[EventOut])
def list_events(
    user: CurrentUser,
    db: DbSession,
    before_seq: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[EventOut]:
    _settle_legacy(db, user)
    rows = events.listing(db, user.id, before_seq=before_seq, limit=limit)
    return [
        EventOut(
            seq=r.seq, actor=r.actor, kind=r.kind, tool=r.tool or "", target=r.target or "", outcome=r.outcome or "",
            reason_code=r.reason_code or "", run_key=r.run_key or "", detail=r.detail or {}, created_at=_aware(r.created_at),
        )
        for r in rows
    ]


@router.get("/events/verify", response_model=VerifyOut)
def verify_events(user: CurrentUser, db: DbSession) -> VerifyOut:
    _settle_legacy(db, user)
    result = events.verify(db, user.id)
    return VerifyOut(ok=result["ok"], checked=result["checked"], first_bad_seq=result["first_bad_seq"])  # the shape the client has
