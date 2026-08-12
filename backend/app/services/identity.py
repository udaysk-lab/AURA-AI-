"""Assistant identity and the growth arc.

The assistant is not a faceless service — it has a name the user chose, a
personality, and a visible stage in its relationship with them. The stage is
never set by hand; it is derived from real signals (how much you've talked to
it, how much it knows about you, how much it has actually done). That means the
progression can't be gamed and reflects something true.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Assistant, Memory, SkillRun, User

PERSONALITIES = {
    "concise": "Be brief and factual. Short sentences. No filler, no pleasantries.",
    "warm": "Be friendly and human. A little warmth is welcome, but never gushing.",
    "dry": "Be understated and lightly wry. Never at the user's expense, never at length.",
    "formal": "Be precise and professional. Full sentences, no contractions, no slang.",
    "encouraging": "Be supportive and forward-looking. Acknowledge progress before problems.",
}

AVATARS = ["teal", "amber", "rose", "violet", "sage"]


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    blurb: str
    # Thresholds that must all be met to reach this stage.
    interactions: int
    memories: int
    actions: int


STAGES: list[Stage] = [
    Stage(
        "stranger",
        "Stranger",
        "Just met you. Eager but clueless — tell it your style, your role, your tools.",
        0,
        0,
        0,
    ),
    Stage(
        "acquaintance",
        "Acquaintance",
        "Picking up patterns. It gets things half-right and asks better questions. Correct it when it's off.",
        5,
        3,
        2,
    ),
    Stage(
        "colleague",
        "Colleague",
        "Finishes your thought. Acts before you ask. You've stopped managing it.",
        25,
        12,
        15,
    ),
    Stage(
        "chief_of_staff",
        "Chief of Staff",
        "Runs your day without checking in. You notice it most when it's off.",
        80,
        30,
        60,
    ),
]

STAGE_BY_KEY = {s.key: s for s in STAGES}


def get_or_create(db: Session, user: User) -> Assistant:
    row = db.scalars(select(Assistant).where(Assistant.user_id == user.id)).first()
    if row:
        return row
    row = Assistant(user_id=user.id, name="Aura")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def signals(db: Session, user_id: str, assistant: Assistant) -> dict:
    memories = db.scalar(
        select(func.count()).select_from(Memory).where(Memory.user_id == user_id)
    ) or 0
    runs = db.scalar(
        select(func.count()).select_from(SkillRun).where(SkillRun.user_id == user_id)
    ) or 0
    return {
        "interactions": assistant.interactions,
        "memories": int(memories),
        "actions": max(int(runs), assistant.actions_taken),
    }


def compute_stage(sig: dict) -> Stage:
    reached = STAGES[0]
    for stage in STAGES:
        if (
            sig["interactions"] >= stage.interactions
            and sig["memories"] >= stage.memories
            and sig["actions"] >= stage.actions
        ):
            reached = stage
    return reached


def progress_to_next(sig: dict, current: Stage) -> dict | None:
    """How far along to the next stage, as the weakest of the three signals."""
    idx = STAGES.index(current)
    if idx >= len(STAGES) - 1:
        return None
    nxt = STAGES[idx + 1]
    ratios = []
    for key, target in (
        ("interactions", nxt.interactions),
        ("memories", nxt.memories),
        ("actions", nxt.actions),
    ):
        ratios.append(min(1.0, sig[key] / target) if target else 1.0)
    return {
        "next": nxt.key,
        "next_label": nxt.label,
        "percent": round(min(ratios) * 100),
        "needs": {
            "interactions": max(0, nxt.interactions - sig["interactions"]),
            "memories": max(0, nxt.memories - sig["memories"]),
            "actions": max(0, nxt.actions - sig["actions"]),
        },
    }


def refresh_stage(db: Session, user: User) -> Assistant:
    assistant = get_or_create(db, user)
    sig = signals(db, user.id, assistant)
    stage = compute_stage(sig)
    if assistant.stage != stage.key:
        assistant.stage = stage.key
        db.commit()
        db.refresh(assistant)
    return assistant


def record_interaction(db: Session, user: User, actions: int = 0) -> Assistant:
    assistant = get_or_create(db, user)
    assistant.interactions += 1
    assistant.actions_taken += actions
    db.commit()
    return refresh_stage(db, user)


def record_correction(db: Session, user: User) -> Assistant:
    assistant = get_or_create(db, user)
    assistant.corrections += 1
    db.commit()
    db.refresh(assistant)
    return assistant


def profile(db: Session, user: User) -> dict:
    assistant = refresh_stage(db, user)
    sig = signals(db, user.id, assistant)
    stage = STAGE_BY_KEY.get(assistant.stage, STAGES[0])
    return {
        "id": assistant.id,
        "name": assistant.name,
        "personality": assistant.personality,
        "avatar": assistant.avatar,
        "pronoun": assistant.pronoun,
        "goals": assistant.goals or [],
        "onboarded": assistant.onboarded,
        "hatched_at": assistant.hatched_at,
        "stage": stage.key,
        "stage_label": stage.label,
        "stage_blurb": stage.blurb,
        "signals": sig,
        "progress": progress_to_next(sig, stage),
        "days_together": _days_since(assistant.hatched_at),
    }


def _days_since(when: datetime | None) -> int:
    if not when:
        return 0
    aware = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - aware).days)


def persona_prompt(assistant: Assistant) -> str:
    """The identity fragment injected at the top of every system prompt."""
    style = PERSONALITIES.get(assistant.personality, PERSONALITIES["concise"])
    stage = STAGE_BY_KEY.get(assistant.stage, STAGES[0])

    stage_guidance = {
        "stranger": (
            "You have just met this person. Ask for context when you genuinely need it, "
            "and save what you learn. Do not pretend to know things you don't."
        ),
        "acquaintance": (
            "You know some of this person's patterns. Offer your read, but check it when "
            "the stakes are real."
        ),
        "colleague": (
            "You know how this person works. Act first and report, rather than asking "
            "permission for things you already know the answer to."
        ),
        "chief_of_staff": (
            "You run this person's day. Be decisive. Surface only what genuinely needs "
            "their judgement."
        ),
    }[stage.key]

    return (
        f"You are {assistant.name}, this person's assistant.\n"
        f"Voice: {style}\n"
        f"Where you are in the relationship: {stage.label}. {stage_guidance}"
    )
