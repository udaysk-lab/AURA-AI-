"""Revision pm01 (permissions, expand): additive columns and tables, guards, round trip.

SQLite only here; tests/test_migrations.py runs the whole chain (including the
pm01 downgrade) on PostgreSQL when AURA_TEST_POSTGRES_URL is set.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from alembic import command  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app import migrate, models  # noqa: E402,F401

PREV = "0002_legacy_defaults"
PM01 = "pm01_permissions"
NEW_TABLES = {"permission_rules", "device_folders", "device_app_grants", "device_app_blocks", "permission_events"}
NEW_COLUMNS = {
    "user_settings": {"permission_mode", "reviewer_boundaries"},
    "schedules": {"approval_mode"},
    "automation_rules": {"approval_mode"},
    "pending_actions": {
        "work_item_id", "expires_at", "origin", "origin_id", "card", "card_payload", "options",
        "always_user", "reason_code", "reason", "args_digest", "decision", "resolved_by", "rule_id",
    },
}


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'aura.db'}", poolclass=NullPool)
    yield eng
    eng.dispose()


def _run(eng, fn, revision):
    with eng.connect() as conn:
        fn(migrate.alembic_config(conn), revision)
        conn.commit()


def _tables(eng) -> set[str]:
    return set(sa.inspect(eng).get_table_names())


def _columns(eng, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(eng).get_columns(table)}


def test_pm01_follows_0002():
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(migrate.alembic_config())
    assert script.get_revision(PM01).down_revision == PREV


def test_pm01_adds_only(engine):
    _run(engine, command.upgrade, PREV)
    before = {t: _columns(engine, t) for t in NEW_COLUMNS}
    assert not (_tables(engine) & NEW_TABLES)
    _run(engine, command.upgrade, PM01)
    assert NEW_TABLES <= _tables(engine)
    for table, added in NEW_COLUMNS.items():
        after = _columns(engine, table)
        assert added <= after and before[table] <= after, table  # nothing dropped
        assert not (added & before[table]), table


def test_existing_rows_get_defaults(engine):
    _run(engine, command.upgrade, PREV)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email, name, avatar_url, timezone, password_hash, is_active, is_demo, created_at, updated_at)"
                          " VALUES ('u1', 'a@example.com', 'A', '', 'UTC', '', 1, 0, '2026-01-01', '2026-01-01')"))
        conn.execute(text("INSERT INTO pending_actions (id, user_id, tool_name, arguments, preview, status, skill_code, created_at, updated_at)"
                          " VALUES ('p1', 'u1', 'send_email', '{}', '', 'pending', '', '2026-01-01', '2026-01-01')"))
        conn.execute(text("INSERT INTO schedules (id, user_id, name, prompt, natural_language, cron, timezone, enabled, deliver_to, last_result, run_count, created_at, updated_at)"
                          " VALUES ('s1', 'u1', 'n', 'p', '', '0 8 * * *', 'UTC', 1, 'notification', '', 0, '2026-01-01', '2026-01-01')"))
    _run(engine, command.upgrade, PM01)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT origin, card, card_payload, options, always_user, reason, decision, expires_at, work_item_id"
                                " FROM pending_actions WHERE id = 'p1'")).one()
        assert row[0] == "chat" and row[1] == "tool" and row[4] in (0, False) and row[5] == "" and row[6] == ""
        assert row[7] is None and row[8] is None
        assert conn.execute(text("SELECT approval_mode FROM schedules")).scalar() == "ask"


def test_models_insert_without_new_columns_set(engine):
    """The ORM defaults and the server defaults agree: a new user lands in 'ask'."""
    from sqlalchemy.orm import Session

    _run(engine, command.upgrade, PM01)
    with Session(engine) as db:
        user = models.User(email="b@example.com", name="B")
        db.add(user)
        db.flush()
        settings_row = models.UserSettings(user_id=user.id)
        db.add(settings_row)
        db.add(models.PermissionRule(user_id=user.id, effect="allow", system="*"))
        db.add(models.PermissionEvent(user_id=user.id, seq=1, actor="system", kind="decision"))
        db.commit()
        assert settings_row.permission_mode == "ask" and settings_row.reviewer_boundaries == ""
        db.add(models.PermissionEvent(user_id=user.id, seq=1, actor="system", kind="decision"))
        with pytest.raises(sa.exc.IntegrityError):
            db.commit()


def test_guards_make_a_rerun_a_no_op(engine):
    _run(engine, command.upgrade, PM01)
    _run(engine, command.stamp, PREV)  # pretend the objects are there but the revision is not recorded
    _run(engine, command.upgrade, PM01)
    assert NEW_TABLES <= _tables(engine)


def test_downgrade_removes_exactly_what_was_added(engine):
    _run(engine, command.upgrade, PREV)
    before = {t: _columns(engine, t) for t in NEW_COLUMNS}
    tables_before = _tables(engine)
    _run(engine, command.upgrade, PM01)
    _run(engine, command.downgrade, PREV)
    assert _tables(engine) == tables_before
    assert {t: _columns(engine, t) for t in NEW_COLUMNS} == before
    _run(engine, command.upgrade, PM01)  # and it goes up again
    assert NEW_TABLES <= _tables(engine)
