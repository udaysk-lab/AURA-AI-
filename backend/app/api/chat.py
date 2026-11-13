"""Chat: conversations, messages, and the agent endpoints."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.agent import taskloop
from app.agent.coordinator import persist_exchange, run_agent, stream_agent
from app.config import settings
from app.db import SessionLocal
from app.deps import CurrentUser, DbSession, GuardedUser
from app.models import Conversation, Message, User, WorkItem
from app.schemas import (
    ChatContinueIn,
    ChatIn,
    ChatOut,
    ConversationDetail,
    ConversationMemoryIn,
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


def _get_or_create(db, user, conversation_id: str | None, memory_off: bool | None = None) -> Conversation:
    """The conversation a message goes to. `memory_off` (from ChatIn) is applied before the run
    starts, so the very first message of a new chat can already opt out of memory."""
    if conversation_id:
        convo = db.get(Conversation, conversation_id)
        if not convo or convo.user_id != user.id:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if memory_off is not None and bool(convo.memory_off) != memory_off:
            convo.memory_off = memory_off
            db.commit()
        return convo
    convo = Conversation(user_id=user.id, memory_off=bool(memory_off))
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


@router.patch("/conversations/{conversation_id}/memory", response_model=ConversationOut)
def set_conversation_memory(
    conversation_id: str, payload: ConversationMemoryIn, user: CurrentUser, db: DbSession
) -> ConversationOut:
    """"Don't use memory in this chat": no recall, no capture, and its tasks are not kept for recall."""
    convo = db.get(Conversation, conversation_id)
    if not convo or convo.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    convo.memory_off = payload.memory_off
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


def _conflict(status: str, work_item_id: str | None, code: str, detail: str) -> JSONResponse:
    """The 409 body the web client reads: {code, work_item_id, status} (plus `detail`)."""
    return JSONResponse(
        status_code=409,
        content={"detail": detail, "code": code, "work_item_id": work_item_id, "status": status},
    )


def _still_working(db, convo: Conversation) -> JSONResponse | None:
    held = taskloop.running_item(db, convo.id)
    if held is None:
        return None
    return _conflict("running", held.id, "still_working", "AURA is still working on this conversation")


@router.post("", response_model=ChatOut)
def chat(payload: ChatIn, user: GuardedUser, db: DbSession):
    """Non-streaming turn. Use /stream for the live UI."""
    convo = _get_or_create(db, user, payload.conversation_id, payload.memory_off)
    busy = _still_working(db, convo)
    if busy is not None:
        return busy
    result = run_agent(db, user, convo, payload.message)
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
        work_item_id=result.work_item_id,
        work_status=result.work_status,
        preferences_noted=result.preferences_noted,
    )


def _sse(source: Iterator[str]) -> StreamingResponse:
    return StreamingResponse(
        source,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/continue")
def chat_continue(payload: ChatContinueIn, user: GuardedUser, db: DbSession):
    """Carry on a paused work item (after an approval, a denial, an expiry or a step budget).

    The lease is taken here, before the stream starts, so a second call (another tab, a retry)
    gets 409 and changes nothing."""
    item = db.get(WorkItem, payload.work_item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        token = taskloop.claim_for_resume(db, user, item.id)
    except taskloop.Busy as busy:
        return _conflict(busy.status, item.id, "not_ready", f"This task is {busy.status}, so it cannot be continued now")
    user_id, convo_id, item_id = user.id, item.conversation_id, item.id
    bind = db.get_bind()

    def event_source() -> Iterator[str]:
        yield f"data: {json.dumps({'type': 'start', 'conversation_id': convo_id})}\n\n"
        stream_db = SessionLocal(bind=bind)
        try:
            stream_user = stream_db.get(User, user_id)
            stream_convo = stream_db.get(Conversation, convo_id) if convo_id else None
            for event in stream_agent(stream_db, stream_user, stream_convo, None,
                                      work_item_id=item_id, token=token):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as exc:
            log.exception("Continuing work item %s failed", item_id)
            stream_db.rollback()
            yield f"data: {json.dumps({'type': 'error', 'message': _stream_error(exc)})}\n\n"
        finally:
            stream_db.close()
        yield "data: [DONE]\n\n"

    return _sse(event_source())


@router.post("/stop")
def chat_stop(payload: ChatContinueIn, user: GuardedUser, db: DbSession) -> dict:
    item = db.get(WorkItem, payload.work_item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="Task not found")
    return taskloop.stop(db, user, item)


@router.post("/stream")
def chat_stream(payload: ChatIn, user: GuardedUser, db: DbSession):
    convo = _get_or_create(db, user, payload.conversation_id, payload.memory_off)
    busy = _still_working(db, convo)
    if busy is not None:
        return busy
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
            ):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as exc:  # never leave the client hanging
            log.exception("Chat stream failed for conversation %s", convo_id)
            stream_db.rollback()
            yield f"data: {json.dumps({'type': 'error', 'message': _stream_error(exc)})}\n\n"
        finally:
            stream_db.close()
        yield "data: [DONE]\n\n"

    return _sse(event_source())
