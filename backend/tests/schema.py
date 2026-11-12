"""Test-schema helpers.

The suite builds its schema with create_all for speed, then marks the database
as being at the migration head so the app's start-up check takes its one-query
fast path. Parity between the models and the migrations is enforced separately
by tests/test_migrations.py::test_models_match_migrations and `alembic check`.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app import migrate
from app.db import Base


def ensure_test_schema(engine: Engine) -> None:
    """create_all, then alembic_version holding exactly one row: the head."""
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alembic_version ("
                "version_num VARCHAR(32) NOT NULL, "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            )
        )
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
            {"head": migrate.EXPECTED_HEAD},
        )


def drop_test_schema(engine: Engine) -> None:
    Base.metadata.drop_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    migrate._CACHE.clear()  # the next lifespan must look at the real database
