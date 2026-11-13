"""The 1.0 behaviour as it was derived at read time, before the data migration pm02 existed.

This is a frozen copy of what `permissions/interim.py` computed (mode and rules from the 1.0 trust
level, approval grants, DEVICE_EXEC settings and automation actions; connected folders from
DEVICE_ALLOWED_ROOTS), kept ONLY so test_perm_legacy.py can prove that decisions after the mapping
equal decisions before it. Nothing in app/ uses it, and it uses nothing from the retired 1.0 modules:
every table and rule of the 1.0 decision logic it needs is written out below as literals. (It drives
the 2.0 gate on purpose: that is the thing whose before-and-after decisions are compared.)
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.models import ApprovalGrant, AutomationRule, User, UserSettings
from app.permissions import access, catalogue, gate, match, shellparse
from app.permissions import rules as rules_store
from app.permissions.rules import Rule

STRICT_ASK = (
    "create_task", "complete_task", "save_memory", "notify", "delegate_task", "chase_delegation", "desktop_notify",
)
RELAXED_ALLOW = (
    "create_event", "write_file", "edit_file", "move_file", "make_directory", "open_app", "close_app",
    "set_clipboard", "scroll_screen", "fetch_url",
)
SHELL_TOOLS = ("run_command", "run_in_background")

# 1.0.0's risk classes: everything not read-only and not irreversible was "safe" or "external".
READ_ONLY = {
    "summarize_inbox", "search_emails", "get_email", "list_events", "find_free_time", "detect_conflicts",
    "prepare_meeting", "list_tasks", "search_memory", "list_contacts", "get_daily_briefing", "sync_google",
    "draft_reply", "web_research", "brief_subject", "search_documents", "summarize_document",
    "compare_documents", "list_delegations", "check_focus_blocks", "read_file", "list_directory",
    "search_files", "list_applications", "system_info", "list_processes", "list_background_commands",
    "process_output", "read_clipboard", "take_screenshot",
}
IRREVERSIBLE = {
    "send_email", "archive_email", "delete_event", "delete_task", "run_command", "run_in_background",
    "stop_process", "kill_process", "delete_file", "mouse_click", "type_text", "press_keys", "send_process_input",
}


def risk_class(name: str) -> str:
    if name in READ_ONLY:
        return "read"
    if name in IRREVERSIBLE:
        return "irreversible"
    return "external" if name in RELAXED_ALLOW else "safe"


def normalise_tier(value):
    if value in ("strict", "conservative", "relaxed", "full"):
        return value
    return {"ask": "strict", "trusted": "relaxed"}.get(value or "", "conservative")


def _row(db, user):
    return user.settings or db.scalars(select(UserSettings).where(UserSettings.user_id == user.id)).first()


def tier_of(db, user) -> str:
    row = _row(db, user)
    return normalise_tier(row.autonomy_level if row else None)


def stored_mode(db, user) -> str:
    return "skip" if tier_of(db, user) == "full" else "ask"


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def derived_rules(db, user, policy, ctx, dialect=shellparse.POSIX) -> list[Rule]:
    name, system = policy.name, policy.system
    out: list[Rule] = []

    def rule(effect, tag, **kw):
        out.append(Rule(id=f"legacy:{tag}:{name}", effect=effect, system=system, tool=name, source="legacy", **kw))

    tier = tier_of(db, user)
    if tier == "strict" and name in STRICT_ASK:
        rule("ask", "strict")
    if tier == "relaxed" and name in RELAXED_ALLOW:
        rule("allow", "relaxed")
    if tier == "full" and ctx.trigger == "schedule":
        rule("allow", "full-schedule")
    if name != "delete_file":
        now = datetime.now(timezone.utc)
        for g in db.scalars(select(ApprovalGrant).where(
            ApprovalGrant.user_id == user.id, ApprovalGrant.tool_name == name, ApprovalGrant.revoked == False  # noqa: E712
        )).all():
            expires = _aware(g.expires_at)
            if g.scope != "always" and not (expires and expires > now):
                continue
            kw = {"expires_at": expires if g.scope != "always" else None}
            if name == "send_email":
                if not g.target:
                    continue
                try:
                    kw.update(target_kind="recipient", pattern=match.normalise_recipient_pattern(g.target))
                except match.PatternError:
                    continue
            out.append(Rule(id=f"legacy:grant:{g.id}", effect="allow", system=system, tool=name, source="legacy", **kw))
    if name in SHELL_TOOLS:
        if settings.device_exec_mode == "full":
            rule("allow", "exec-full")
        elif settings.device_exec_mode == "allowlist":
            for i, entry in enumerate(settings.device_exec_allow_list):
                try:
                    pat = match.normalise_command_pattern(entry, "allow", dialect)
                except match.PatternError:
                    continue
                out.append(Rule(id=f"legacy:allowlist:{i}", effect="allow", system=system, tool=name,
                                target_kind="command", pattern=pat, source="legacy"))
    if ctx.trigger == "automation" and ctx.origin_id and catalogue.default_disposition(policy.kind, "ask") != "allow":
        if risk_class(name) in ("safe", "external"):
            auto = db.get(AutomationRule, ctx.origin_id)
            if auto and auto.user_id == user.id and any((a or {}).get("tool") == name for a in (auto.actions or [])):
                out.append(Rule(id=f"legacy:automation:{auto.id}:{name}", effect="allow", system=system, tool=name,
                                applies_to=f"automation:{auto.id}", source="legacy"))
    return out


def derived_folders(db, user, rows) -> list:
    have = {r.path_key for r in rows}
    out = []
    for root in settings.device_allowed_root_list:
        key = access.path_key(root)
        if key not in have:
            out.append(access.FolderInfo(id="legacy:" + key[:16], path=root, path_key=key, can_delete=True, source="legacy"))
    return out


@contextlib.contextmanager
def read_time_derivation(monkeypatch):
    """Make the gate behave as it did for a user the migration had not reached."""
    original_snapshot = access.snapshot

    def active(db, user, policy, ctx, dialect=shellparse.POSIX):
        now = rules_store.utcnow()
        out = rules_store.stored(db, user.id, policy, ctx, now)
        out.extend(r for r in derived_rules(db, user, policy, ctx, dialect)
                   if rules_store.in_play(r, policy, rules_store.scope_of(ctx), now))
        return out

    def snapshot(db, user):
        snap = original_snapshot(db, user)
        if snap is access.EMPTY or db is None:
            return snap
        rows = list(snap.folders)
        extra = derived_folders(db, user, rows)
        return access.Access(tuple(rows) + tuple(extra), snap.grants, snap.blocks, snap.ceiling)

    with monkeypatch.context() as m:
        m.setattr(rules_store, "active", active)
        m.setattr(gate, "stored_mode", stored_mode)
        m.setattr(access, "snapshot", snapshot)
        yield
