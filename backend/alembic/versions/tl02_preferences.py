"""Preference memory, expand step: the preferences table, the two switches, the data copy.

Revision ID: tl02_preferences
Revises: tl01_work_items
Create Date: 2026-11-12

Spec 20-taskloop-memory.md sections 2.4, 2.5 and 10.6. Adds the `preferences` table,
`user_settings.memory_paused` and `conversations.memory_off`, then copies what the 1.0
release kept in three places into `preferences` (origin 'migrated'):

  1. memories of kind preference / style / habit -> task type general
  2. every entry of user_skills.learned_notes -> the task type of its skill code (frozen map
     below); skipped when the user already has a preference with the same text, because the
     1.0 code wrote each correction to both stores
  3. user_settings.tone, when it is not the default -> "Tone: <tone>", task type general

The old rows stay (tl90 removes them), so a rollback loses nothing. Idempotent: a text the
user already has as a preference is never inserted twice. Plain SQL Core; nothing from the
app is imported but `app.migration_ops` (spec 30 section 6.4).
"""

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
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
    sql_str,
    sql_true,
    table_exists,
)

revision: str = "tl02_preferences"
down_revision: Union[str, None] = "tl01_work_items"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

OLD_KINDS = ("preference", "style", "habit")
DEFAULT_TONE = ("", "concise-professional")
TEXT_MAX = 300

# --- frozen mapping: skill code prefix -> task type ----------------------------------------
_PREFIX_TYPES = (
    ("EM", "email"),
    ("CA", "scheduling"),
    ("TK", "todo"),
    ("DL", "delegation"),
    ("RS", "research"),
    ("DC", "documents"),
)
_EXACT_TYPES = {
    "MP01": "scheduling",
    "CT01": "contacts",
    "BR01": "briefing",
    "DV01": "commands",
    "DV02": "files",
    "DV03": "computer",
    "DV04": "computer",
}


def task_type_for_skill(code: str) -> str:
    code = (code or "").upper()
    if code in _EXACT_TYPES:
        return _EXACT_TYPES[code]
    for prefix, task_type in _PREFIX_TYPES:
        if code.startswith(prefix):
            return task_type
    return "general"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _aware(value):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def copy_old_stores(conn) -> dict:
    """The data copy. Returns counts per step (also used by the tests)."""
    memories = sa.table(
        "memories",
        sa.column("user_id"), sa.column("kind"), sa.column("content"), sa.column("source"), sa.column("created_at"),
    )
    skills = sa.table(
        "user_skills",
        sa.column("user_id"), sa.column("code"), sa.column("learned_notes", sa.JSON), sa.column("updated_at"),
    )
    settings = sa.table(
        "user_settings", sa.column("user_id"), sa.column("tone"), sa.column("updated_at"),
    )
    prefs = sa.table(
        "preferences",
        sa.column("id"), sa.column("user_id"), sa.column("task_type"), sa.column("system_scope"), sa.column("text"),
        sa.column("source"), sa.column("origin"), sa.column("origin_excerpt"), sa.column("active"),
        sa.column("use_count"), sa.column("created_at"), sa.column("updated_at"),
    )

    seen: dict[str, set[str]] = {}
    for user_id, text in conn.execute(sa.select(prefs.c.user_id, prefs.c.text)):
        seen.setdefault(user_id, set()).add(_norm(text))
    counts = {"memories": 0, "learned_notes": 0, "tone": 0}

    def put(user_id, task_type, text, source, created_at, key) -> bool:
        text = (text or "").strip()[:TEXT_MAX]
        if not text or _norm(text) in seen.setdefault(user_id, set()):
            return False
        seen[user_id].add(_norm(text))
        created = _aware(created_at)
        conn.execute(prefs.insert().values(
            id=str(uuid.uuid4()), user_id=user_id, task_type=task_type, system_scope="", text=text,
            source=source, origin="migrated", origin_excerpt="", active=True, use_count=0,
            created_at=created, updated_at=created,
        ))
        counts[key] += 1
        return True

    # 1. memories of the old kinds (oldest first, so a later duplicate is the one dropped)
    for row in conn.execute(
        sa.select(memories.c.user_id, memories.c.content, memories.c.source, memories.c.created_at)
        .where(memories.c.kind.in_(OLD_KINDS))
        .order_by(memories.c.created_at, memories.c.content)
    ).all():
        put(row.user_id, "general", row.content, "correction" if row.source == "correction" else "stated",
            row.created_at, "memories")

    # 2. learned notes (the list keeps the newest last; keep that order in created_at)
    for row in conn.execute(
        sa.select(skills.c.user_id, skills.c.code, skills.c.learned_notes, skills.c.updated_at)
        .order_by(skills.c.user_id, skills.c.code)
    ).all():
        notes = row.learned_notes
        if isinstance(notes, str):
            try:
                import json

                notes = json.loads(notes)
            except ValueError:
                notes = []
        notes = [n for n in (notes or []) if isinstance(n, str)]
        base = _aware(row.updated_at)
        for i, note in enumerate(notes):
            put(row.user_id, task_type_for_skill(row.code), note, "correction",
                base - timedelta(seconds=len(notes) - 1 - i), "learned_notes")

    # 3. the tone setting
    for row in conn.execute(
        sa.select(settings.c.user_id, settings.c.tone, settings.c.updated_at)
    ).all():
        tone = (row.tone or "").strip()
        if tone not in DEFAULT_TONE:
            put(row.user_id, "general", f"Tone: {tone}", "stated", row.updated_at, "tone")
    return counts


def upgrade() -> None:
    create_table_if_missing(
        "preferences",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("task_type", sa.String(length=40), nullable=False),
        sa.Column("system_scope", sa.String(length=60), nullable=False, server_default=sql_str("")),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("origin_message_id", sa.String(length=36), nullable=True),
        sa.Column("origin_excerpt", sa.String(length=300), nullable=False, server_default=sql_str("")),
        sa.Column("origin_work_item_id", sa.String(length=36), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sql_true()),
        sa.Column("superseded_by", sa.String(length=36), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default=sql_int(0)),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_preferences"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="fk_preferences_user_id"),
        sa.ForeignKeyConstraint(
            ["origin_work_item_id"], ["work_items.id"], ondelete="SET NULL", name="fk_preferences_origin_work_item_id"
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"], ["preferences.id"], ondelete="SET NULL", name="fk_preferences_superseded_by"
        ),
    )
    create_index_if_missing("ix_preferences_user_id", "preferences", ["user_id"])
    create_index_if_missing("ix_preferences_lookup", "preferences", ["user_id", "active", "task_type"])

    add_column_if_missing(
        "user_settings", sa.Column("memory_paused", sa.Boolean(), nullable=False, server_default=sql_false())
    )
    add_column_if_missing(
        "conversations", sa.Column("memory_off", sa.Boolean(), nullable=False, server_default=sql_false())
    )

    counts = copy_old_stores(op.get_bind())
    log.info("tl02: copied %s into preferences", counts)


def downgrade() -> None:
    # The copy only added rows to a table this revision creates; the old stores were left in place.
    for table, name in (("conversations", "memory_off"), ("user_settings", "memory_paused")):
        if column_exists(table, name):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(name)
    if table_exists("preferences"):
        op.drop_table("preferences")
