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
    if migrated:
        legacy_folders(db, user)
    return user


def connect(db, user, path, can_delete=True, source="user"):
    """A connected folder, as the user (or the data migration) would have made it."""
    from app.permissions import access

    real = os.path.realpath(str(path))
    row = models.DeviceFolder(user_id=user.id, path=real, path_key=access.path_key(real), can_delete=can_delete,
                              source=source)
    db.add(row)
    db.commit()
    return row


def legacy_folders(db, user):
    """What the data migration does with DEVICE_ALLOWED_ROOTS: each root becomes a folder (spec 12)."""
    from app.config import settings

    if not (settings.device_control_enabled and settings.device_allowed_roots.strip()):
        return
    for root in settings.device_allowed_root_list:
        connect(db, user, root, can_delete=True, source="legacy")


_PM02 = None


def pm02():
    """The data migration module (alembic/versions/pm02_permissions_data.py), loaded from its file."""
    global _PM02
    if _PM02 is None:
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "pm02_permissions_data.py"
        spec = importlib.util.spec_from_file_location("pm02_permissions_data_under_test", path)
        _PM02 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_PM02)
    return _PM02


def apply_legacy(db, user, tier=None, *, env=None, reset=True):
    """Run the pm02 mapping for one user, as the upgrade would, with this process's settings as the
    install (or `env`). `tier` sets the 1.0 level first. `reset` clears an earlier mapping so a test
    can map the same user again under another tier or setting."""
    import sqlalchemy as sa

    from app.migration_ops import install_env

    db.flush()
    row = db.scalars(sa.select(models.UserSettings).where(models.UserSettings.user_id == user.id)).first()
    if row is None:
        row = models.UserSettings(user_id=user.id)
        db.add(row)
        db.flush()
    if reset:
        db.execute(sa.delete(models.PermissionRule).where(
            models.PermissionRule.user_id == user.id, models.PermissionRule.source == "legacy"))
        db.execute(sa.delete(models.DeviceFolder).where(
            models.DeviceFolder.user_id == user.id, models.DeviceFolder.source == "legacy"))
        extra = dict(row.extra or {})
        for key in ("permissions_migrated", "legacy_events_pending"):
            extra.pop(key, None)
        row.extra = extra
        row.permission_mode = "ask"
    if tier:
        row.autonomy_level = tier
    db.commit()
    summary = pm02().map_legacy(db.connection(), env if env is not None else install_env(), only_user_id=user.id)
    db.commit()
    db.expire_all()
    return summary.get(user.id)


def ctx(db, user, trigger="chat", **kw):
    return gate.build_context(db, user, trigger, **kw)


def decide(db, user, tool, args, trigger="chat", **kw):
    return gate.decide(db, user, tool, args, ctx(db, user, trigger, **kw))
