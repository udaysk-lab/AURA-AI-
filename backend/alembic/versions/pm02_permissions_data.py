"""Permissions, data step: map every user's 1.0 trust settings onto the new rows.

Revision ID: pm02_permissions_data
Revises: pm01_permissions
Create Date: 2026-11-12

Writes, for each existing user, what the 1.0 settings meant in the 2.0 permission model
(spec 10-permissions.md section 12, decision 14), so nobody's behaviour changes:

  strict        -> mode ask, ask rules on AURA's own writes
  conservative  -> mode ask
  relaxed       -> mode ask, allow rules for the tools "relaxed" ran alone
  full          -> mode skip, plus allow rules scoped to each existing schedule
  every tier    -> allow rules scoped to each automation, for the actions the old engine ran alone
  approval grants (always / unexpired window) -> allow rules (send_email: that recipient only)
  DEVICE_EXEC_MODE / ALLOWLIST / COMMAND_DENYLIST -> rules on the two shell tools
  DEVICE_ALLOWED_ROOTS -> connected folders (device control on only)
  pending approvals -> given an expiry (24 h after they were made) or expired
  app blocklist -> seeded (device control on only)

Nobody is put on `auto`. Every row written has source 'legacy'. The change is recorded for the
user in `user_settings.extra`: `permissions_migrated` (also what makes a re-run skip the user)
and `legacy_events_pending`, which the app turns into a `legacy_mapped` event in the user's
tamper-evident log the first time it looks (the log is signed with the server secret, which a
revision must not read).

Idempotent: a user already marked is skipped, and every rule and folder is inserted only if absent
(so a rule the user later revoked is never brought back). Old columns and tables stay (pm90 drops
them). Per spec 30 section 6.4 the mapping tables below are frozen literals and nothing from the
app is imported but `app.migration_ops`.
"""

import hashlib
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.migration_ops import install_env

revision: str = "pm02_permissions_data"
down_revision: Union[str, None] = "pm01_permissions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

# --- frozen mapping tables ---------------------------------------------------------------------

STRICT_ASK = (
    "create_task", "complete_task", "save_memory", "notify", "delegate_task", "chase_delegation", "desktop_notify",
)
RELAXED_ALLOW = (
    "create_event", "write_file", "edit_file", "move_file", "make_directory", "open_app", "close_app",
    "set_clipboard", "scroll_screen", "fetch_url",
)
# Tools the 1.0 automation engine ran without asking at any tier although the new defaults ask
# (old class safe or external, and not allowed by default). The same ten as RELAXED_ALLOW.
AUTOMATION_RAN_ALONE = frozenset(RELAXED_ALLOW)
SHELL_TOOLS = ("run_command", "run_in_background")
CORE_SYSTEMS = ("core-calendar", "core-inbox", "core-memory", "core-planning", "core-tasks")
DEVICE_SYSTEM = "device-control"
# A grant for this tool is not carried over: deleting files now always needs the user.
NOT_CARRIED = frozenset({"delete_file"})

TOOL_SYSTEM = {
    "archive_email": "core-inbox", "brief_subject": "research", "chase_delegation": "delegation",
    "check_focus_blocks": "focus", "close_app": "device-control", "compare_documents": "documents",
    "complete_task": "core-tasks", "create_event": "core-calendar", "create_task": "core-tasks",
    "delegate_task": "delegation", "delete_event": "core-calendar", "delete_file": "device-control",
    "delete_task": "core-tasks", "desktop_notify": "device-control", "detect_conflicts": "core-calendar",
    "draft_reply": "core-inbox", "edit_file": "device-control", "fetch_url": "device-control",
    "find_free_time": "core-calendar", "get_daily_briefing": "core-planning", "get_email": "core-inbox",
    "kill_process": "device-control", "list_applications": "device-control",
    "list_background_commands": "device-control", "list_contacts": "core-memory",
    "list_delegations": "delegation", "list_directory": "device-control", "list_events": "core-calendar",
    "list_processes": "device-control", "list_tasks": "core-tasks", "make_directory": "device-control",
    "mouse_click": "device-control", "move_file": "device-control", "notify": "core-planning",
    "open_app": "device-control", "prepare_meeting": "core-calendar", "press_keys": "device-control",
    "process_output": "device-control", "read_clipboard": "device-control", "read_file": "device-control",
    "run_command": "device-control", "run_in_background": "device-control", "save_memory": "core-memory",
    "scroll_screen": "device-control", "search_documents": "documents", "search_emails": "core-inbox",
    "search_files": "device-control", "search_memory": "core-memory", "send_email": "core-inbox",
    "send_process_input": "device-control", "set_clipboard": "device-control",
    "stop_process": "device-control", "summarize_document": "documents", "summarize_inbox": "core-inbox",
    "sync_google": "core-planning", "system_info": "device-control", "take_screenshot": "device-control",
    "type_text": "device-control", "web_research": "research", "write_file": "device-control",
}

# The default app blocklist (spec 10.3), seeded for installs with device control.
DEFAULT_BLOCKS = tuple(
    [(k, "keyword") for k in (
        "bank", "wallet", "trading", "trade", "crypto", "coinbase", "binance", "kraken", "robinhood",
        "metatrader", "thinkorswim", "tradingview", "zerodha", "kite", "groww", "upstox", "angel-one",
        "paytm", "phonepe", "metamask", "ledger-live", "trezor", "exodus", "electrum", "1password",
        "bitwarden", "lastpass", "dashlane", "keepass", "keepassxc", "enpass", "nordpass", "proton-pass",
    )]
    + [("keychain-access", "app"), ("passwords", "app")]
)

PENDING_LIFETIME = timedelta(hours=24)
ALL_OPTIONS = ["once", "window", "always", "reject"]

# --- table stubs (SQL Core; the models are not imported) -----------------------------------------

_md = sa.MetaData()
users = sa.Table("users", _md, sa.Column("id", sa.String(36)))
user_settings = sa.Table(
    "user_settings", _md,
    sa.Column("id", sa.String(36)), sa.Column("user_id", sa.String(36)),
    sa.Column("autonomy_level", sa.String(20)), sa.Column("extra", sa.JSON()),
    sa.Column("permission_mode", sa.String(8)), sa.Column("updated_at", sa.DateTime(timezone=True)),
)
approval_grants = sa.Table(
    "approval_grants", _md,
    sa.Column("id", sa.String(36)), sa.Column("user_id", sa.String(36)), sa.Column("tool_name", sa.String(120)),
    sa.Column("scope", sa.String(20)), sa.Column("expires_at", sa.DateTime(timezone=True)),
    sa.Column("revoked", sa.Boolean()), sa.Column("target", sa.String(320)),
)
schedules = sa.Table("schedules", _md, sa.Column("id", sa.String(36)), sa.Column("user_id", sa.String(36)))
automation_rules = sa.Table(
    "automation_rules", _md,
    sa.Column("id", sa.String(36)), sa.Column("user_id", sa.String(36)), sa.Column("actions", sa.JSON()),
)
installed_plugins = sa.Table(
    "installed_plugins", _md,
    sa.Column("user_id", sa.String(36)), sa.Column("plugin_id", sa.String(60)), sa.Column("enabled", sa.Boolean()),
)
permission_rules = sa.Table(
    "permission_rules", _md,
    sa.Column("id", sa.String(36)), sa.Column("user_id", sa.String(36)), sa.Column("effect", sa.String(8)),
    sa.Column("system", sa.String(60)), sa.Column("tool", sa.String(120)), sa.Column("target_kind", sa.String(12)),
    sa.Column("pattern", sa.String(1024)), sa.Column("applies_to", sa.String(80)), sa.Column("source", sa.String(12)),
    sa.Column("expires_at", sa.DateTime(timezone=True)), sa.Column("revoked_at", sa.DateTime(timezone=True)),
    sa.Column("note", sa.String(300)),
    sa.Column("created_at", sa.DateTime(timezone=True)), sa.Column("updated_at", sa.DateTime(timezone=True)),
)
device_folders = sa.Table(
    "device_folders", _md,
    sa.Column("id", sa.String(36)), sa.Column("user_id", sa.String(36)), sa.Column("path", sa.Text()),
    sa.Column("path_key", sa.String(64)), sa.Column("can_delete", sa.Boolean()), sa.Column("source", sa.String(12)),
    sa.Column("revoked_at", sa.DateTime(timezone=True)),
    sa.Column("created_at", sa.DateTime(timezone=True)), sa.Column("updated_at", sa.DateTime(timezone=True)),
)
device_app_blocks = sa.Table(
    "device_app_blocks", _md,
    sa.Column("id", sa.String(36)), sa.Column("user_id", sa.String(36)), sa.Column("app_id", sa.String(120)),
    sa.Column("match", sa.String(8)), sa.Column("source", sa.String(12)),
    sa.Column("created_at", sa.DateTime(timezone=True)), sa.Column("updated_at", sa.DateTime(timezone=True)),
)
pending_actions = sa.Table(
    "pending_actions", _md,
    sa.Column("id", sa.String(36)), sa.Column("user_id", sa.String(36)), sa.Column("conversation_id", sa.String(36)),
    sa.Column("status", sa.String(20)), sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)), sa.Column("resolved_at", sa.DateTime(timezone=True)),
    sa.Column("expires_at", sa.DateTime(timezone=True)), sa.Column("origin", sa.String(20)),
    sa.Column("origin_id", sa.String(36)), sa.Column("options", sa.JSON()),
)


# --- helpers ---------------------------------------------------------------------------------------


def _aware(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _has(bind, name):
    return sa.inspect(bind).has_table(name)


def normalise_tier(value):
    """strict | conservative | relaxed | full; the older spellings `ask` and `trusted` still count."""
    if value in ("strict", "conservative", "relaxed", "full"):
        return value
    return {"ask": "strict", "trusted": "relaxed"}.get(value or "", "conservative")


def path_key(real_path):
    """sha256 of the folded realpath: the key permissions/access.py uses for a connected folder."""
    ci = os.name == "nt" or sys.platform == "darwin"
    folded = os.path.normcase(real_path).casefold() if ci else real_path
    return hashlib.sha256(folded.encode("utf-8")).hexdigest()


def _recipient(raw):
    """The normalised address of a grant's target, or None when it is not one."""
    text = (raw or "").strip()
    local, sep, domain = text.rpartition("@")
    if not sep or not local or not domain or any(c.isspace() for c in text) or "," in text:
        return None
    try:
        host = domain.strip().strip(".").lower().encode("idna").decode("ascii")
    except UnicodeError:
        return None
    return f"{local.strip().lower()}@{host}"


class _Plan:
    """The rows one user gets, with the reasons for the user's record."""

    def __init__(self):
        self.rules = []
        self.dropped = []

    def rule(self, effect, tool=None, *, system=None, target_kind="", pattern="", applies_to="", expires_at=None):
        system = system or TOOL_SYSTEM.get(tool or "")
        if not system:
            return
        self.rules.append({
            "effect": effect, "system": system, "tool": tool, "target_kind": target_kind, "pattern": pattern,
            "applies_to": applies_to, "expires_at": expires_at,
        })


def _plan_for(bind, user_id, tier, env, now):
    plan = _Plan()
    if tier == "strict":
        for tool in STRICT_ASK:
            plan.rule("ask", tool)
    if tier == "relaxed":
        for tool in RELAXED_ALLOW:
            plan.rule("allow", tool)
    if tier == "full":
        assigned = set(CORE_SYSTEMS)
        if env["device_control"]:
            assigned.add(DEVICE_SYSTEM)
        if _has(bind, "installed_plugins"):
            assigned |= {
                r.plugin_id for r in bind.execute(
                    sa.select(installed_plugins.c.plugin_id).where(
                        installed_plugins.c.user_id == user_id, installed_plugins.c.enabled.is_(True))
                )
            }
        known = set(TOOL_SYSTEM.values())
        for sid in bind.execute(sa.select(schedules.c.id).where(schedules.c.user_id == user_id)).scalars():
            for system in sorted(assigned & known):
                plan.rule("allow", None, system=system, applies_to=f"schedule:{sid}")

    for rid, actions in bind.execute(
        sa.select(automation_rules.c.id, automation_rules.c.actions).where(automation_rules.c.user_id == user_id)
    ):
        tools = {(a or {}).get("tool") for a in (actions or []) if isinstance(a, dict)}
        for tool in sorted(t for t in tools if t in AUTOMATION_RAN_ALONE):
            plan.rule("allow", tool, applies_to=f"automation:{rid}")

    for g in bind.execute(sa.select(approval_grants).where(
        approval_grants.c.user_id == user_id, approval_grants.c.revoked.is_(False))
    ).mappings():
        tool = g["tool_name"]
        expires = _aware(g["expires_at"])
        if g["scope"] != "always" and not (expires and expires > now):
            continue
        if tool in NOT_CARRIED:
            plan.dropped.append(tool)
            continue
        kw = {"expires_at": g["expires_at"] if g["scope"] != "always" else None}
        if tool == "send_email":
            address = _recipient(g["target"])
            if not address:
                continue
            kw.update(target_kind="recipient", pattern=address)
        plan.rule("allow", tool, **kw)

    if env["device_control"]:
        if env["exec_mode"] == "full":
            for tool in SHELL_TOOLS:
                plan.rule("allow", tool)
        elif env["exec_mode"] == "allowlist":
            for entry in env["exec_allowlist"]:
                for tool in SHELL_TOOLS:
                    plan.rule("allow", tool, target_kind="command", pattern=entry)
        for entry in env["command_denylist"]:
            for tool in SHELL_TOOLS:
                plan.rule("block", tool, target_kind="command", pattern="~" + entry)
    return plan


def _insert_rules(bind, user_id, plan, now):
    have = {
        (r.effect, r.system, r.tool, r.target_kind, r.pattern, r.applies_to)
        for r in bind.execute(sa.select(permission_rules).where(
            permission_rules.c.user_id == user_id, permission_rules.c.source == "legacy"))
    }
    added = 0
    for r in plan.rules:
        key = (r["effect"], r["system"], r["tool"], r["target_kind"], r["pattern"], r["applies_to"])
        if key in have:
            continue
        have.add(key)
        bind.execute(permission_rules.insert().values(
            id=str(uuid.uuid4()), user_id=user_id, source="legacy", revoked_at=None, note="Carried over from AURA 1.0",
            created_at=now, updated_at=now, **r))
        added += 1
    return added


def _insert_folders(bind, user_id, roots, now):
    have = {r for r in bind.execute(sa.select(device_folders.c.path_key).where(
        device_folders.c.user_id == user_id)).scalars()}
    added = 0
    for root in roots:
        key = path_key(root)
        if key in have:
            continue
        have.add(key)
        bind.execute(device_folders.insert().values(
            id=str(uuid.uuid4()), user_id=user_id, path=root, path_key=key, can_delete=True, source="legacy",
            revoked_at=None, created_at=now, updated_at=now))
        added += 1
    return added


def _seed_blocks(bind, user_id, now):
    have = {r for r in bind.execute(sa.select(device_app_blocks.c.app_id).where(
        device_app_blocks.c.user_id == user_id)).scalars()}
    for app_id, how in DEFAULT_BLOCKS:
        if app_id not in have:
            bind.execute(device_app_blocks.insert().values(
                id=str(uuid.uuid4()), user_id=user_id, app_id=app_id, match=how, source="default",
                created_at=now, updated_at=now))


def _settle_pending(bind, user_id, now):
    """Old parked approvals get a lifetime: 24 hours from when they were made, or they have lapsed."""
    expired = 0
    rows = bind.execute(sa.select(
        pending_actions.c.id, pending_actions.c.conversation_id, pending_actions.c.created_at
    ).where(
        pending_actions.c.user_id == user_id, pending_actions.c.status == "pending",
        pending_actions.c.expires_at.is_(None))).all()
    for pid, convo, created in rows:
        due = (_aware(created) or now) + PENDING_LIFETIME
        values = {"expires_at": due, "origin": "chat" if convo else "automation", "options": list(ALL_OPTIONS),
                  "updated_at": now}
        if due <= now:
            values.update(status="expired", resolved_at=now)
            expired += 1
        bind.execute(pending_actions.update().where(pending_actions.c.id == pid).values(**values))
    return len(rows), expired


def map_legacy(bind, env, *, only_user_id=None, now=None):
    """Map the users (all of them, or one); returns {user_id: summary}. Used by upgrade() and by tests."""
    now = now or datetime.now(timezone.utc)
    done = {}
    query = sa.select(users.c.id)
    if only_user_id:
        query = query.where(users.c.id == only_user_id)
    for user_id in bind.execute(query).scalars().all():
        row = bind.execute(sa.select(user_settings).where(user_settings.c.user_id == user_id)).mappings().first()
        extra = dict((row["extra"] if row else None) or {})
        if extra.get("permissions_migrated"):
            continue
        tier = normalise_tier(row["autonomy_level"] if row else None)
        mode = "skip" if tier == "full" else "ask"
        plan = _plan_for(bind, user_id, tier, env, now)
        n_rules = _insert_rules(bind, user_id, plan, now)
        n_folders = _insert_folders(bind, user_id, env["allowed_roots"], now) if env["device_control"] else 0
        if env["device_control"]:
            _seed_blocks(bind, user_id, now)
            extra["app_blocklist_seeded"] = 1
        pending, expired = _settle_pending(bind, user_id, now) if _has(bind, "pending_actions") else (0, 0)
        summary = {
            "tier": tier, "mode": mode, "rules": n_rules, "folders": n_folders,
            "grants_not_carried": sorted(set(plan.dropped)), "pending_given_expiry": pending,
            "pending_expired": expired,
        }
        if row is not None:
            extra["permissions_migrated"] = {"from_tier": tier, "at": now.isoformat()}
            extra["legacy_events_pending"] = [summary]
            bind.execute(user_settings.update().where(user_settings.c.id == row["id"]).values(
                permission_mode=mode, extra=extra, updated_at=now))
        done[user_id] = summary
    return done


def upgrade() -> None:
    done = map_legacy(op.get_bind(), install_env())
    log.info("pm02: mapped the 1.0 permission settings of %d user(s)", len(done))


def downgrade() -> None:
    raise NotImplementedError("irreversible; restore a backup")
