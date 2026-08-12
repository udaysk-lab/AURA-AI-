"""Channels — the surfaces the assistant can be reached on.

Every channel funnels into the same place: a conversation, run through the same
agent, with the same skills and the same autonomy gate. A message from Telegram
is not a lesser message than one typed into the web app, and there is exactly
one code path deciding what the assistant is allowed to do.

Inbound requests authenticate with a per-channel token rather than the user's
JWT, because a webhook has no session. The token is generated server-side, shown
to the owner once, and rotatable.
"""

from __future__ import annotations

import hmac
import logging
import secrets
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Channel, Conversation, User, utcnow

log = logging.getLogger("aura.channels")


@dataclass(frozen=True)
class ChannelKind:
    key: str
    name: str
    blurb: str
    inbound: bool
    available: bool = True
    unavailable_reason: str = ""
    setup: str = ""


KINDS: dict[str, ChannelKind] = {
    k.key: k
    for k in [
        ChannelKind(
            key="web",
            name="Web",
            blurb="This app. Always on, always available.",
            inbound=False,
        ),
        ChannelKind(
            key="email",
            name="Email",
            blurb="Forward or CC your assistant and it replies like a colleague.",
            inbound=True,
            setup=(
                "Point your mail provider's inbound webhook at "
                "POST /api/channels/inbound/email with the token below."
            ),
        ),
        ChannelKind(
            key="telegram",
            name="Telegram",
            blurb="Message it from your phone without opening the app.",
            inbound=True,
            setup=(
                "Create a bot with @BotFather, then set its webhook to "
                "POST /api/channels/inbound/telegram?token=… "
            ),
        ),
        ChannelKind(
            key="slack",
            name="Slack",
            blurb="Mention it in a channel and it answers in the thread.",
            inbound=True,
            setup=(
                "Create a Slack app with an Events subscription pointing at "
                "POST /api/channels/inbound/slack with the token below."
            ),
        ),
        ChannelKind(
            key="cli",
            name="CLI",
            blurb="Talk to it from a terminal or a script.",
            inbound=True,
            setup=(
                'curl -X POST $AURA_URL/api/channels/inbound/cli '
                '-H "Content-Type: application/json" '
                '-d \'{"token":"…","text":"what does my day look like"}\''
            ),
        ),
        ChannelKind(
            key="sms",
            name="SMS",
            blurb="Text it.",
            inbound=True,
            available=False,
            unavailable_reason="Needs a Twilio account — not wired up yet.",
        ),
        ChannelKind(
            key="voice",
            name="Voice",
            blurb="Call it, or have it call you.",
            inbound=True,
            available=False,
            unavailable_reason="Needs a speech provider — not wired up yet.",
        ),
    ]
}


def ensure_web(db: Session, user_id: str) -> Channel:
    """Every account has the web channel, on by default."""
    row = db.scalars(
        select(Channel).where(Channel.user_id == user_id, Channel.kind == "web")
    ).first()
    if row:
        return row
    row = Channel(
        user_id=user_id, kind="web", enabled=True, verified=True, identifier="app"
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def listing(db: Session, user_id: str) -> list[Channel]:
    ensure_web(db, user_id)
    return list(
        db.scalars(select(Channel).where(Channel.user_id == user_id)).all()
    )


def get(db: Session, user_id: str, kind: str) -> Channel | None:
    return db.scalars(
        select(Channel).where(Channel.user_id == user_id, Channel.kind == kind)
    ).first()


def connect(db: Session, user_id: str, kind: str, identifier: str = "") -> Channel:
    spec = KINDS.get(kind)
    if not spec:
        raise ValueError(f"Unknown channel: {kind}")
    if not spec.available:
        raise ValueError(spec.unavailable_reason)

    row = get(db, user_id, kind)
    if row is None:
        row = Channel(user_id=user_id, kind=kind)
        db.add(row)

    row.identifier = identifier
    row.enabled = True
    row.verified = kind == "web"
    if spec.inbound and not row.token:
        row.token = secrets.token_urlsafe(32)
    db.commit()
    db.refresh(row)
    return row


def rotate_token(db: Session, user_id: str, kind: str) -> Channel | None:
    row = get(db, user_id, kind)
    if not row:
        return None
    row.token = secrets.token_urlsafe(32)
    db.commit()
    db.refresh(row)
    return row


def disconnect(db: Session, user_id: str, kind: str) -> bool:
    if kind == "web":
        return False  # you cannot lock yourself out of the app
    row = get(db, user_id, kind)
    if not row:
        return False
    row.enabled = False
    row.token = ""
    db.commit()
    return True


def authenticate(db: Session, kind: str, token: str) -> User | None:
    """Resolve an inbound webhook token to its owner, in constant time."""
    if not token:
        return None
    candidates = db.scalars(
        select(Channel).where(
            Channel.kind == kind,
            Channel.enabled == True,  # noqa: E712
        )
    ).all()
    for row in candidates:
        if row.token and hmac.compare_digest(row.token, token):
            row.verified = True
            row.last_seen_at = utcnow()
            row.message_count += 1
            db.commit()
            return db.get(User, row.user_id)
    return None


def conversation_for(db: Session, user: User, kind: str, thread_key: str = "") -> Conversation:
    """One rolling conversation per channel (per thread, where the channel has them)."""
    title = f"{KINDS[kind].name if kind in KINDS else kind}" + (
        f" · {thread_key[:30]}" if thread_key else ""
    )
    row = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id, Conversation.title == title)
        .order_by(Conversation.updated_at.desc())
        .limit(1)
    ).first()
    if row:
        return row
    row = Conversation(user_id=user.id, title=title)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def handle_inbound(db: Session, user: User, kind: str, text: str, thread_key: str = "") -> dict:
    """Run an inbound message through the same agent the web app uses."""
    from app.agent.coordinator import persist_exchange, run_agent
    from app.deps import get_user_settings

    text = (text or "").strip()
    if not text:
        return {"reply": "", "skipped": "empty message"}
    if len(text) > 8000:
        text = text[:8000]

    convo = conversation_for(db, user, kind, thread_key)
    tier = get_user_settings(db, user).autonomy_level
    result = run_agent(db, user, convo, text, autonomy_level=tier, trigger="inbound")
    persist_exchange(db, convo, text, result)

    reply = result.text
    if result.pending_actions:
        reply += (
            f"\n\n({len(result.pending_actions)} action awaiting your approval — "
            f"open {settings.frontend_url}/dashboard)"
        )

    return {
        "reply": reply,
        "conversation_id": convo.id,
        "skills": [r["code"] for r in result.skill_runs],
        "pending": len(result.pending_actions),
    }
