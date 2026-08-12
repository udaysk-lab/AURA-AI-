"""Graduated trust.

Four tiers instead of a yes/no switch, because "can it act on its own" isn't one
question — reading your calendar and emailing your investor are not the same
risk, and users shouldn't have to pick one setting for both.

    strict        ask before anything that changes state
    conservative  handle internal work alone; ask before anything the world sees
    relaxed       act freely; ask only before irreversible external actions
    full          complete autonomy

On top of the tier, the user can grant standing permission for a specific tool
("Always allow") or a time-boxed one ("Allow for 10 minutes"). A grant wins over
the tier, which is what makes the confirmation prompt feel like progress rather
than a toll booth.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApprovalGrant

TIERS = ("strict", "conservative", "relaxed", "full")
TIER_RANK = {name: i for i, name in enumerate(TIERS)}

TIER_LABELS = {
    "strict": "Strict — ask before every action",
    "conservative": "Conservative — handles the safe stuff alone",
    "relaxed": "Relaxed — only checks in for the big decisions",
    "full": "Full access — complete autonomy",
}

# Risk classes. A tool not listed anywhere is treated as SAFE_WRITE.
READ_ONLY = {
    "summarize_inbox",
    "search_emails",
    "get_email",
    "list_events",
    "find_free_time",
    "detect_conflicts",
    "prepare_meeting",
    "list_tasks",
    "search_memory",
    "list_contacts",
    "get_daily_briefing",
    "sync_google",
    "draft_reply",  # writes nothing — the draft is not sent
    "web_research",
    "brief_subject",
    "search_documents",
    "summarize_document",
    "compare_documents",
    "list_delegations",
    "check_focus_blocks",
}

# Changes only AURA's own data.
SAFE_WRITE = {
    "create_task",
    "complete_task",
    "save_memory",
    "notify",
    "delegate_task",
    "chase_delegation",  # drafts the nudge; sending is a separate, gated step
}

# Visible outside AURA, but recoverable.
EXTERNAL_WRITE = {"create_event"}

# Irreversible or seen by other people.
IRREVERSIBLE = {"send_email", "archive_email", "delete_event", "delete_task"}

# The highest risk class each tier will run without asking.
_TIER_CEILING = {
    "strict": {"read"},
    "conservative": {"read", "safe"},
    "relaxed": {"read", "safe", "external"},
    "full": {"read", "safe", "external", "irreversible"},
}


def risk_class(tool_name: str) -> str:
    if tool_name in READ_ONLY:
        return "read"
    if tool_name in IRREVERSIBLE:
        return "irreversible"
    if tool_name in EXTERNAL_WRITE:
        return "external"
    return "safe"


def normalise_tier(value: str | None) -> str:
    """Accept legacy values so existing rows keep working."""
    if value in TIERS:
        return value
    return {"ask": "strict", "trusted": "relaxed"}.get(value or "", "conservative")


def active_grant(db: Session, user_id: str, tool_name: str) -> ApprovalGrant | None:
    now = datetime.now(timezone.utc)
    grants = db.scalars(
        select(ApprovalGrant).where(
            ApprovalGrant.user_id == user_id,
            ApprovalGrant.tool_name == tool_name,
            ApprovalGrant.revoked == False,  # noqa: E712
        )
    ).all()
    for grant in grants:
        if grant.scope == "always":
            return grant
        expires = grant.expires_at
        if expires and (expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)) > now:
            return grant
    return None


def requires_approval(db: Session, user_id: str, tool_name: str, tier: str) -> bool:
    tier = normalise_tier(tier)
    if risk_class(tool_name) in _TIER_CEILING[tier]:
        return False
    return active_grant(db, user_id, tool_name) is None


def grant(
    db: Session, user_id: str, tool_name: str, scope: str, minutes: int = 10
) -> ApprovalGrant:
    """scope: 'always' | 'window'"""
    row = ApprovalGrant(
        user_id=user_id,
        tool_name=tool_name,
        scope="always" if scope == "always" else "window",
        expires_at=(
            None
            if scope == "always"
            else datetime.now(timezone.utc) + timedelta(minutes=minutes)
        ),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def revoke(db: Session, user_id: str, tool_name: str) -> int:
    rows = db.scalars(
        select(ApprovalGrant).where(
            ApprovalGrant.user_id == user_id,
            ApprovalGrant.tool_name == tool_name,
            ApprovalGrant.revoked == False,  # noqa: E712
        )
    ).all()
    for row in rows:
        row.revoked = True
    db.commit()
    return len(rows)


def list_grants(db: Session, user_id: str) -> list[ApprovalGrant]:
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(ApprovalGrant).where(
            ApprovalGrant.user_id == user_id,
            ApprovalGrant.revoked == False,  # noqa: E712
        )
    ).all()
    live = []
    for row in rows:
        if row.scope == "always":
            live.append(row)
        elif row.expires_at:
            expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(
                tzinfo=timezone.utc
            )
            if expires > now:
                live.append(row)
    return live


def heartbeat_allows(tier: str, skill_floor: str) -> bool:
    """Can the background loop run a skill with this floor at this tier?"""
    return TIER_RANK[normalise_tier(tier)] >= TIER_RANK.get(skill_floor, 1)
