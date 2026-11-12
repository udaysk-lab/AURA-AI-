"""Read-time stand-in for the legacy mapping (spec 10 section 12), until WP11.

The data migration `pm02` will turn each user's 1.0 settings into a permission
mode and stored rules. Until it has run for a user (marker
`user_settings.extra["permissions_migrated"]`), this module derives the same
result at read time and writes nothing, so that adopting the gate changes no
existing user's behaviour:

  strict        -> mode ask + ask rules on the AURA-internal writes
  conservative  -> mode ask
  relaxed       -> mode ask + allow rules for the tools relaxed ran alone
  full          -> mode skip (+ allow everything on schedules, as full ran them)
  ApprovalGrant -> allow rules (send_email: that recipient only)
  DEVICE_EXEC_MODE/ALLOWLIST -> allow rules on the shell tools
  automation    -> allow rules for the safe/external actions the old engine ran alone
  DEVICE_COMMAND_DENYLIST -> not mapped here: the denylist floor already refuses those

Nobody is mapped to `auto`. WP11 deletes this module together with `autonomy.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ApprovalGrant, AutomationRule, User, UserSettings
from app.permissions import catalogue, match, shellparse
from app.permissions.rules import Rule

STRICT_ASK = (
    "create_task", "complete_task", "save_memory", "notify", "delegate_task",
    "chase_delegation", "desktop_notify",
)
RELAXED_ALLOW = (
    "create_event", "write_file", "edit_file", "move_file", "make_directory", "open_app",
    "close_app", "set_clipboard", "scroll_screen", "fetch_url",
)
SHELL_TOOLS = ("run_command", "run_in_background")


def _settings_row(db: Session, user: User) -> UserSettings | None:
    return user.settings or db.scalars(select(UserSettings).where(UserSettings.user_id == user.id)).first()


def migrated(row: UserSettings | None) -> bool:
    return bool(row and (row.extra or {}).get("permissions_migrated"))


def tier_of(db: Session, user: User, ctx=None) -> str:
    """The 1.0 tier in force: the one the caller passed (a loop run), else the stored level."""
    from app.agent.autonomy import normalise_tier

    forced = getattr(ctx, "legacy_tier", None)
    if forced:
        return normalise_tier(forced)
    row = _settings_row(db, user)
    return normalise_tier(row.autonomy_level if row else None)


def stored_mode(db: Session, user: User, ctx=None) -> str:
    """The user's permission mode: the stored column once migrated, else derived from the tier."""
    row = _settings_row(db, user)
    if migrated(row):
        mode = row.permission_mode if row else "ask"
        return mode if mode in catalogue.MODES else "ask"
    return "skip" if tier_of(db, user, ctx) == "full" else "ask"


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def rules(db: Session, user: User, policy: catalogue.ToolPolicy, ctx, dialect: str = shellparse.POSIX) -> list[Rule]:
    row = _settings_row(db, user)
    if migrated(row):
        return []
    name, system = policy.name, policy.system
    out: list[Rule] = []

    def rule(effect: str, tag: str, **kw) -> None:
        out.append(Rule(id=f"legacy:{tag}:{name}", effect=effect, system=system, tool=name, source="legacy", **kw))

    tier = tier_of(db, user, ctx)
    if tier == "strict" and name in STRICT_ASK:
        rule("ask", "strict")
    if tier == "relaxed" and name in RELAXED_ALLOW:
        rule("allow", "relaxed")
    if tier == "full" and ctx.trigger == "schedule":
        rule("allow", "full-schedule")

    # Standing grants made with "always allow" / "allow for 10 minutes".
    if name != "delete_file":
        now = datetime.now(timezone.utc)
        for g in db.scalars(
            select(ApprovalGrant).where(
                ApprovalGrant.user_id == user.id, ApprovalGrant.tool_name == name,
                ApprovalGrant.revoked == False,  # noqa: E712
            )
        ).all():
            expires = _aware(g.expires_at)
            if g.scope != "always" and not (expires and expires > now):
                continue
            kw: dict = {"expires_at": expires if g.scope != "always" else None}
            if name == "send_email":
                if not g.target:
                    continue
                try:
                    kw.update(target_kind="recipient", pattern=match.normalise_recipient_pattern(g.target))
                except match.PatternError:
                    continue
            out.append(Rule(id=f"legacy:grant:{g.id}", effect="allow", system=system, tool=name,
                            source="legacy", **kw))

    # DEVICE_EXEC_MODE / DEVICE_EXEC_ALLOWLIST for the shell tools.
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

    # Automations ran their safe and external actions alone, whatever the tier.
    if ctx.trigger == "automation" and ctx.origin_id and catalogue.default_disposition(policy.kind, "ask") != "allow":
        from app.agent.autonomy import risk_class

        if risk_class(name) in ("safe", "external"):
            auto = db.get(AutomationRule, ctx.origin_id)
            if auto and auto.user_id == user.id and any(
                (a or {}).get("tool") == name for a in (auto.actions or [])
            ):
                out.append(Rule(id=f"legacy:automation:{auto.id}:{name}", effect="allow", system=system,
                                tool=name, applies_to=f"automation:{auto.id}", source="legacy"))
    return out
