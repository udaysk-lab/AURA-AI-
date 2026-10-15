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
    # Device: a native notification is harmless and self-contained.
    "desktop_notify",
}

# Visible outside AURA, but recoverable.
EXTERNAL_WRITE = {
    "create_event",
    # Device actions that change the machine's state but are recoverable or
    # visible rather than destructive: writing/editing files, moving things,
    # opening/closing apps, driving the screen, the clipboard. These sit at
    # "external" so conservative trust asks first and relaxed trust acts.
    "write_file",
    "edit_file",
    "move_file",
    "make_directory",
    "open_app",
    "close_app",
    "set_clipboard",
    "scroll_screen",
    # A request to a server AURA doesn't control is something the outside
    # world sees — and the shortest way to carry what it just read out of the
    # machine (https://somewhere/?data=...). Reading a file runs at any tier;
    # sending anywhere asks below relaxed.
    "fetch_url",
}

# Irreversible or seen by other people.
IRREVERSIBLE = {
    "send_email",
    "archive_email",
    "delete_event",
    "delete_task",
    # Device: running arbitrary code and anything with no undo. These stay at
    # the top class, so only full trust (or a standing grant) runs them without
    # asking — and shell additionally passes the exec denylist floor.
    "run_command",
    "run_in_background",
    "stop_process",
    "kill_process",
    "delete_file",
    # Driving the keyboard and mouse can do anything a person at the desk can —
    # including typing a command into a terminal. Below full trust it asks,
    # like the shell does; otherwise clicking would be a way round the shell's
    # approval card. Typing into a running command is the shell too.
    "mouse_click",
    "type_text",
    "press_keys",
    "send_process_input",
}

# Device read-only tools: observing the machine, never changing it. Folded into
# READ_ONLY below so they run at every tier like any other read.
DEVICE_READ_ONLY = {
    "read_file",
    "list_directory",
    "search_files",
    "list_applications",
    "system_info",
    "list_processes",
    "list_background_commands",
    "process_output",
    "read_clipboard",
    "take_screenshot",
}
READ_ONLY = READ_ONLY | DEVICE_READ_ONLY

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


# Tools whose standing permission covers one target, not the tool as a whole.
# "Always allow send_email" used to mean any recipient at all — including an
# address planted in an email the assistant had just read. Now "always" means
# "always, to this person", and a new recipient asks again.
TARGETED = {"send_email": "to"}


def grant_target(tool_name: str, args: dict | None) -> str:
    """What a grant for this call would cover: a normalised recipient, or ''."""
    field = TARGETED.get(tool_name)
    if not field:
        return ""
    from email.utils import parseaddr

    raw = str((args or {}).get(field) or "")
    address = parseaddr(raw)[1] or raw
    return address.strip().lower()[:320]


def active_grant(
    db: Session, user_id: str, tool_name: str, target: str = ""
) -> ApprovalGrant | None:
    now = datetime.now(timezone.utc)
    grants = db.scalars(
        select(ApprovalGrant).where(
            ApprovalGrant.user_id == user_id,
            ApprovalGrant.tool_name == tool_name,
            ApprovalGrant.revoked == False,  # noqa: E712
        )
    ).all()
    targeted = tool_name in TARGETED
    for grant in grants:
        if targeted and (not target or (grant.target or "") != target):
            # Includes grants made before targets existed: they said "any
            # recipient", which is exactly what no longer holds.
            continue
        if grant.scope == "always":
            return grant
        expires = grant.expires_at
        if expires and (expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)) > now:
            return grant
    return None


SHELL_TOOLS = {"run_command", "run_in_background"}


def _shell_policy_decides(tool_name: str, args: dict | None) -> bool:
    """DEVICE_EXEC_MODE's say on one shell command: True when it skips approval.

    `allowlist` lets listed commands run without asking at any tier; `full`
    lets every command through. A command on the denylist skips the card too —
    not to run, but because the shell refuses it outright, and asking you to
    approve something that can't happen would only teach you to click Allow.
    """
    if tool_name not in SHELL_TOOLS:
        return False
    from app.config import settings

    if not settings.device_control_enabled:
        return False
    from app.device import policy

    verdict = policy.classify(str((args or {}).get("command") or ""))
    return verdict.auto or not verdict.allowed


def requires_approval(
    db: Session, user_id: str, tool_name: str, tier: str, args: dict | None = None
) -> bool:
    tier = normalise_tier(tier)
    if risk_class(tool_name) in _TIER_CEILING[tier]:
        return False
    if _shell_policy_decides(tool_name, args):
        return False
    return active_grant(db, user_id, tool_name, grant_target(tool_name, args)) is None


def grant(
    db: Session,
    user_id: str,
    tool_name: str,
    scope: str,
    minutes: int = 10,
    target: str = "",
) -> ApprovalGrant | None:
    """scope: 'always' | 'window'. None when there's nothing to grant —
    a targeted tool called without a target can only ever be allowed once."""
    if tool_name in TARGETED and not target:
        return None
    row = ApprovalGrant(
        user_id=user_id,
        tool_name=tool_name,
        scope="always" if scope == "always" else "window",
        target=target,
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


def revoke(db: Session, user_id: str, tool_name: str, target: str | None = None) -> int:
    """Revoke a tool's grants — all of them, or only the one for `target`."""
    query = select(ApprovalGrant).where(
        ApprovalGrant.user_id == user_id,
        ApprovalGrant.tool_name == tool_name,
        ApprovalGrant.revoked == False,  # noqa: E712
    )
    if target is not None:
        query = query.where(ApprovalGrant.target == target)
    rows = db.scalars(query).all()
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
