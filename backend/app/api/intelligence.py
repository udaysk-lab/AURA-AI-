"""Skills, identity, heartbeat and memory maintenance."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.agent import skills as skill_registry
from app.deps import CurrentUser, DbSession, PresentUser
from app.models import HeartbeatReport
from app.schemas import (
    AssistantOut,
    AssistantPatch,
    CompactionOut,
    HeartbeatOut,
    SkillOut,
    SkillPatch,
    SkillRunOut,
    SkillStatsOut,
    SkillTeachIn,
)
from app.services import heartbeat as heartbeat_service
from app.services import identity as identity_service
from app.services import memory as memory_service
from app.services import preferences as preference_service

router = APIRouter(prefix="/api", tags=["intelligence"])


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


@router.get("/skills", response_model=list[SkillOut])
def list_skills(
    user: CurrentUser, db: DbSession, category: str = Query("")
) -> list[SkillOut]:
    return _skill_views(db, user, category=category)


def _skill_views(db, user, category: str = "") -> list[SkillOut]:
    """The skills this user can see, as the API returns them.

    Kept separate from the route on purpose. The PATCH and teach endpoints used
    to call `list_skills(user, db)` directly, which left `category` at its
    default — a FastAPI `Query("")` object, not the string "". That object is
    truthy and never equals a category name, so every skill was filtered out,
    `next()` found nothing, and the StopIteration surfaced as a 500 ("coroutine
    raised StopIteration") after the change had already been committed.
    """
    rows = {r.code: r for r in skill_registry.ensure_user_skills(db, user.id)}
    unlocked = skill_registry.unlocked_codes(db, user.id)
    out: list[SkillOut] = []
    for code, skill in skill_registry.CATALOG.items():
        # Skills from uninstalled plugins are hidden, not merely disabled.
        if code not in unlocked:
            continue
        if category and skill.category != category:
            continue
        row = rows.get(code)
        out.append(
            SkillOut(
                code=skill.code,
                name=skill.name,
                category=skill.category,
                description=skill.description,
                tools=list(skill.tools),
                proactive=skill.proactive,
                autonomy_floor=skill.autonomy_floor,
                enabled=row.enabled if row else skill.default_enabled,
                run_count=row.run_count if row else 0,
                success_count=row.success_count if row else 0,
                last_run_at=row.last_run_at if row else None,
                learned_notes=preference_service.skill_notes(db, user.id, code),
            )
        )
    return sorted(out, key=lambda s: (s.category, s.code))


def _require_visible(db, user, code: str) -> None:
    if code not in skill_registry.CATALOG:
        raise HTTPException(status_code=404, detail="Unknown skill")
    if code not in skill_registry.unlocked_codes(db, user.id):
        raise HTTPException(
            status_code=404,
            detail="That skill belongs to a plugin that isn't installed.",
        )


def _skill_view(db, user, code: str) -> SkillOut:
    for skill in _skill_views(db, user):
        if skill.code == code:
            return skill
    _require_visible(db, user, code)
    raise HTTPException(status_code=404, detail="Unknown skill")


@router.get("/skills/categories", response_model=list[str])
def skill_categories() -> list[str]:
    return skill_registry.CATEGORIES


@router.get("/skills/catalog")
def skill_catalog() -> JSONResponse:
    """Every skill AURA has, for the landing page. Public; nothing per-user.

    The landing page used to keep its own hardcoded list, which drifted from
    this catalogue twice. Reading it from here means it can't drift again.
    Device skills are listed too, flagged, because they're what the desktop
    app adds.
    """
    skills = [
        {
            "code": s.code,
            "name": s.name,
            "category": s.category,
            "description": s.description,
            "device": skill_registry.is_device_skill(s.code),
            "tools": len(s.tools),
        }
        for s in skill_registry.CATALOG.values()
    ]
    body = {
        "skills": skills,
        "counts": {
            "skills": len(skills),
            "device_skills": sum(1 for s in skills if s["device"]),
            "tools": len(skill_registry.TOOL_TO_SKILL),
        },
    }
    return JSONResponse(content=body, headers={"Cache-Control": "public, max-age=3600"})


@router.get("/skills/stats", response_model=SkillStatsOut)
def skill_stats(user: CurrentUser, db: DbSession) -> SkillStatsOut:
    data = skill_registry.stats(db, user.id)
    most = data.pop("most_used", None)
    data["total"] = len(skill_registry.unlocked_codes(db, user.id))
    skill = skill_registry.CATALOG.get(most.code) if most else None
    return SkillStatsOut(
        **data,
        most_used_code=most.code if most else "",
        most_used_name=skill.name if skill else "",
    )


@router.get("/skills/activity", response_model=list[SkillRunOut])
def skill_activity(
    user: CurrentUser, db: DbSession, limit: int = Query(40, ge=1, le=200)
) -> list[SkillRunOut]:
    return [
        SkillRunOut.model_validate(r)
        for r in skill_registry.recent_runs(db, user.id, limit=limit)
    ]


@router.patch("/skills/{code}", response_model=SkillOut)
def update_skill(
    code: str, payload: SkillPatch, user: PresentUser, db: DbSession
) -> SkillOut:
    code = code.upper()
    _require_visible(db, user, code)
    row = skill_registry.get_user_skill(db, user.id, code)
    if not row:
        skill_registry.ensure_user_skills(db, user.id)
        row = skill_registry.get_user_skill(db, user.id, code)
    if row and payload.enabled is not None:
        was = bool(row.enabled)
        row.enabled = payload.enabled
        db.commit()
        if was != bool(payload.enabled):
            from app.permissions import changes

            changes.record(db, user, "system_changed", f"skill {code} turned {'on' if payload.enabled else 'off'}",
                           widens=bool(payload.enabled), target=f"skill:{code}", detail={"enabled": bool(payload.enabled)})
    return _skill_view(db, user, code)


@router.post("/skills/{code}/teach", response_model=SkillOut)
def teach_skill(
    code: str, payload: SkillTeachIn, user: CurrentUser, db: DbSession
) -> SkillOut:
    """Kept for older clients (the web app no longer calls it). A note typed against a skill is
    stored as a preference for that skill's kind of work (origin `skill_teach`); the conflict rules
    of preference memory apply. The skill view's `learned_notes` reads those lines back."""
    code = code.upper()
    _require_visible(db, user, code)
    try:
        preference_service.teach(db, user, code, payload.note.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _skill_view(db, user, code)


@router.delete("/skills/{code}/notes")
def clear_skill_notes(code: str, user: CurrentUser, db: DbSession) -> dict:
    """Kept for older clients. Switches off the lines `teach` (and the 2.0 migration) stored for
    this skill's kind of work; they stay listed, inactive, under Memory > Preferences."""
    code = code.upper()
    if code not in skill_registry.CATALOG:
        raise HTTPException(status_code=404, detail="Unknown skill")
    preference_service.clear_skill_notes(db, user.id, code)
    return {"message": "Cleared"}


# ---------------------------------------------------------------------------
# Assistant identity
# ---------------------------------------------------------------------------


@router.get("/assistant", response_model=AssistantOut)
def get_assistant(user: CurrentUser, db: DbSession) -> AssistantOut:
    return AssistantOut(**identity_service.profile(db, user))


@router.patch("/assistant", response_model=AssistantOut)
def update_assistant(
    payload: AssistantPatch, user: CurrentUser, db: DbSession
) -> AssistantOut:
    assistant = identity_service.get_or_create(db, user)
    changes = payload.model_dump(exclude_unset=True)
    # Explicit fields only: nothing outside this list can be written here.
    for field in ("name", "personality", "avatar", "pronoun", "goals"):
        if field in changes and changes[field] is not None:
            setattr(assistant, field, changes[field])
    db.commit()
    return AssistantOut(**identity_service.profile(db, user))


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


@router.get("/heartbeat", response_model=list[HeartbeatOut])
def heartbeat_history(
    user: CurrentUser, db: DbSession, limit: int = Query(10, ge=1, le=50)
) -> list[HeartbeatOut]:
    rows = db.scalars(
        select(HeartbeatReport)
        .where(HeartbeatReport.user_id == user.id)
        .order_by(HeartbeatReport.created_at.desc())
        .limit(limit)
    ).all()
    return [HeartbeatOut.model_validate(r) for r in rows]


@router.get("/heartbeat/latest", response_model=HeartbeatOut | None)
def heartbeat_latest(user: CurrentUser, db: DbSession) -> HeartbeatOut | None:
    row = heartbeat_service.latest(db, user.id)
    return HeartbeatOut.model_validate(row) if row else None


@router.post("/heartbeat/run", response_model=HeartbeatOut)
def heartbeat_run(user: CurrentUser, db: DbSession) -> HeartbeatOut:
    return HeartbeatOut.model_validate(heartbeat_service.run(db, user, force=True))


@router.post("/heartbeat/{report_id}/ack", response_model=HeartbeatOut)
def heartbeat_ack(report_id: str, user: CurrentUser, db: DbSession) -> HeartbeatOut:
    row = db.get(HeartbeatReport, report_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Report not found")
    row.acknowledged = True
    db.commit()
    db.refresh(row)
    return HeartbeatOut.model_validate(row)


# ---------------------------------------------------------------------------
# Memory maintenance
# ---------------------------------------------------------------------------


@router.post("/memories/compact", response_model=CompactionOut)
def compact_memory(
    user: CurrentUser, db: DbSession, dry_run: bool = Query(False)
) -> CompactionOut:
    return CompactionOut(**memory_service.compact(db, user.id, dry_run=dry_run))
