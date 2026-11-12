"""Helpers for migration scripts, and the list of legacy columns awaiting a drop.

Migration scripts may import this module and nothing else from `app` (spec 6.4).
It needs alembic only when a helper runs, so importing it is cheap.

Server defaults are rendered per dialect here, so a revision never spells
`now()` or `'[]'::json` itself and works on both Postgres and SQLite.
"""

from __future__ import annotations

import sqlalchemy as sa

# (table, column) pairs that exist in the database but whose model attribute is
# gone, or is about to go. (table, None) means the whole table. Each entry only
# takes effect once the attribute is removed: alembic/env.py's include_object
# ignores a database object the models do not know. Contract revisions drop
# exactly these and leave the set empty.
LEGACY_PENDING_DROP: set[tuple[str, str | None]] = {
    ("assistants", "hatched_at"),
    ("assistants", "interactions"),
    ("assistants", "actions_taken"),
    ("assistants", "corrections"),
    ("assistants", "stage"),
    ("assistants", "onboarded"),
    ("user_settings", "autonomy_level"),
    ("user_settings", "tone"),
    ("user_skills", "learned_notes"),
    ("automation_rules", "requires_confirmation"),
    ("approval_grants", None),
}


# ---------------------------------------------------------------------------
# Dialect-aware server defaults
# ---------------------------------------------------------------------------


def _dialect() -> str:
    from alembic import op

    return op.get_context().dialect.name


def sql_true() -> sa.TextClause:
    return sa.text("true" if _dialect() == "postgresql" else "1")


def sql_false() -> sa.TextClause:
    return sa.text("false" if _dialect() == "postgresql" else "0")


def sql_now() -> sa.TextClause:
    return sa.text("now()" if _dialect() == "postgresql" else "CURRENT_TIMESTAMP")


def sql_json_empty_list() -> sa.TextClause:
    return sa.text("'[]'::json" if _dialect() == "postgresql" else "'[]'")


def sql_json_empty_object() -> sa.TextClause:
    return sa.text("'{}'::json" if _dialect() == "postgresql" else "'{}'")


def sql_str(value: str) -> sa.TextClause:
    return sa.text("'" + value.replace("'", "''") + "'")


def sql_int(value: int) -> sa.TextClause:
    return sa.text(str(int(value)))


# ---------------------------------------------------------------------------
# Guards: every create/add/drop tolerates an object that is already (not) there
# ---------------------------------------------------------------------------


def _inspector() -> sa.Inspector:
    from alembic import op

    return sa.inspect(op.get_bind())


def table_exists(name: str) -> bool:
    return _inspector().has_table(name)


def column_exists(table: str, column: str) -> bool:
    insp = _inspector()
    return insp.has_table(table) and column in {c["name"] for c in insp.get_columns(table)}


def create_table_if_missing(name: str, *args, **kwargs) -> bool:
    from alembic import op

    if table_exists(name):
        return False
    op.create_table(name, *args, **kwargs)
    return True


def add_column_if_missing(table: str, column: sa.Column) -> bool:
    from alembic import op

    if column_exists(table, column.name):
        return False
    with op.batch_alter_table(table) as batch:
        batch.add_column(column)
    return True


def create_index_if_missing(name: str, table: str, columns: list[str], unique: bool = False) -> bool:
    from alembic import op

    insp = _inspector()
    if insp.has_table(table) and name in {i["name"] for i in insp.get_indexes(table)}:
        return False
    with op.batch_alter_table(table) as batch:
        batch.create_index(name, columns, unique=unique)
    return True


def drop_column_if_present(table: str, column: str) -> bool:
    from alembic import op

    if not column_exists(table, column):
        return False
    with op.batch_alter_table(table) as batch:
        batch.drop_column(column)
    return True


def drop_table_if_present(name: str) -> bool:
    from alembic import op

    if not table_exists(name):
        return False
    op.drop_table(name)
    return True
