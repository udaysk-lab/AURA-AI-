"""Schema, contract step: drop the 1.0.0 assistant columns nothing uses.

Revision ID: sc90_contract
Revises: tl90_contract
Create Date: 2026-11-17

IRREVERSIBLE. Only a restore from a backup brings the dropped data back.

The hatch flow and the growth counters of AURA 1.0.0 are gone. Revision 0002 gave these
columns server defaults so new code could insert rows without them; this step drops them,
each only if present:

  assistants.hatched_at, interactions, actions_taken, corrections, stage, onboarded

Plain ALTER on PostgreSQL inside the migration transaction; a table rebuild (batch mode) on
SQLite.
"""

from typing import Sequence, Union

from app.migration_ops import drop_column_if_present

revision: str = "sc90_contract"
down_revision: Union[str, None] = "tl90_contract"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = ("hatched_at", "interactions", "actions_taken", "corrections", "stage", "onboarded")


def upgrade() -> None:
    for column in COLUMNS:
        drop_column_if_present("assistants", column)


def downgrade() -> None:
    raise NotImplementedError(
        "sc90_contract cannot be undone: the dropped columns are gone. Restore the database from a backup."
    )
