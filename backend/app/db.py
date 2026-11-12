"""Database engine and session management."""

from collections.abc import Iterator

import logging

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings

log = logging.getLogger("aura.db")


def _is_transaction_pooler(url: str) -> bool:
    """Does this URL point at PgBouncer in transaction mode?

    Supabase serves its transaction pooler on port 6543, and both Supabase and
    Neon put "pooler" in the pooled hostname. Worth detecting because transaction
    pooling has one behaviour that breaks psycopg silently and intermittently:
    a connection is only yours for the length of a transaction, so a prepared
    statement created on one is likely gone — or worse, belongs to someone
    else's session — by the next query.

    psycopg 3 prepares any statement it has seen prepare_threshold (5) times.
    So this does not fail on the first request, or the tenth; it fails once a
    given query gets warm, with "prepared statement _pg3_N already exists".
    """
    lowered = url.lower()
    return ":6543" in lowered or "pooler." in lowered


connect_args: dict = {}
engine_kwargs: dict = {"pool_pre_ping": True}

if settings.is_sqlite:
    # FastAPI runs sync endpoints in a threadpool, so the connection can hop threads.
    connect_args["check_same_thread"] = False
elif _is_transaction_pooler(settings.database_url):
    # None disables psycopg's prepared statements entirely.
    connect_args["prepare_threshold"] = None
    # PgBouncer is already the pool. A second pool in front of it holds
    # connections open that the platform will kill between invocations, and
    # pool_pre_ping then pays a round trip to discover that on every request.
    engine_kwargs = {"poolclass": NullPool}
    log.info("Transaction pooler detected: prepared statements off, NullPool.")

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    echo=False,
    future=True,
    **engine_kwargs,
)

if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - infra glue
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
