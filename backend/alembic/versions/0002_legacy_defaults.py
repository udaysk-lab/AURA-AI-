"""Legacy defaults: let 2.0 code insert rows without the 1.0.0-only columns.

Revision ID: 0002_legacy_defaults
Revises: 0001_initial
Create Date: 2026-11-12

Expand step. The columns the 2.0 models stop writing keep NOT NULL and gain a
server default, so an INSERT that omits them succeeds and old code still reads
valid values from rows that new code creates. Nothing is dropped here; the
contract revisions drop them later (spec 30-migrations.md, section 9).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.migration_ops import (
    sql_int,
    sql_json_empty_list,
    sql_now,
    sql_str,
    sql_true,
)

revision: str = "0002_legacy_defaults"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _defaults() -> dict[str, list[tuple[str, sa.types.TypeEngine, object]]]:
    """table -> [(column, 0001 type, server default)], evaluated per dialect."""
    return {
        "assistants": [
            ("hatched_at", sa.DateTime(timezone=True), sql_now()),
            ("interactions", sa.Integer(), sql_int(0)),
            ("actions_taken", sa.Integer(), sql_int(0)),
            ("corrections", sa.Integer(), sql_int(0)),
            ("stage", sa.String(length=30), sql_str("stranger")),
            # True on purpose: a user created by 2.0 code who reaches a 1.0.0
            # instance during the rollout must not be sent into the hatch flow.
            ("onboarded", sa.Boolean(), sql_true()),
        ],
        "user_settings": [
            ("autonomy_level", sa.String(length=20), sql_str("conservative")),
            ("tone", sa.String(length=50), sql_str("concise-professional")),
        ],
        "user_skills": [
            ("learned_notes", sa.JSON(), sql_json_empty_list()),
        ],
        "automation_rules": [
            ("requires_confirmation", sa.Boolean(), sql_true()),
        ],
    }


def upgrade() -> None:
    for table, columns in _defaults().items():
        with op.batch_alter_table(table) as batch_op:
            for name, type_, default in columns:
                batch_op.alter_column(
                    name,
                    existing_type=type_,
                    existing_nullable=False,
                    server_default=default,
                )


def downgrade() -> None:
    for table, columns in _defaults().items():
        with op.batch_alter_table(table) as batch_op:
            for name, type_, _default in columns:
                batch_op.alter_column(
                    name,
                    existing_type=type_,
                    existing_nullable=False,
                    server_default=None,
                )
