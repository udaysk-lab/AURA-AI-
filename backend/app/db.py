"""Database engine and session management."""

from collections.abc import Iterator

import logging

from sqlalchemy import JSON, create_engine, event, inspect, text
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


def _default_literal(column) -> str | None:
    """A SQL literal for a column's Python-side default, if we can express one.

    Needed because SQLite refuses to ADD a NOT NULL column to a populated table
    without a default.
    """
    if isinstance(column.type, JSON):
        factory = getattr(column.default, "arg", None)
        return "'{}'" if factory is dict else "'[]'"

    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    value = default.arg
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return None


def apply_additive_migrations() -> None:
    """Add columns that exist on the models but not yet in the database.

    Local-dev convenience so an existing aura.db survives a model change rather
    than blowing up with 'no such column'. Deliberately additive only — it never
    drops, renames or retypes anything. Once you have data you care about, use
    Alembic instead and delete this call.
    """
    inspector = inspect(engine)
    present = set(inspector.get_table_names())

    for table in Base.metadata.sorted_tables:
        if table.name not in present:
            continue  # create_all just made it
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        missing = [c for c in table.columns if c.name not in existing]
        if not missing:
            continue

        for column in missing:
            type_sql = column.type.compile(dialect=engine.dialect)
            literal = _default_literal(column)
            clause = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {type_sql}'
            if literal is not None:
                clause += f" DEFAULT {literal}"
            try:
                with engine.begin() as conn:
                    conn.execute(text(clause))
                log.info("Added column %s.%s", table.name, column.name)
            except Exception as exc:
                log.warning("Could not add %s.%s: %s", table.name, column.name, exc)


def init_db() -> None:
    """Create tables and patch in any new columns.

    Skipped entirely when AUTO_CREATE_SCHEMA=false, which is what you want once
    Alembic owns the schema — two things racing to define your tables is how you
    end up with a column that exists in staging and not production.
    """
    from app import models  # noqa: F401  (registers mappers)

    if not settings.auto_create_schema:
        log.info("AUTO_CREATE_SCHEMA=false — expecting Alembic to have run.")
        return

    try:
        Base.metadata.create_all(bind=engine)
        apply_additive_migrations()
    except Exception as exc:
        # Raising here kills the process during startup, and on a serverless
        # host that surfaces as an opaque FUNCTION_INVOCATION_FAILED with no
        # clue as to why. The overwhelmingly common cause is the SQLite default
        # on a read-only filesystem: nothing about the app is broken, the
        # database just cannot live there. Log something actionable and let the
        # app boot, so /setup and /api/health/preflight can be reached and can
        # say what is wrong. Requests that genuinely need the database will
        # still fail, and loudly.
        log.error(
            "Could not initialise the database schema: %s\n"
            "DATABASE_URL is %r.%s",
            exc,
            settings.database_url,
            (
                "\nThis is a file-backed SQLite database. Serverless platforms"
                " (Vercel, Lambda) have a read-only filesystem apart from /tmp,"
                " and /tmp is wiped between invocations — so SQLite cannot work"
                " there even when it appears to. Set DATABASE_URL to a Postgres"
                " connection string."
                if settings.database_url.startswith("sqlite")
                else "\nCheck the host is reachable and the credentials are right."
            ),
        )
