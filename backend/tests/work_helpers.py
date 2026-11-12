"""Shared set-up for the task-memory and preference tests: a private SQLite database per test."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

from datetime import timedelta
from types import SimpleNamespace

from app import models
from app.llm import LLMResponse
from app.models import utcnow
from tests.perm_helpers import make_session, make_user  # noqa: F401  (re-exported)


def conversation(db, user, **kw) -> models.Conversation:
    convo = models.Conversation(user_id=user.id, title="t", **kw)
    db.add(convo)
    db.commit()
    return convo


def finished_item(db, user, *, title, instruction="", task_type="other", status="done", summary="",
                  outcome="", age=timedelta(hours=1), recallable=True, convo=None, feedback=None,
                  tools=None, embedding=None) -> models.WorkItem:
    """A finished work item with the given words, as the loop would leave it."""
    from app.services import workmemory

    when = utcnow() - age
    item = models.WorkItem(
        user_id=user.id, conversation_id=convo.id if convo else None, trigger="chat",
        instruction=instruction or title, title=title, task_type=task_type, status=status,
        summary=summary, outcome=outcome or title, report=outcome or title, recallable=recallable,
        started_at=when, finished_at=when, tools_used=list(tools or []), user_feedback=feedback or {},
        embedding=embedding or [], verification={"status": "none", "checks": []},
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    assert workmemory.index_text(item)
    return item


def assistant_reply(db, convo, item) -> models.Message:
    msg = models.Message(conversation_id=convo.id, role="assistant", content="done",
                         meta={"work_item_id": item.id, "work_status": item.status})
    db.add(msg)
    db.commit()
    return msg


class FakeProvider:
    """A provider that answers `complete` from a list and cannot embed (like the mock)."""

    def __init__(self, replies=(), name="scripted", vectors=None):
        self.name = name
        self.replies = list(replies)
        self.calls: list[list[dict]] = []
        self.vectors = vectors  # callable(text) -> list[float] | None

    def complete(self, messages, tools=None, **kw):
        self.calls.append(messages)
        if not self.replies:
            raise RuntimeError("no reply scripted")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(text=reply)

    def embed(self, texts):
        if self.vectors is None:
            return []
        return [self.vectors(t) for t in texts]
