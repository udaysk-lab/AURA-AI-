"""Cold-start cost of the schema check in init_db.

Behind a transaction pooler the engine uses NullPool, so every connection
checkout is a brand-new TCP + TLS + auth handshake. Measured from Vercel iad1
to Supabase Tokyo, one handshake plus one query took about 1.3 s. The additive
migration check used an engine-bound inspector, which checks out a new
connection per call: 29 handshakes on every cold start.

These tests count real DBAPI connections on a NullPool engine.

Run from backend/:  pytest -q tests/test_cold_start.py
"""

from __future__ import annotations

import os

# Must match test_smoke.py exactly — see the note in test_password_auth.py.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("ALLOW_DEMO_LOGIN", "true")

import pytest  # noqa: E402
from sqlalchemy import create_engine, event, inspect, text  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

import app.db as db_module  # noqa: E402
import app.models  # noqa: E402,F401  (registers every table on Base.metadata)


@pytest.fixture()
def nullpool_engine(tmp_path, monkeypatch):
    """A NullPool engine like production's, with the full schema, counting
    every new DBAPI connection it opens."""
    engine = create_engine(f"sqlite:///{tmp_path / 'coldstart.db'}", poolclass=NullPool)
    db_module.Base.metadata.create_all(engine)

    connects: list[int] = []
    event.listen(engine, "connect", lambda *_: connects.append(1))
    monkeypatch.setattr(db_module, "engine", engine)
    yield engine, connects
    engine.dispose()


def test_schema_check_opens_one_connection_not_one_per_table(nullpool_engine):
    engine, connects = nullpool_engine
    table_count = len(db_module.Base.metadata.sorted_tables)
    assert table_count > 20  # the point of the test: many tables, one handshake

    db_module.apply_additive_migrations()

    assert len(connects) == 1, (
        f"{len(connects)} connections for {table_count} tables — each one is a "
        "full handshake to the database on a cold start"
    )


def test_a_missing_column_is_still_added(nullpool_engine):
    engine, connects = nullpool_engine
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE "users" DROP COLUMN "last_login_at"'))
    assert "last_login_at" not in {c["name"] for c in inspect(engine).get_columns("users")}
    connects.clear()

    db_module.apply_additive_migrations()
    opened = len(connects)

    assert "last_login_at" in {c["name"] for c in inspect(engine).get_columns("users")}
    # One connection to inspect everything, one for the ALTER.
    assert opened == 2


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
