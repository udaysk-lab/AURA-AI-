"""Cold-start cost of the schema check in ensure_schema.

Behind a transaction pooler the engine uses NullPool, so every connection
checkout is a brand-new TCP + TLS + auth handshake. Measured from Vercel iad1
to Supabase Tokyo, one handshake plus one query took about 1.3 s. The old
create_all + add-missing-columns check opened one connection per table: 29
handshakes on every cold start. At the migration head the runner needs one
connection and one query, and once confirmed in a process, none.

These tests count real DBAPI connections and statements on a NullPool engine.

Run from backend/:  pytest -q tests/test_cold_start.py
"""

from __future__ import annotations

import os

# Must match test_smoke.py exactly — see the note in test_password_auth.py.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("ALLOW_DEMO_LOGIN", "true")

import pytest  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

import app.db as db_module  # noqa: E402
import app.models  # noqa: E402,F401  (registers every table on Base.metadata)
from app import migrate  # noqa: E402
from tests.schema import ensure_test_schema  # noqa: E402


@pytest.fixture()
def nullpool_engine(tmp_path, monkeypatch):
    """A NullPool engine like production's, at the migration head, counting
    every new DBAPI connection it opens and every statement it runs."""
    engine = create_engine(f"sqlite:///{tmp_path / 'coldstart.db'}", poolclass=NullPool)
    ensure_test_schema(engine)

    connects: list[int] = []
    statements: list[str] = []
    event.listen(engine, "connect", lambda *_: connects.append(1))
    event.listen(
        engine, "before_cursor_execute", lambda conn, cur, stmt, *a: statements.append(stmt)
    )
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(migrate, "_CACHE", {})
    yield engine, connects, statements
    engine.dispose()


def test_head_check_is_one_connection_one_query(nullpool_engine):
    engine, connects, statements = nullpool_engine
    table_count = len(db_module.Base.metadata.sorted_tables)
    assert table_count > 20  # the point of the test: many tables, one handshake

    report = migrate.upgrade_to_head(engine)

    assert report.state_before == migrate.State.AT_HEAD and report.ok
    assert len(connects) == 1, (
        f"{len(connects)} connections for {table_count} tables — each one is a "
        "full handshake to the database on a cold start"
    )
    assert len(statements) == 1 and "alembic_version" in statements[0], statements


def test_cached_check_opens_no_connection(nullpool_engine):
    engine, connects, statements = nullpool_engine
    migrate.upgrade_to_head(engine)
    connects.clear()
    statements.clear()

    report = migrate.upgrade_to_head(engine)

    assert report.cached
    assert connects == [] and statements == []


def test_functions_are_pinned_next_to_the_database():
    """The Supabase project is in ap-northeast-1 (Tokyo). Vercel's default
    function region is iad1 (Washington), where every request paid about 1.3 s
    of trans-Pacific handshake before its first query. See DEPLOY.md."""
    import json
    from pathlib import Path

    config = json.loads(
        (Path(__file__).resolve().parents[2] / "vercel.json").read_text(encoding="utf-8")
    )
    assert config.get("regions") == ["hnd1"]
