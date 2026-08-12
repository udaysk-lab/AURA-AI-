"""Database engine and session management."""

from collections.abc import Iterator

import logging

from sqlalchemy import JSON, create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

log = logging.getLogger("aura.db")

connect_args: dict = {}
if settings.is_sqlite:
    # FastAPI runs sync endpoints in a threadpool, so the connection can hop threads.
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
    echo=False,
    future=True,
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

    Base.metadata.create_all(bind=engine)
    apply_additive_migrations()
