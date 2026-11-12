"""Task loop, expand step: work items, work steps, and the links to them.

Revision ID: tl01_work_items
Revises: pm02_permissions_data
Create Date: 2026-11-12

Purely additive (spec 20-taskloop-memory.md sections 2.1 to 2.3). New tables: work_items
(task memory: one row per assignment) and work_steps (what happened inside it). New
nullable columns `work_item_id` (foreign key work_items.id, SET NULL) on skill_runs,
activity_logs and usage_records. pending_actions already had an unconstrained
`work_item_id` (pm01); this revision gives it the foreign key, after clearing any value
that points at no work item (the column was never written before the loop existed), and
adds `tool_call_id` and `result`.

Nothing is dropped and no data is moved. Every create and add is guarded.
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
    sql_int,
    sql_json_empty_list,
    sql_json_empty_object,
    sql_str,
    sql_true,
    table_exists,
)

revision: str = "tl01_work_items"
down_revision: Union[str, None] = "pm02_permissions_data"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LINK_TABLES = ("skill_runs", "activity_logs", "usage_records", "pending_actions")


def _stamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _fk_name(table: str) -> str:
    return f"fk_{table}_work_item_id"


def _index_name(table: str) -> str:
    return f"ix_{table}_work_item_id"


def _fk_exists(table: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return any(fk.get("name") == _fk_name(table) or (
        fk["referred_table"] == "work_items" and fk["constrained_columns"] == ["work_item_id"]
    ) for fk in insp.get_foreign_keys(table))


def upgrade() -> None:
    create_table_if_missing(
        "work_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("origin_ref", sa.String(length=64), nullable=False, server_default=sql_str("")),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("task_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("wait_reason", sa.String(length=20), nullable=False, server_default=sql_str("")),
        sa.Column("plan", sa.JSON(), nullable=False, server_default=sql_json_empty_list()),
        sa.Column("systems_used", sa.JSON(), nullable=False, server_default=sql_json_empty_list()),
        sa.Column("tools_used", sa.JSON(), nullable=False, server_default=sql_json_empty_list()),
        sa.Column("summary", sa.Text(), nullable=False, server_default=sql_str("")),
        sa.Column("outcome", sa.String(length=300), nullable=False, server_default=sql_str("")),
        sa.Column("report", sa.Text(), nullable=False, server_default=sql_str("")),
        sa.Column("verification", sa.JSON(), nullable=False, server_default=sql_json_empty_object()),
        sa.Column("user_feedback", sa.JSON(), nullable=False, server_default=sql_json_empty_object()),
        sa.Column("recallable", sa.Boolean(), nullable=False, server_default=sql_true()),
        sa.Column("steps_total", sa.Integer(), nullable=False, server_default=sql_int(0)),
        sa.Column("segments", sa.Integer(), nullable=False, server_default=sql_int(0)),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default=sql_int(0)),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default=sql_int(0)),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=36), nullable=False, server_default=sql_str("")),
        sa.Column("loop_state", sa.JSON(), nullable=False, server_default=sql_json_empty_object()),
        sa.Column("recall_used", sa.JSON(), nullable=False, server_default=sql_json_empty_object()),
        sa.Column("embedding", sa.JSON(), nullable=False, server_default=sql_json_empty_list()),
        sa.Column("error", sa.Text(), nullable=False, server_default=sql_str("")),
        *_stamps(),
        sa.PrimaryKeyConstraint("id", name="pk_work_items"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="fk_work_items_user_id"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="SET NULL", name="fk_work_items_conversation_id"
        ),
    )
    create_index_if_missing("ix_work_items_user_id", "work_items", ["user_id"])
    create_index_if_missing("ix_work_items_conversation_id", "work_items", ["conversation_id"])
    create_index_if_missing("ix_work_items_task_type", "work_items", ["task_type"])
    create_index_if_missing("ix_work_items_status", "work_items", ["status"])
    create_index_if_missing("ix_work_items_user_status", "work_items", ["user_id", "status"])
    create_index_if_missing("ix_work_items_user_type", "work_items", ["user_id", "task_type"])
    create_index_if_missing("ix_work_items_status_ready", "work_items", ["status", "ready_at"])
    create_index_if_missing("ix_work_items_user_created", "work_items", ["user_id", "created_at"])

    create_table_if_missing(
        "work_steps",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("work_item_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False, server_default=sql_int(0)),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("tool_name", sa.String(length=120), nullable=False, server_default=sql_str("")),
        sa.Column("tool_call_id", sa.String(length=64), nullable=False, server_default=sql_str("")),
        sa.Column("system", sa.String(length=60), nullable=False, server_default=sql_str("")),
        sa.Column("arguments", sa.JSON(), nullable=False, server_default=sql_json_empty_object()),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=sql_str("")),
        sa.Column("detail", sa.JSON(), nullable=False, server_default=sql_json_empty_object()),
        *_stamps(),
        sa.PrimaryKeyConstraint("id", name="pk_work_steps"),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], ondelete="CASCADE", name="fk_work_steps_work_item_id"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="fk_work_steps_user_id"),
        sa.UniqueConstraint("work_item_id", "seq", name="uq_work_steps_item_seq"),
    )
    create_index_if_missing("ix_work_steps_work_item_id", "work_steps", ["work_item_id"])
    create_index_if_missing("ix_work_steps_user_id", "work_steps", ["user_id"])

    # --- links from existing tables ------------------------------------------------
    for table in LINK_TABLES:
        add_column_if_missing(table, sa.Column("work_item_id", sa.String(length=36), nullable=True))
    # A value in pending_actions.work_item_id can only be a leftover: no work item exists yet.
    op.get_bind().execute(sa.text("UPDATE pending_actions SET work_item_id = NULL WHERE work_item_id IS NOT NULL"))
    for table in LINK_TABLES:
        if not _fk_exists(table):
            with op.batch_alter_table(table) as batch:
                batch.create_foreign_key(_fk_name(table), "work_items", ["work_item_id"], ["id"], ondelete="SET NULL")
        create_index_if_missing(_index_name(table), table, ["work_item_id"])

    add_column_if_missing(
        "pending_actions",
        sa.Column("tool_call_id", sa.String(length=64), nullable=False, server_default=sql_str("")),
    )
    add_column_if_missing("pending_actions", sa.Column("result", sa.JSON(), nullable=True))


def downgrade() -> None:
    from sqlalchemy import inspect

    for table in LINK_TABLES:
        if not table_exists(table):
            continue
        insp = inspect(op.get_bind())
        with op.batch_alter_table(table) as batch:
            if _index_name(table) in {i["name"] for i in insp.get_indexes(table)}:
                batch.drop_index(_index_name(table))
            if any(fk.get("name") == _fk_name(table) for fk in insp.get_foreign_keys(table)):
                batch.drop_constraint(_fk_name(table), type_="foreignkey")
            # pending_actions.work_item_id is pm01's column: it stays, unconstrained and indexed.
            if table != "pending_actions" and column_exists(table, "work_item_id"):
                batch.drop_column("work_item_id")
        if table == "pending_actions":
            create_index_if_missing(_index_name(table), table, ["work_item_id"])
    for name in ("tool_call_id", "result"):
        if column_exists("pending_actions", name):
            with op.batch_alter_table("pending_actions") as batch:
                batch.drop_column(name)
    for table in ("work_steps", "work_items"):
        if table_exists(table):
            op.drop_table(table)
