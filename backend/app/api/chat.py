"""Chat: conversations, messages, and the agent endpoints."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.agent.coordinator import persist_exchange, run_agent, stream_agent
from app.config import settings
from app.db import SessionLocal
from app.deps import CurrentUser, DbSession, get_user_settings
from app.models import Conversation, Message, User
from app.schemas import (
    ChatIn,
    ChatOut,
    ConversationDetail,
    ConversationOut,
    MessageOut,
    PendingActionOut,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])
log = logging.getLogger("aura.chat")


def _stream_error(exc: Exception) -> str:
    """What the chat shows when a turn fails part-way through a stream.

    The raw exception text is for the logs; outside debug mode it can carry SQL,
    file paths or provider responses, which is the same reason the global
    handler answers "Internal error".
    """
    if isinstance(exc, OperationalError):
        return "I couldn't reach the database just now, so that didn't finish. Please try again in a minute."
    if settings.debug:
        return str(exc)
    return "Something went wrong on my side while answering. Please try again."


def _get_or_create(db, user, conversation_id: str | None) -> Conversation:
    if conversation_id:
        convo = db.get(Conversation, conversation_id)
        if not convo or convo.user_id != user.id:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return convo
    convo = Conversation(user_id=user.id)
    db.add(convo)
    db.commit()
    db.refresh(convo)
    return convo


UNTITLED = ("", "New conversation")


def _title_from(text: str) -> str:
    return text.strip().split("\n")[0][:60]


def _backfill_titles(db, rows: list[Conversation]) -> None:
    """Name conversations that still carry the placeholder.

    Streamed chats stayed "New conversation" until the September fix, and
    those already saved kept the placeholder. A conversation is named from its
    first message once, here, the next time the list is read.
    """
    untitled = [c for c in rows if c.title in UNTITLED]
    changed = False
    for convo in untitled:
        first = db.scalars(
            select(Message)
            .where(Message.conversation_id == convo.id, Message.role == "user")
            .order_by(Message.created_at)
            .limit(1)
        ).first()
        if first and first.content.strip():
            convo.title = _title_from(first.content)
            changed = True
    if changed:
        db.commit()


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(user: CurrentUser, db: DbSession) -> list[ConversationOut]:
    rows = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id, Conversation.archived == False)  # noqa: E712
        .order_by(Conversation.pinned.desc(), Conversation.updated_at.desc())
        .limit(100)
    ).all()
    _backfill_titles(db, list(rows))
    return [ConversationOut.model_validate(c) for c in rows]


@router.post("/conversations", response_model=ConversationOut)
def create_conversation(user: CurrentUser, db: DbSession) -> ConversationOut:
    convo = Conversation(user_id=user.id)
    db.add(convo)
    db.commit()
    db.refresh(convo)
    return ConversationOut.model_validate(convo)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str, user: CurrentUser, db: DbSession) -> ConversationDetail:
    convo = db.get(Conversation, conversation_id)
    if not convo or convo.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = db.scalars(
        select(Message)
        .where(Message.conversation_id == convo.id)
        .order_by(Message.created_at)
    ).all()
    detail = ConversationDetail.model_validate(convo)
    detail.messages = [MessageOut.model_validate(m) for m in messages]
    return detail


@router.patch("/conversations/{conversation_id}/pin", response_model=ConversationOut)
def toggle_pin(conversation_id: str, user: CurrentUser, db: DbSession) -> ConversationOut:
    convo = db.get(Conversation, conversation_id)
    if not convo or convo.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    convo.pinned = not convo.pinned
    db.commit()
    db.refresh(convo)
    return ConversationOut.model_validate(convo)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, user: CurrentUser, db: DbSession) -> dict:
    convo = db.get(Conversation, conversation_id)
    if not convo or convo.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(convo)
    db.commit()
    return {"message": "Deleted"}


@router.post("", response_model=ChatOut)
def chat(payload: ChatIn, user: CurrentUser, db: DbSession) -> ChatOut:
    """Non-streaming turn. Use /stream for the live UI."""
    convo = _get_or_create(db, user, payload.conversation_id)
    tier = get_user_settings(db, user).autonomy_level
    result = run_agent(db, user, convo, payload.message, autonomy_level=tier)
    message = persist_exchange(db, convo, payload.message, result)
    return ChatOut(
        conversation_id=convo.id,
        message=MessageOut.model_validate(message),
        tool_calls=[
            {"name": c["name"], "skill_code": c.get("skill_code", ""),
             "arguments": c["arguments"]}
            for c in result.tool_calls
        ],
        skill_runs=result.skill_runs,
        memories_used=result.memories_used,
        pending_actions=[PendingActionOut.model_validate(a) for a in result.pending_actions],
        learned=result.learned,
    )


@router.post("/stream")
def chat_stream(payload: ChatIn, user: CurrentUser, db: DbSession) -> StreamingResponse:
    convo = _get_or_create(db, user, payload.conversation_id)
    tier = get_user_settings(db, user).autonomy_level
    user_id, convo_id = user.id, convo.id
    bind = db.get_bind()  # same engine as the request, test overrides included

    def event_source() -> Iterator[str]:
        yield f"data: {json.dumps({'type': 'start', 'conversation_id': convo_id})}\n\n"
        # The generator runs after the route has returned, and by then FastAPI
        # has already closed the request's session. Working through that closed
        # session left `convo` and `user` detached: tools and messages still
        # committed, but changes to the conversation itself were never flushed,
        # so every streamed chat stayed titled "New conversation" and its
        # updated_at never moved. The stream gets a session of its own.
        stream_db = SessionLocal(bind=bind)
        try:
            stream_user = stream_db.get(User, user_id)
            stream_convo = stream_db.get(Conversation, convo_id)
            for event in stream_agent(
                stream_db, stream_user, stream_convo, payload.message,
                autonomy_level=tier,
            ):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as exc:  # never leave the client hanging
            log.exception("Chat stream failed for conversation %s", convo_id)
            stream_db.rollback()
            yield f"data: {json.dumps({'type': 'error', 'message': _stream_error(exc)})}\n\n"
        finally:
            stream_db.close()
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
