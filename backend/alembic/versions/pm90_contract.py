"""Permissions, contract step: drop what the permission mode replaced.

Revision ID: pm90_contract
Revises: tl02_preferences
Create Date: 2026-11-17

IRREVERSIBLE. Only a restore from a backup brings the dropped data back.

First the 1.0 trust settings are mapped once more, for every user the pm02 step has not
marked yet. pm02 ran minutes or hours before this step on a live site, and a 1.0.0 instance
that was still serving in between may have created a user or saved a trust level that pm02
never saw. The mapping is the same code pm02 ran (it is loaded from pm02's file), it skips a
user that is already marked, and it inserts a rule only if it is absent, so running it again
changes nothing for anyone who was already handled.

Then it drops, each only if present:

  user_settings.autonomy_level           replaced by user_settings.permission_mode
  automation_rules.requires_confirmation replaced by automation_rules.approval_mode
  approval_grants (table)                replaced by permission_rules

Plain ALTER and DROP on PostgreSQL inside the migration transaction; a table rebuild
(batch mode) on SQLite.
"""

import importlib.util
import logging
from pathlib import Path
from typing import Sequence, Union

from alembic import op

from app.migration_ops import column_exists, drop_column_if_present, drop_table_if_present, install_env, table_exists

revision: str = "pm90_contract"
down_revision: Union[str, None] = "tl02_preferences"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

_PM02_FILE = "pm02_permissions_data.py"


def _load_pm02():
    """The pm02 module, loaded from its file next to this one (a revision cannot import another)."""
    path = Path(__file__).resolve().with_name(_PM02_FILE)
    spec = importlib.util.spec_from_file_location("pm02_permissions_data_for_pm90", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _map_unmarked_users() -> None:
    needed = (
        column_exists("user_settings", "autonomy_level")
        and column_exists("user_settings", "permission_mode")
        and column_exists("user_settings", "extra")
        and table_exists("approval_grants")
        and table_exists("permission_rules")
    )
    if not needed:
        log.info("pm90: nothing to map (a 1.0 trust setting or a 2.0 table is missing)")
        return
    done = _load_pm02().map_legacy(op.get_bind(), install_env())
    log.info("pm90: mapped the 1.0 permission settings of %d more user(s)", len(done))


def upgrade() -> None:
    _map_unmarked_users()
    drop_column_if_present("user_settings", "autonomy_level")
    drop_column_if_present("automation_rules", "requires_confirmation")
    drop_table_if_present("approval_grants")


def downgrade() -> None:
    raise NotImplementedError(
        "pm90_contract cannot be undone: the dropped columns and table are gone. Restore the database from a backup."
    )
