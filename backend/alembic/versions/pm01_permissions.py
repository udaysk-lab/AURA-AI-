"""Permissions, expand step: modes, rules, approval cards, local access, event log.

Revision ID: pm01_permissions
Revises: 0002_legacy_defaults
Create Date: 2026-11-12

Purely additive (spec 10-permissions.md section 2). New tables: permission_rules,
device_folders, device_app_grants, device_app_blocks, permission_events. New
columns: user_settings (permission_mode, reviewer_boundaries), pending_actions
(approval-card fields), schedules and automation_rules (approval_mode).

Nothing is dropped and no data is moved; `autonomy_level`, `approval_grants` and
`requires_confirmation` stay until the contract revision pm90. The data
migration pm02 maps the old trust tiers onto the new rows.

pending_actions.work_item_id carries no foreign key here because the work_items
table belongs to the task-loop revision (tl01); that revision may add the
constraint. Every create and add is guarded, so the order of pm01 and tl01 does
not matter.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.migration_ops import (
    add_column_if_missing,
    column_exists,
    create_index_if_missing,
    create_table_if_missing,
    sql_false,
    sql_json_empty_list,
    sql_json_empty_object,
    sql_str,
    table_exists,
)

revision: str = "pm01_permissions"
down_revision: Union[str, None] = "0002_legacy_defaults"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _id() -> sa.Column:
    return sa.Column("id", sa.String(length=36), nullable=False)


def _user_fk() -> sa.Column:
    return sa.Column("user_id", sa.String(length=36), nullable=False)


def _stamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _user_cascade(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name=f"fk_{table}_user_id")


def _added_columns() -> list[tuple[str, sa.Column]]:
    """(table, column) for every column this revision adds to an existing table."""
    return [
        ("user_settings", sa.Column("permission_mode", sa.String(length=8), nullable=False, server_default=sql_str("ask"))),
        ("user_settings", sa.Column("reviewer_boundaries", sa.Text(), nullable=False, server_default=sql_str(""))),
        ("schedules", sa.Column("approval_mode", sa.String(length=8), nullable=False, server_default=sql_str("ask"))),
        ("automation_rules", sa.Column("approval_mode", sa.String(length=8), nullable=False, server_default=sql_str("ask"))),
        ("pending_actions", sa.Column("work_item_id", sa.String(length=36), nullable=True)),
        ("pending_actions", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)),
        ("pending_actions", sa.Column("origin", sa.String(length=20), nullable=False, server_default=sql_str("chat"))),
        ("pending_actions", sa.Column("origin_id", sa.String(length=36), nullable=False, server_default=sql_str(""))),
        ("pending_actions", sa.Column("card", sa.String(length=20), nullable=False, server_default=sql_str("tool"))),
        ("pending_actions", sa.Column("card_payload", sa.JSON(), nullable=False, server_default=sql_json_empty_object())),
        ("pending_actions", sa.Column("options", sa.JSON(), nullable=False, server_default=sql_json_empty_list())),
        ("pending_actions", sa.Column("always_user", sa.Boolean(), nullable=False, server_default=sql_false())),
        ("pending_actions", sa.Column("reason_code", sa.String(length=40), nullable=False, server_default=sql_str(""))),
        ("pending_actions", sa.Column("reason", sa.Text(), nullable=False, server_default=sql_str(""))),
        ("pending_actions", sa.Column("args_digest", sa.String(length=64), nullable=False, server_default=sql_str(""))),
        ("pending_actions", sa.Column("decision", sa.String(length=10), nullable=False, server_default=sql_str(""))),
        ("pending_actions", sa.Column("resolved_by", sa.String(length=40), nullable=False, server_default=sql_str(""))),
        ("pending_actions", sa.Column("rule_id", sa.String(length=36), nullable=True)),
    ]


# (index name, table, columns), created after the columns/tables exist.
_INDEXES: list[tuple[str, str, list[str]]] = [
    ("ix_pending_actions_work_item_id", "pending_actions", ["work_item_id"]),
    ("ix_pending_actions_expires_at", "pending_actions", ["expires_at"]),
    ("ix_pa_user_status", "pending_actions", ["user_id", "status"]),
]


def upgrade() -> None:
    # --- new tables --------------------------------------------------------
    create_table_if_missing(
        "permission_rules",
        _id(),
        _user_fk(),
        sa.Column("effect", sa.String(length=8), nullable=False),
        sa.Column("system", sa.String(length=60), nullable=False),
        sa.Column("tool", sa.String(length=120), nullable=True),
        sa.Column("target_kind", sa.String(length=12), nullable=False, server_default=sql_str("")),
        sa.Column("pattern", sa.String(length=1024), nullable=False, server_default=sql_str("")),
        sa.Column("applies_to", sa.String(length=80), nullable=False, server_default=sql_str("")),
        sa.Column("source", sa.String(length=12), nullable=False, server_default=sql_str("user")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=False, server_default=sql_str("")),
        *_stamps(),
        sa.PrimaryKeyConstraint("id", name="pk_permission_rules"),
        _user_cascade("permission_rules"),
    )
    create_index_if_missing("ix_prules_user_tool", "permission_rules", ["user_id", "tool"])
    create_index_if_missing("ix_prules_user_system", "permission_rules", ["user_id", "system"])

    create_table_if_missing(
        "device_folders",
        _id(),
        _user_fk(),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("path_key", sa.String(length=64), nullable=False),
        sa.Column("can_delete", sa.Boolean(), nullable=False, server_default=sql_false()),
        sa.Column("source", sa.String(length=12), nullable=False, server_default=sql_str("user")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        sa.PrimaryKeyConstraint("id", name="pk_device_folders"),
        _user_cascade("device_folders"),
        sa.UniqueConstraint("user_id", "path_key", name="uq_device_folders_user_key"),
    )

    create_table_if_missing(
        "device_app_grants",
        _id(),
        _user_fk(),
        sa.Column("app_id", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False, server_default=sql_str("")),
        sa.Column("tier", sa.String(length=8), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        sa.PrimaryKeyConstraint("id", name="pk_device_app_grants"),
        _user_cascade("device_app_grants"),
        sa.UniqueConstraint("user_id", "app_id", name="uq_device_app_grants_user_app"),
    )

    create_table_if_missing(
        "device_app_blocks",
        _id(),
        _user_fk(),
        sa.Column("app_id", sa.String(length=120), nullable=False),
        sa.Column("match", sa.String(length=8), nullable=False, server_default=sql_str("app")),
        sa.Column("source", sa.String(length=12), nullable=False, server_default=sql_str("user")),
        *_stamps(),
        sa.PrimaryKeyConstraint("id", name="pk_device_app_blocks"),
        _user_cascade("device_app_blocks"),
        sa.UniqueConstraint("user_id", "app_id", name="uq_device_app_blocks_user_app"),
    )

    create_table_if_missing(
        "permission_events",
        _id(),
        _user_fk(),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(length=12), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("tool", sa.String(length=120), nullable=False, server_default=sql_str("")),
        sa.Column("target", sa.String(length=500), nullable=False, server_default=sql_str("")),
        sa.Column("outcome", sa.String(length=8), nullable=False, server_default=sql_str("")),
        sa.Column("reason_code", sa.String(length=40), nullable=False, server_default=sql_str("")),
        sa.Column("run_key", sa.String(length=36), nullable=False, server_default=sql_str("")),
        sa.Column("detail", sa.JSON(), nullable=False, server_default=sql_json_empty_object()),
        sa.Column("prev_hash", sa.String(length=64), nullable=False, server_default=sql_str("")),
        sa.Column("hash", sa.String(length=64), nullable=False, server_default=sql_str("")),
        sa.Column("mac", sa.String(length=64), nullable=False, server_default=sql_str("")),
        *_stamps(),
        sa.PrimaryKeyConstraint("id", name="pk_permission_events"),
        _user_cascade("permission_events"),
        sa.UniqueConstraint("user_id", "seq", name="uq_permission_events_user_seq"),
    )
    create_index_if_missing("ix_permission_events_user_run", "permission_events", ["user_id", "run_key"])

    # --- new columns on existing tables --------------------------------------
    for table, column in _added_columns():
        add_column_if_missing(table, column)
    for name, table, columns in _INDEXES:
        create_index_if_missing(name, table, columns)


def downgrade() -> None:
    from sqlalchemy import inspect

    # Indexes first (batch mode on SQLite rebuilds the table around them).
    for name, table, _columns in _INDEXES:
        if table_exists(table) and name in {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}:
            with op.batch_alter_table(table) as batch:
                batch.drop_index(name)

    by_table: dict[str, list[str]] = {}
    for table, column in _added_columns():
        by_table.setdefault(table, []).append(column.name)
    for table, names in by_table.items():
        present = [n for n in names if column_exists(table, n)]
        if present:
            with op.batch_alter_table(table) as batch:
                for n in present:
                    batch.drop_column(n)

    for table in ("permission_events", "device_app_blocks", "device_app_grants", "device_folders", "permission_rules"):
        if table_exists(table):
            op.drop_table(table)
