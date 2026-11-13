"""Assistant identity: a name, a personality, an avatar.

AURA works from the first message. Nothing about it is gated or unlocked by how
long the user has used it, so this module only holds what the user chose.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assistant, User

PERSONALITIES = {
    "concise": "Be brief and factual. Short sentences. No filler, no pleasantries.",
    "warm": "Be friendly and human. A little warmth is welcome, but never gushing.",
    "dry": "Be understated and lightly wry. Never at the user's expense, never at length.",
    "formal": "Be precise and professional. Full sentences, no contractions, no slang.",
    "encouraging": "Be supportive and forward-looking. Acknowledge progress before problems.",
}

AVATARS = ["teal", "amber", "rose", "violet", "sage"]


def get_or_create(db: Session, user: User) -> Assistant:
    row = db.scalars(select(Assistant).where(Assistant.user_id == user.id)).first()
    if row:
        return row
    row = Assistant(user_id=user.id, name="Aura")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def profile(db: Session, user: User) -> dict:
    assistant = get_or_create(db, user)
    return {
        "id": assistant.id,
        "name": assistant.name,
        "personality": assistant.personality,
        "avatar": assistant.avatar,
        "pronoun": assistant.pronoun,
        "goals": assistant.goals or [],
    }


def persona_prompt(assistant: Assistant) -> str:
    """The identity fragment injected at the top of every system prompt: a name and a voice.
    How the work is done lives in the system prompt itself."""
    style = PERSONALITIES.get(assistant.personality, PERSONALITIES["concise"])
    return f"You are {assistant.name}.\nVoice: {style}"
