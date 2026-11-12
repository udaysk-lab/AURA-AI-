"""Rule storage and lookup (spec 10 sections 2.2 and 4.3 step 5).

`active(...)` returns the rules that can apply to one call, as plain `Rule`
values the matchers in `match.py` understand. The gate evaluates them in the
order block, ask, allow; this module only decides which rules are in play:
not revoked, not expired, for this tool's system and tool, and limited either
to everywhere or to the schedule/automation this call belongs to.

Writers (`add`, `revoke`) are used by the approval lifecycle and the API (later
packages). Both write an event. A rule is never deleted: revoking sets
`revoked_at`, so the audit trail keeps what was once allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import PermissionRule, User, utcnow
from app.permissions import catalogue, events, match, shellparse

EFFECTS = ("block", "ask", "allow")
SOURCES = ("user", "card", "legacy")


@dataclass(frozen=True)
class Rule:
    """The matching-relevant view of a stored rule."""

    id: str
    effect: str
    system: str = "*"
    tool: str | None = None
    target_kind: str = ""
    pattern: str = ""
    applies_to: str = ""
    source: str = "user"
    expires_at: datetime | None = None


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def scope_of(ctx) -> str:
    """`schedule:<id>` / `automation:<id>` for those triggers, else ''."""
    if ctx.trigger in ("schedule", "automation") and ctx.origin_id:
        return f"{ctx.trigger}:{ctx.origin_id}"
    return ""


def _to_rule(row: PermissionRule) -> Rule:
    return Rule(
        id=row.id, effect=row.effect, system=row.system, tool=row.tool,
        target_kind=row.target_kind or "", pattern=row.pattern or "",
        applies_to=row.applies_to or "", source=row.source, expires_at=_aware(row.expires_at),
    )


def in_play(rule: Rule, policy: catalogue.ToolPolicy, scope: str, now: datetime) -> bool:
    if rule.system not in ("*", policy.system):
        return False
    if rule.tool is not None and rule.tool != policy.name:
        return False
    if rule.applies_to and rule.applies_to != scope:
        return False
    if rule.expires_at is not None and rule.expires_at <= now:
        return False
    return True


def stored(db: Session, user_id: str, policy: catalogue.ToolPolicy, ctx, now: datetime | None = None) -> list[Rule]:
    """Stored rules in play for this call, oldest first."""
    now = now or utcnow()
    rows = db.scalars(
        select(PermissionRule)
        .where(
            PermissionRule.user_id == user_id,
            PermissionRule.revoked_at.is_(None),
            PermissionRule.system.in_(("*", policy.system)),
            or_(PermissionRule.tool.is_(None), PermissionRule.tool == policy.name),
        )
        .order_by(PermissionRule.created_at, PermissionRule.id)
    ).all()
    scope = scope_of(ctx)
    return [r for r in map(_to_rule, rows) if in_play(r, policy, scope, now)]


def active(db: Session, user: User, policy: catalogue.ToolPolicy, ctx, dialect: str = shellparse.POSIX) -> list[Rule]:
    """The user's stored rules in play for this call (the `dialect` argument is kept for callers)."""
    return stored(db, user.id, policy, ctx)


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def add(
    db: Session,
    user: User,
    *,
    effect: str,
    system: str,
    tool: str | None = None,
    target_kind: str = "",
    pattern: str = "",
    applies_to: str = "",
    source: str = "user",
    expires_at: datetime | None = None,
    note: str = "",
    actor: str = "user",
    dialect: str = shellparse.POSIX,
) -> PermissionRule:
    """Validate, normalise and store one rule; writes a `rule_added` event.

    Raises match.PatternError (the API answers 422) for anything that cannot be stored.
    """
    if effect not in EFFECTS:
        raise match.PatternError(f"effect must be one of {', '.join(EFFECTS)}")
    if source not in SOURCES:
        raise match.PatternError("unknown rule source")
    if tool:
        policy = catalogue.policy_for(tool)
        if policy is None or policy.loop_internal:
            raise match.PatternError(f"unknown tool {tool!r}")
        if system not in ("*", policy.system):
            raise match.PatternError(f"{tool} belongs to {policy.system}, not {system}")
        if target_kind and target_kind not in policy.rule_target_kinds:
            raise match.PatternError(f"{tool} has no {target_kind} to limit a rule to")
    elif system != "*" and not any(p.system == system for p in catalogue.CATALOGUE.values()):
        raise match.PatternError(f"unknown system {system!r}")
    if applies_to and not (applies_to.startswith("schedule:") or applies_to.startswith("automation:")):
        raise match.PatternError("applies_to must be empty, schedule:<id> or automation:<id>")
    norm = match.normalise_pattern(target_kind, pattern, effect, dialect)
    row = PermissionRule(
        user_id=user.id, effect=effect, system=system, tool=tool or None,
        target_kind=target_kind, pattern=norm, applies_to=applies_to, source=source,
        expires_at=expires_at, note=note[:300],
    )
    db.add(row)
    db.flush()
    events.append(
        db, user.id, "rule_added", actor=actor, tool=tool or "", target=f"{target_kind}:{norm}"[:500] if norm else "",
        outcome=effect, detail={"rule_id": row.id, "system": system, "source": source, "applies_to": applies_to,
                                "expires_at": expires_at.isoformat() if expires_at else None},
    )
    return row


def revoke(db: Session, user: User, rule_id: str, *, actor: str = "user") -> bool:
    row = db.get(PermissionRule, rule_id)
    if row is None or row.user_id != user.id or row.revoked_at is not None:
        return False
    row.revoked_at = utcnow()
    db.flush()
    events.append(db, user.id, "rule_revoked", actor=actor, tool=row.tool or "", outcome=row.effect,
                  detail={"rule_id": row.id})
    return True
