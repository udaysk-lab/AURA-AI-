"""Alembic environment.

Two ways in:

* The app (app/migrate.py) hands over a connection that already holds a
  transaction (`config.attributes["connection"]`). Alembic then neither begins
  nor commits one, so the runner can stamp and upgrade as one unit, and the app's
  logging is left alone.
* The CLI (`alembic upgrade head`, `alembic check`, `alembic revision`) builds its
  own engine from DATABASE_URL, the one source of truth for where the database
  lives. Autogenerate compares against the same metadata the app uses, so a
  forgotten model shows up as a missing migration rather than a runtime error.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import settings
from app.db import Base
from app.db import connect_args as app_connect_args
from app.migration_ops import LEGACY_PENDING_DROP
from app import models  # noqa: F401  — registers every mapper before autogenerate

config = context.config
supplied_connection = config.attributes.get("connection")

# Only the CLI owns logging. Inside the app, fileConfig would reset its handlers.
if config.config_file_name is not None and supplied_connection is None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    """Keep `alembic check` clean while legacy columns await their contract.

    A table or column that is in the database, unknown to the models, and listed
    in LEGACY_PENDING_DROP is expected, not drift.
    """
    if reflected and compare_to is None:
        if type_ == "table" and (name, None) in LEGACY_PENDING_DROP:
            return False
        if type_ == "column" and (obj.table.name, name) in LEGACY_PENDING_DROP:
            return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        # SQLite can't ALTER most things in place; batch mode rewrites the
        # table instead so the same migrations run on both backends.
        render_as_batch=connection.dialect.name == "sqlite",
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    if supplied_connection is not None:
        _run(supplied_connection)
        return
    # Not set_main_option: a "%" in a password would trip its interpolation.
    connectable = create_engine(
        settings.database_url, poolclass=pool.NullPool, connect_args=app_connect_args
    )
    try:
        with connectable.connect() as connection:
            _run(connection)
            connection.commit()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
