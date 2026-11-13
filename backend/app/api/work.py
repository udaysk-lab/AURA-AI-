"""Work items (task memory), preferences and task types: the owner's view and controls.

Spec 20 sections 11 and 13. Every row is checked against the signed-in user (404 otherwise).
These are user HTTP endpoints only; no agent tool reaches them, and the gate's hard floor on
AURA's own API stops a fetch to them.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select

from app.agent import taskloop
from app.deps import CurrentUser, DbSession
from app.models import PendingAction, Preference, User, WorkItem
from app.schemas import (
    PreferenceIn,
    PreferenceOut,
    PreferencePatch,
    RecallUsedOut,
    TaskTypeOut,
    WorkItemDetail,
    WorkItemFeedbackIn,
    WorkItemOut,
    WorkItemPatch,
    WorkStepOut,
)
from app.services import preferences as preference_service
from app.services import tasktypes
from app.services import workmemory

router = APIRouter(prefix="/api", tags=["work"])

MAX_LIMIT = 100
DEFAULT_LIMIT = 50


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def _own_item(db, user: User, item_id: str) -> WorkItem:
    item = db.get(WorkItem, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="Task not found")
    return item


def _own_preference(db, user: User, pref_id: str) -> Preference:
    pref = db.get(Preference, pref_id)
    if pref is None or pref.user_id != user.id:
        raise HTTPException(status_code=404, detail="Preference not found")
    return pref


def detail_of(db, item: WorkItem) -> WorkItemDetail:
    """The detail view: the row, its steps in order, the cards still waiting, and what was recalled
    (ids resolved to lists, rows deleted since left out)."""
    out = WorkItemDetail.model_validate(item)
    out.steps = [WorkStepOut.model_validate(s) for s in workmemory.steps_of(db, item.id)]
    out.recall_used = RecallUsedOut.model_validate(workmemory.resolve_recall(db, item))
    out.pending_action_ids = list(db.scalars(
        select(PendingAction.id)
        .where(PendingAction.work_item_id == item.id, PendingAction.status == "pending")
        .order_by(PendingAction.created_at, PendingAction.id)
    ).all())
    return out


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_before(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail="before must be an ISO date and time")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Work items
# ---------------------------------------------------------------------------


@router.get("/work-items", response_model=list[WorkItemOut])
def list_work_items(
    user: CurrentUser,
    db: DbSession,
    status: str = Query(""),
    task_type: str = Query(""),
    conversation_id: str = Query(""),
    q: str = Query("", max_length=200),
    limit: int = Query(DEFAULT_LIMIT, ge=1),
    before: str = Query(""),
) -> list[WorkItemOut]:
    stmt = select(WorkItem).where(WorkItem.user_id == user.id)
    if status:
        stmt = stmt.where(WorkItem.status.in_([s for s in status.split(",") if s.strip()]))
    if task_type:
        stmt = stmt.where(WorkItem.task_type == task_type)
    if conversation_id:
        stmt = stmt.where(WorkItem.conversation_id == conversation_id)
    if q.strip():
        pattern = f"%{_like_escape(q.strip())}%"
        stmt = stmt.where(or_(WorkItem.title.ilike(pattern, escape="\\"),
                              WorkItem.instruction.ilike(pattern, escape="\\")))
    if before:
        stmt = stmt.where(WorkItem.created_at < _parse_before(before))
    rows = db.scalars(
        stmt.order_by(WorkItem.created_at.desc(), WorkItem.id.desc()).limit(min(limit, MAX_LIMIT))
    ).all()
    return [WorkItemOut.model_validate(r) for r in rows]


@router.get("/task-types", response_model=list[TaskTypeOut])
def task_types(user: CurrentUser) -> list[dict]:
    return tasktypes.options()


@router.get("/work-items/{item_id}", response_model=WorkItemDetail)
def get_work_item(item_id: str, user: CurrentUser, db: DbSession) -> WorkItemDetail:
    return detail_of(db, _own_item(db, user, item_id))


@router.patch("/work-items/{item_id}", response_model=WorkItemDetail)
def patch_work_item(item_id: str, payload: WorkItemPatch, user: CurrentUser, db: DbSession) -> WorkItemDetail:
    item = _own_item(db, user, item_id)
    try:
        workmemory.edit(db, item, **payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return detail_of(db, item)


@router.delete("/work-items/{item_id}")
def delete_work_item(item_id: str, user: CurrentUser, db: DbSession):
    item = _own_item(db, user, item_id)
    if taskloop.is_live(item):
        return JSONResponse(
            status_code=409,
            content={"detail": "AURA is still working on this task. Stop it first.", "code": "still_working",
                     "work_item_id": item.id, "status": "running"},
        )
    if item.status in workmemory.OPEN_STATUSES:
        # Waiting, ready, or a run whose process died: close it properly before it goes, so its
        # pending cards are rejected and nothing is left waiting for an item that no longer exists.
        taskloop.stop(db, user, item)
        db.refresh(item)
    try:
        workmemory.delete_item(db, item)
    except workmemory.ItemBusy:
        return JSONResponse(
            status_code=409,
            content={"detail": "AURA is still working on this task. Stop it first.", "code": "still_working",
                     "work_item_id": item.id, "status": "running"},
        )
    return {"message": "Deleted"}


@router.post("/work-items/{item_id}/feedback", response_model=WorkItemDetail)
def work_item_feedback(item_id: str, payload: WorkItemFeedbackIn, user: CurrentUser, db: DbSession) -> WorkItemDetail:
    item = _own_item(db, user, item_id)
    workmemory.set_feedback(db, user, item, payload.rating, payload.note)
    return detail_of(db, item)


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


@router.get("/preferences", response_model=list[PreferenceOut])
def list_preferences(
    user: CurrentUser, db: DbSession, task_type: str = Query(""), include_inactive: bool = Query(False)
) -> list[PreferenceOut]:
    rows = preference_service.listing(db, user.id, task_type or None, include_inactive)
    return [PreferenceOut.model_validate(r) for r in rows]


@router.post("/preferences", response_model=PreferenceOut)
def add_preference(payload: PreferenceIn, user: CurrentUser, db: DbSession) -> PreferenceOut:
    """A line the owner typed in Settings. The conflict rules apply: a near-twin is not added twice,
    and a close rewrite replaces the old line."""
    try:
        captured = preference_service.add(
            db, user, payload.text, payload.task_type, "stated", "settings", system_scope=payload.system_scope
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return PreferenceOut.model_validate(captured.preference)


@router.patch("/preferences/{pref_id}", response_model=PreferenceOut)
def patch_preference(pref_id: str, payload: PreferencePatch, user: CurrentUser, db: DbSession) -> PreferenceOut:
    pref = _own_preference(db, user, pref_id)
    try:
        preference_service.update(db, pref, **payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return PreferenceOut.model_validate(pref)


@router.delete("/preferences/{pref_id}")
def delete_preference(pref_id: str, user: CurrentUser, db: DbSession) -> dict:
    preference_service.delete(db, _own_preference(db, user, pref_id))
    return {"message": "Deleted"}
