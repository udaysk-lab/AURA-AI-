"""Usage, spend, and a user's rights over their own data.

Export and hard delete aren't compliance theatre — an assistant that has read
your inbox and stored what it learned is exactly the kind of system where
"can I get my data out" and "can I make it forget me" have to be real buttons
that work, not a support email.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.config import settings
from app.deps import CurrentUser, DbSession
from app.models import (
    ActivityLog,
    Assistant,
    AutomationRule,
    CalendarEvent,
    Channel,
    Contact,
    Conversation,
    Delegation,
    Document,
    EmailMessage,
    HeartbeatReport,
    Memory,
    Message,
    Notification,
    Schedule,
    SkillRun,
    Task,
    UsageRecord,
    UserSkill,
    VaultSecret,
)
from app.schemas import AccountDeleteIn, SpendOut, UsageOut
from app.services import usage as usage_service

log = logging.getLogger("aura.account")
router = APIRouter(prefix="/api/account", tags=["account"])


# ---------------------------------------------------------------------------
# Spend
# ---------------------------------------------------------------------------


def _spend_out(state: usage_service.SpendState) -> SpendOut:
    provider = settings.resolved_provider()
    model = {
        "anthropic": settings.anthropic_model,
        "openai": settings.openai_model,
    }.get(provider, "offline mock")
    return SpendOut(
        **state.as_dict(),
        cap_enabled=settings.spend_cap_enabled,
        provider=provider,
        model=model,
    )


@router.get("/spend", response_model=SpendOut)
def spend(user: CurrentUser, db: DbSession) -> SpendOut:
    return _spend_out(usage_service.spend_today(db, user.id))


@router.get("/usage", response_model=UsageOut)
def usage(
    user: CurrentUser, db: DbSession, days: int = Query(14, ge=1, le=90)
) -> UsageOut:
    return UsageOut(
        today=_spend_out(usage_service.spend_today(db, user.id)),
        daily=usage_service.history(db, user.id, days=days),  # type: ignore[arg-type]
        by_trigger=usage_service.by_trigger(db, user.id, days=7),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

# (attribute name on the row, model, user-facing label)
EXPORTABLE = [
    ("memories", Memory),
    ("tasks", Task),
    ("events", CalendarEvent),
    ("emails", EmailMessage),
    ("contacts", Contact),
    ("documents", Document),
    ("conversations", Conversation),
    ("skill_runs", SkillRun),
    ("user_skills", UserSkill),
    ("automations", AutomationRule),
    ("schedules", Schedule),
    ("delegations", Delegation),
    ("notifications", Notification),
    ("heartbeats", HeartbeatReport),
    ("activity", ActivityLog),
    ("usage", UsageRecord),
    ("channels", Channel),
]

# Columns that must never appear in an export, even though they belong to the user.
REDACTED_COLUMNS = {"value_enc", "access_token_enc", "refresh_token_enc", "token", "embedding"}


def _row_to_dict(row) -> dict:
    out = {}
    for column in row.__table__.columns:
        if column.name in REDACTED_COLUMNS:
            continue
        value = getattr(row, column.name)
        out[column.name] = value.isoformat() if hasattr(value, "isoformat") else value
    return out


@router.get("/export")
def export_account(user: CurrentUser, db: DbSession) -> JSONResponse:
    """Everything AURA holds about you, as JSON.

    Encrypted secrets and OAuth tokens are listed by name but never by value —
    exporting a decrypted credential would turn a data-rights feature into an
    exfiltration route.
    """
    payload: dict = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user": _row_to_dict(user),
    }

    assistant = db.scalars(select(Assistant).where(Assistant.user_id == user.id)).first()
    payload["assistant"] = _row_to_dict(assistant) if assistant else None

    for label, model in EXPORTABLE:
        rows = db.scalars(select(model).where(model.user_id == user.id)).all()
        payload[label] = [_row_to_dict(r) for r in rows]

    # Messages hang off conversations rather than the user directly.
    conversation_ids = [c["id"] for c in payload.get("conversations", [])]
    if conversation_ids:
        messages = db.scalars(
            select(Message).where(Message.conversation_id.in_(conversation_ids))
        ).all()
        payload["messages"] = [_row_to_dict(m) for m in messages]

    secrets = db.scalars(select(VaultSecret).where(VaultSecret.user_id == user.id)).all()
    payload["vault_keys"] = [
        {"key": s.key, "label": s.label, "kind": s.kind, "hint": s.hint} for s in secrets
    ]
    payload["_note"] = (
        "Vault values and OAuth tokens are intentionally excluded. Only key names "
        "and masked hints are exported."
    )

    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": f'attachment; filename="aura-export-{user.id[:8]}.json"'
        },
    )


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.post("/delete")
def delete_account(payload: AccountDeleteIn, user: CurrentUser, db: DbSession) -> dict:
    """Hard delete. Everything cascades from the user row.

    Two guards, because there is no undo: the typed email must match, and the
    user must explicitly acknowledge. A single confirm dialog is not enough
    friction for an irreversible action.
    """
    if payload.confirm_email.strip().lower() != user.email.lower():
        raise HTTPException(
            status_code=400,
            detail="The email you typed doesn't match this account.",
        )
    if not payload.understand:
        raise HTTPException(
            status_code=400,
            detail="Confirm you understand this cannot be undone.",
        )

    email = user.email
    db.delete(user)  # every table cascades on user_id
    db.commit()
    log.warning("Account deleted: %s", email)
    return {
        "deleted": True,
        "message": (
            f"{email} and everything associated with it has been removed. "
            "Model providers may retain their own request logs under their policies."
        ),
    }
