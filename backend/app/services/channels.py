"""Channels — the surfaces the assistant can be reached on.

Every channel funnels into the same place: a conversation, run through the same
agent, with the same skills and the same permission gate. There is exactly one
code path deciding what the assistant is allowed to do.

A channel proves the channel, not the person: anyone who learns a webhook token
can post to it. So a message is acted on only when it is from the owner (the
configured `owner_sender`, or every message on a channel the owner declared a
private, owner-only bridge). Anything else is data, kept as a notification: it
starts no run, reaches no tool and teaches AURA nothing.

Inbound requests authenticate with a per-channel token rather than the user's
JWT, because a webhook has no session. The token is generated server-side, shown
to the owner once, and rotatable.
"""

from __future__ import annotations

import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from email.utils import parseaddr

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Channel, Conversation, Notification, User, utcnow

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


def authenticate_channel(db: Session, kind: str, token: str) -> Channel | None:
    """Resolve an inbound webhook token to its channel, in constant time."""
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
            return row
    return None


def authenticate(db: Session, kind: str, token: str) -> User | None:
    """Resolve an inbound webhook token to its owner, in constant time."""
    row = authenticate_channel(db, kind, token)
    return db.get(User, row.user_id) if row else None


# --- who is the owner on a channel -------------------------------------------


def owner_sender_of(channel: Channel | None) -> str:
    return str(((channel.config if channel else None) or {}).get("owner_sender") or "")


def bridge_owner_only_of(channel: Channel | None) -> bool:
    return bool(((channel.config if channel else None) or {}).get("bridge_owner_only"))


def set_owner(db: Session, channel: Channel, owner_sender: str, bridge_owner_only: bool) -> Channel:
    config = dict(channel.config or {})
    config["owner_sender"] = (owner_sender or "").strip()[:320]
    config["bridge_owner_only"] = bool(bridge_owner_only)
    channel.config = config  # a new dict: JSON columns do not notice in-place changes
    db.commit()
    db.refresh(channel)
    return channel


def normalise_sender(raw: str) -> str:
    """Compare senders as people write them: "Ana <Ana@Example.com>" is "ana@example.com",
    "@Ana" is "ana", "+1 (555) 010-2000" is "+15550102000". An empty result matches nothing."""
    text = (raw or "").strip()
    if not text:
        return ""
    addr = parseaddr(text)[1] if "@" in text and not text.startswith("@") else text
    addr = addr.strip().casefold()
    if re.fullmatch(r"\+?[\d\s().-]{6,}", addr):
        return ("+" if addr.startswith("+") else "") + re.sub(r"\D", "", addr)
    return addr.lstrip("@")


def is_owner_message(channel: Channel | None, sender: str) -> bool:
    """True only for the owner's own message on a verified channel (spec 9.3)."""
    if channel is None or not channel.verified or not channel.enabled:
        return False
    if bridge_owner_only_of(channel):
        return True
    configured = normalise_sender(owner_sender_of(channel))
    return bool(configured) and normalise_sender(sender) == configured


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


def _keep_as_notification(db: Session, user: User, kind: str, sender: str, text: str) -> dict:
    """A message that is not from the owner is shown to the owner and nothing else: no
    conversation, no model call, no tool, nothing learned."""
    who = re.sub(r"[\x00-\x1f\x7f]", " ", sender or "").strip()[:80] or "unknown"
    name = KINDS[kind].name if kind in KINDS else kind
    db.add(
        Notification(
            user_id=user.id, level="info", link="/dashboard",
            title=f"Message on {name} from {who}"[:300],
            body=(text or "")[:1500],
        )
    )
    db.commit()
    return {"reply": "", "skipped": "not from the owner", "pending": 0}


def handle_inbound(
    db: Session,
    user: User,
    kind: str,
    text: str,
    thread_key: str = "",
    sender: str = "",
    channel: Channel | None = None,
) -> dict:
    """Run an inbound message through the same agent the web app uses, if it is the owner's."""
    from app.agent.coordinator import persist_exchange, run_agent

    text = (text or "").strip()
    if not text:
        return {"reply": "", "skipped": "empty message"}
    if len(text) > 8000:
        text = text[:8000]

    channel = channel or get(db, user.id, kind)
    if not is_owner_message(channel, sender):
        return _keep_as_notification(db, user, kind, sender, text)

    convo = conversation_for(db, user, kind, thread_key)
    result = run_agent(db, user, convo, text, trigger="inbound", origin_id=kind)
    persist_exchange(db, convo, text, result)

    reply = result.text
    if result.pending_actions:
        n = len(result.pending_actions)
        reply += (
            f"\n\n({n} action{'s' if n != 1 else ''} waiting for your approval. "
            f"Open {settings.frontend_url}/dashboard to answer.)"
        )

    return {
        "reply": reply,
        "conversation_id": convo.id,
        "skills": [r["code"] for r in result.skill_runs],
        "pending": len(result.pending_actions),
        # A run that parked is carried on by the worker once the card is answered, and the owner is
        # notified when it finishes (taskloop.on_finished).
        "work_item_id": result.work_item_id,
        "work_status": result.work_status,
    }
