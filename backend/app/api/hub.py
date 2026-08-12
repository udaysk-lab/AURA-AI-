"""Plugins, channels, vault, documents, schedules and delegations."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select

from app import plugins as plugin_registry
from app.agent import skills as skill_registry
from app.config import settings
from app.deps import CurrentUser, DbSession
from app.models import Delegation, Document, DocumentChunk, Schedule, utcnow
from app.schemas import (
    ChannelConnectIn,
    ChannelOut,
    DelegationIn,
    DelegationOut,
    DocumentDetail,
    DocumentOut,
    DocumentPassage,
    DocumentTextIn,
    InboundIn,
    InboundOut,
    PluginOut,
    PluginSummaryOut,
    ScheduleIn,
    SchedulePatch,
    ScheduleOut,
    SecretIn,
    SecretOut,
)
from app.services import channels as channel_service
from app.services import documents as document_service
from app.services import schedules as schedule_service
from app.services import vault as vault_service

log = logging.getLogger("aura.hub")
router = APIRouter(prefix="/api", tags=["hub"])


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------


def _plugin_out(plugin, installed: set[str]) -> PluginOut:
    return PluginOut(
        id=plugin.id,
        name=plugin.name,
        category=plugin.category,
        summary=plugin.summary,
        detail=plugin.detail,
        skills=list(plugin.skills),
        skill_names=[
            skill_registry.CATALOG[c].name
            for c in plugin.skills
            if c in skill_registry.CATALOG
        ],
        core=plugin.core,
        available=plugin.available,
        unavailable_reason=plugin.unavailable_reason,
        accent=plugin.accent,
        installed=plugin.id in installed,
    )


@router.get("/plugins", response_model=list[PluginOut])
def list_plugins(
    user: CurrentUser, db: DbSession, category: str = Query("")
) -> list[PluginOut]:
    installed = plugin_registry.installed_ids(db, user.id)
    rows = [
        _plugin_out(p, installed)
        for p in plugin_registry.CATALOG.values()
        if not category or p.category == category
    ]
    # Installed first, then available, then the ones that need work.
    return sorted(rows, key=lambda p: (not p.installed, not p.available, p.category, p.name))


@router.get("/plugins/categories", response_model=list[str])
def plugin_categories() -> list[str]:
    return plugin_registry.CATEGORIES


@router.get("/plugins/summary", response_model=PluginSummaryOut)
def plugin_summary(user: CurrentUser, db: DbSession) -> PluginSummaryOut:
    return PluginSummaryOut(**plugin_registry.summary(db, user.id))


@router.post("/plugins/{plugin_id}/install", response_model=PluginOut)
def install_plugin(plugin_id: str, user: CurrentUser, db: DbSession) -> PluginOut:
    ok, message = plugin_registry.install(db, user.id, plugin_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return _plugin_out(
        plugin_registry.CATALOG[plugin_id], plugin_registry.installed_ids(db, user.id)
    )


@router.post("/plugins/{plugin_id}/uninstall", response_model=PluginOut)
def uninstall_plugin(plugin_id: str, user: CurrentUser, db: DbSession) -> PluginOut:
    ok, message = plugin_registry.uninstall(db, user.id, plugin_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return _plugin_out(
        plugin_registry.CATALOG[plugin_id], plugin_registry.installed_ids(db, user.id)
    )


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


def _channel_out(kind_key: str, row, token: str | None = None) -> ChannelOut:
    spec = channel_service.KINDS[kind_key]
    return ChannelOut(
        kind=spec.key,
        name=spec.name,
        blurb=spec.blurb,
        setup=spec.setup,
        inbound=spec.inbound,
        available=spec.available,
        unavailable_reason=spec.unavailable_reason,
        connected=bool(row and row.enabled),
        verified=bool(row and row.verified),
        identifier=row.identifier if row else "",
        message_count=row.message_count if row else 0,
        last_seen_at=row.last_seen_at if row else None,
        token=token,
    )


@router.get("/channels", response_model=list[ChannelOut])
def list_channels(user: CurrentUser, db: DbSession) -> list[ChannelOut]:
    rows = {c.kind: c for c in channel_service.listing(db, user.id)}
    return [_channel_out(kind, rows.get(kind)) for kind in channel_service.KINDS]


@router.post("/channels/connect", response_model=ChannelOut)
def connect_channel(
    payload: ChannelConnectIn, user: CurrentUser, db: DbSession
) -> ChannelOut:
    try:
        row = channel_service.connect(db, user.id, payload.kind, payload.identifier)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # The token is returned exactly once, here.
    return _channel_out(payload.kind, row, token=row.token or None)


@router.post("/channels/{kind}/rotate", response_model=ChannelOut)
def rotate_channel_token(kind: str, user: CurrentUser, db: DbSession) -> ChannelOut:
    row = channel_service.rotate_token(db, user.id, kind)
    if not row:
        raise HTTPException(status_code=404, detail="Channel not connected")
    return _channel_out(kind, row, token=row.token)


@router.post("/channels/{kind}/disconnect")
def disconnect_channel(kind: str, user: CurrentUser, db: DbSession) -> dict:
    if not channel_service.disconnect(db, user.id, kind):
        raise HTTPException(status_code=400, detail="Cannot disconnect that channel")
    return {"message": "Disconnected"}


@router.post("/channels/inbound/{kind}", response_model=InboundOut)
def channel_inbound(kind: str, payload: InboundIn, db: DbSession) -> InboundOut:
    """Unauthenticated by JWT — the channel token is the credential.

    A webhook has no session, so the per-channel token stands in. It is compared
    in constant time and scoped to one channel of one user.
    """
    if kind not in channel_service.KINDS:
        raise HTTPException(status_code=404, detail="Unknown channel")

    user = channel_service.authenticate(db, kind, payload.token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid channel token")

    result = channel_service.handle_inbound(
        db, user, kind, payload.text, payload.thread_key
    )
    return InboundOut(**{k: v for k, v in result.items() if k != "skipped"})


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------


@router.get("/vault", response_model=list[SecretOut])
def list_secrets(user: CurrentUser, db: DbSession) -> list[SecretOut]:
    """Metadata only. There is deliberately no endpoint that returns a value."""
    return [SecretOut.model_validate(s) for s in vault_service.listing(db, user.id)]


@router.put("/vault", response_model=SecretOut)
def put_secret(payload: SecretIn, user: CurrentUser, db: DbSession) -> SecretOut:
    try:
        row = vault_service.put(
            db, user.id, payload.key, payload.value, payload.label, payload.kind
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SecretOut.model_validate(row)


@router.delete("/vault/{key}")
def delete_secret(key: str, user: CurrentUser, db: DbSession) -> dict:
    if not vault_service.delete(db, user.id, key):
        raise HTTPException(status_code=404, detail="Secret not found")
    return {"message": "Deleted"}


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(user: CurrentUser, db: DbSession) -> list[DocumentOut]:
    rows = db.scalars(
        select(Document)
        .where(Document.user_id == user.id)
        .order_by(Document.created_at.desc())
    ).all()
    return [DocumentOut.model_validate(d) for d in rows]


@router.get("/documents/search", response_model=list[DocumentPassage])
def search_documents(
    user: CurrentUser, db: DbSession, q: str = Query(..., min_length=1)
) -> list[DocumentPassage]:
    return [
        DocumentPassage(**hit) for hit in document_service.search(db, user.id, q, limit=8)
    ]


@router.post("/documents/upload", response_model=DocumentOut)
async def upload_document(
    user: CurrentUser, db: DbSession, file: UploadFile = File(...)
) -> DocumentOut:
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413, detail=f"File exceeds {settings.max_upload_mb}MB"
        )
    try:
        text, mime = document_service.extract_text(
            file.filename or "upload", data, file.content_type or ""
        )
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    if not text.strip():
        raise HTTPException(status_code=422, detail="No readable text in that file")

    document = document_service.ingest(
        db, user.id, file.filename or "Untitled", text, mime, len(data), "upload"
    )
    return DocumentOut.model_validate(document)


@router.post("/documents/text", response_model=DocumentOut)
def add_document_text(
    payload: DocumentTextIn, user: CurrentUser, db: DbSession
) -> DocumentOut:
    """Paste text in directly — handy for notes and anything not in a file."""
    document = document_service.ingest(
        db, user.id, payload.title, payload.content, "text/plain", 0, "paste"
    )
    return DocumentOut.model_validate(document)


@router.get("/documents/{document_id}", response_model=DocumentDetail)
def get_document(document_id: str, user: CurrentUser, db: DbSession) -> DocumentDetail:
    document = db.get(Document, document_id)
    if not document or document.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    chunks = db.scalar(
        select(func.count())
        .select_from(DocumentChunk)
        .where(DocumentChunk.document_id == document.id)
    )
    detail = DocumentDetail.model_validate(document)
    detail.content = document.content[:20_000]
    detail.chunk_count = int(chunks or 0)
    return detail


@router.post("/documents/{document_id}/summarize", response_model=DocumentOut)
def summarize_document(document_id: str, user: CurrentUser, db: DbSession) -> DocumentOut:
    document = db.get(Document, document_id)
    if not document or document.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    document_service.summarise(db, user.id, document)
    db.refresh(document)
    return DocumentOut.model_validate(document)


@router.delete("/documents/{document_id}")
def delete_document(document_id: str, user: CurrentUser, db: DbSession) -> dict:
    if not document_service.delete(db, user.id, document_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"message": "Deleted"}


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


def _schedule_out(row: Schedule) -> ScheduleOut:
    out = ScheduleOut.model_validate(row)
    out.cron_label = schedule_service.describe(row.cron)
    return out


@router.get("/schedules", response_model=list[ScheduleOut])
def list_schedules(user: CurrentUser, db: DbSession) -> list[ScheduleOut]:
    return [_schedule_out(s) for s in schedule_service.listing(db, user.id)]


@router.post("/schedules", response_model=ScheduleOut)
def create_schedule(payload: ScheduleIn, user: CurrentUser, db: DbSession) -> ScheduleOut:
    row = schedule_service.create(
        db, user,
        prompt=payload.prompt,
        natural_language=payload.natural_language,
        name=payload.name,
        cron=payload.cron,
        deliver_to=payload.deliver_to,
    )
    return _schedule_out(row)


@router.patch("/schedules/{schedule_id}", response_model=ScheduleOut)
def update_schedule(
    schedule_id: str, payload: SchedulePatch, user: CurrentUser, db: DbSession
) -> ScheduleOut:
    row = db.get(Schedule, schedule_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Schedule not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return _schedule_out(row)


@router.post("/schedules/{schedule_id}/run")
def run_schedule(schedule_id: str, user: CurrentUser, db: DbSession) -> dict:
    row = db.get(Schedule, schedule_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule_service.run_one(db, user, row)


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: str, user: CurrentUser, db: DbSession) -> dict:
    row = db.get(Schedule, schedule_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Schedule not found")
    db.delete(row)
    db.commit()
    return {"message": "Deleted"}


# ---------------------------------------------------------------------------
# Delegations
# ---------------------------------------------------------------------------


@router.get("/delegations", response_model=list[DelegationOut])
def list_delegations(
    user: CurrentUser, db: DbSession, status: str = Query("all")
) -> list[DelegationOut]:
    stmt = select(Delegation).where(Delegation.user_id == user.id)
    if status != "all":
        stmt = stmt.where(Delegation.status == status)
    rows = db.scalars(stmt.order_by(Delegation.due_at)).all()
    return [DelegationOut.model_validate(d) for d in rows]


@router.post("/delegations", response_model=DelegationOut)
def create_delegation(
    payload: DelegationIn, user: CurrentUser, db: DbSession
) -> DelegationOut:
    row = Delegation(
        user_id=user.id,
        assignee_name=payload.assignee,
        assignee_email=payload.assignee if "@" in payload.assignee else "",
        title=payload.title,
        context=payload.context,
        due_at=payload.due_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return DelegationOut.model_validate(row)


@router.patch("/delegations/{delegation_id}", response_model=DelegationOut)
def update_delegation(
    delegation_id: str,
    user: CurrentUser,
    db: DbSession,
    status: str = Query(..., pattern="^(open|chased|done)$"),
) -> DelegationOut:
    row = db.get(Delegation, delegation_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Delegation not found")
    row.status = status
    db.commit()
    db.refresh(row)
    return DelegationOut.model_validate(row)


@router.delete("/delegations/{delegation_id}")
def delete_delegation(delegation_id: str, user: CurrentUser, db: DbSession) -> dict:
    row = db.get(Delegation, delegation_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Delegation not found")
    db.delete(row)
    db.commit()
    return {"message": "Deleted"}
