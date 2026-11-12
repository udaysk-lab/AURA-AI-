"""Shared set-up for the permission gate tests: a private SQLite database per test."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app import models
from app.permissions import gate
from tests.schema import ensure_test_schema


def make_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'gate.db'}", poolclass=NullPool)
    ensure_test_schema(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def make_user(db, email="owner@example.com", level="conservative", mode=None, migrated=False):
    user = models.User(email=email, name="Owner")
    db.add(user)
    db.flush()
    row = models.UserSettings(user_id=user.id, autonomy_level=level)
    if mode:
        row.permission_mode = mode
    if migrated:
        row.extra = {"permissions_migrated": 1}
    db.add(row)
    db.commit()
    db.refresh(user)
    return user


def ctx(db, user, trigger="chat", **kw):
    return gate.build_context(db, user, trigger, **kw)


def decide(db, user, tool, args, trigger="chat", **kw):
    return gate.decide(db, user, tool, args, ctx(db, user, trigger, **kw))
