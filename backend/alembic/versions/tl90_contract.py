"""Task loop and memory, contract step: drop what the preference store replaced.

Revision ID: tl90_contract
Revises: pm90_contract
Create Date: 2026-11-17

IRREVERSIBLE. Only a restore from a backup brings the dropped data back.

Revision tl02 copied three old stores into `preferences`: memories of kind preference, style
and habit, every entry of user_skills.learned_notes, and a user_settings.tone that was not the
default. Nothing reads the old copies any more. This step removes them, each only if present:

  user_settings.tone              (column)
  user_skills.learned_notes       (column)
  memories rows of kind preference, style or habit

Plain ALTER and DELETE on PostgreSQL inside the migration transaction; a table rebuild
(batch mode) on SQLite.
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.migration_ops import drop_column_if_present, table_exists

revision: str = "tl90_contract"
down_revision: Union[str, None] = "pm90_contract"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

OLD_KINDS = ("preference", "style", "habit")  # the kinds tl02 copied


def upgrade() -> None:
    if table_exists("memories"):
        memories = sa.table("memories", sa.column("kind"))
        result = op.get_bind().execute(sa.delete(memories).where(memories.c.kind.in_(OLD_KINDS)))
        log.info("tl90: deleted %s memory row(s) of the old kinds", result.rowcount)
    drop_column_if_present("user_settings", "tone")
    drop_column_if_present("user_skills", "learned_notes")


def downgrade() -> None:
    raise NotImplementedError(
        "tl90_contract cannot be undone: the deleted rows and dropped columns are gone. "
        "Restore the database from a backup."
    )
