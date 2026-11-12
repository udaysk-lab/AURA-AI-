"""Put believable AURA 1.0.0 data into a 1.0.0-shaped database (CI, migrations job).

    python backend/tests/migrations/seed_v1.py <database url>

SQLAlchemy Core only, against the tables as they exist in the database, so it
needs neither the 1.0.0 nor the 2.0 models and runs the same on SQLite and
PostgreSQL. verify_v1.py imports EXPECTED_COUNTS to prove nothing was lost.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.engine import Engine

EXPECTED_COUNTS = {
    "users": 2,
    "assistants": 2,
    "user_settings": 2,
    "approval_grants": 1,
    "user_skills": 2,
    "memories": 3,
    "automation_rules": 1,
    "schedules": 1,
    "conversations": 1,
    "messages": 3,
    "documents": 1,
    "document_chunks": 2,
}

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


def _filler(column: sa.Column):
    """A harmless value for a NOT NULL column the seed does not care about."""
    t = column.type
    if isinstance(t, sa.Boolean):
        return False
    if isinstance(t, (sa.Integer, sa.Float, sa.Numeric)):
        return 0
    if isinstance(t, sa.DateTime):
        return NOW
    if isinstance(t, sa.JSON):
        return {}
    return ""


def insert(conn, tables: dict[str, sa.Table], table_name: str, /, **values) -> str:
    """INSERT one row with `values`; every other NOT NULL column gets a filler."""
    table = tables[table_name]
    row = dict(values)
    row.setdefault("id", str(uuid.uuid4()))
    for column in table.columns:
        if column.name not in row and not column.nullable and column.server_default is None:
            row[column.name] = _filler(column)
    conn.execute(table.insert().values(**row))
    return row["id"]


def seed(engine: Engine) -> dict[str, int]:
    meta = sa.MetaData()
    meta.reflect(bind=engine)
    tables = dict(meta.tables)
    stamp = {"created_at": NOW, "updated_at": NOW}

    with engine.begin() as conn:
        users = []
        for n, (autonomy, stage) in enumerate([("relaxed", "trusted"), ("full", "trusted")], start=1):
            uid = insert(
                conn, tables, "users",
                email=f"owner{n}@example.com", name=f"Owner {n}", timezone="UTC",
                is_active=True, is_demo=False, **stamp,
            )
            users.append(uid)
            insert(
                conn, tables, "assistants", user_id=uid, name="Aura", personality="concise",
                avatar="teal", pronoun="it", hatched_at=NOW - timedelta(days=30),
                interactions=120 + n, actions_taken=40 + n, corrections=3, stage=stage,
                goals=["inbox zero"], onboarded=True, **stamp,
            )
            insert(
                conn, tables, "user_settings", user_id=uid, autonomy_level=autonomy,
                tone="concise-professional", extra={}, **stamp,
            )

        insert(
            conn, tables, "approval_grants", user_id=users[0], tool_name="send_email",
            scope="always", revoked=False, target="", **stamp,
        )
        for uid, code, notes in [
            (users[0], "email", ["prefers short replies", "signs off with Thanks"]),
            (users[1], "calendar", ["no meetings before 10"]),
        ]:
            insert(
                conn, tables, "user_skills", user_id=uid, code=code, enabled=True, run_count=7,
                success_count=6, learned_notes=notes, **stamp,
            )
        for kind, content in [
            ("preference", "Likes bullet-point summaries"),
            ("style", "Writes in a warm, brief tone"),
            ("fact", "Works from Pune on weekdays"),
        ]:
            insert(
                conn, tables, "memories", user_id=users[0], kind=kind, content=content,
                source="chat", confidence=0.9, embedding=[0.1, 0.2], use_count=2, pinned=False, **stamp,
            )
        insert(
            conn, tables, "automation_rules", user_id=users[0], name="Newsletters to read-later",
            natural_language="Move newsletters to read-later", trigger_type="email_received",
            trigger_config={}, actions=[{"tool": "label_email"}], enabled=True,
            requires_confirmation=True, run_count=4, **stamp,
        )
        insert(
            conn, tables, "schedules", user_id=users[0], name="Morning brief", prompt="Summarise my day",
            natural_language="every weekday at 8", cron="0 8 * * 1-5", timezone="UTC", enabled=True,
            deliver_to="chat", last_result="", run_count=12, **stamp,
        )
        conversation = insert(
            conn, tables, "conversations", user_id=users[0], title="Planning the week",
            pinned=False, archived=False, **stamp,
        )
        for role, content in [
            ("user", "What does my week look like?"),
            ("assistant", "Three meetings and one deadline."),
            ("user", "Move the Thursday one."),
        ]:
            insert(conn, tables, "messages", conversation_id=conversation, role=role, content=content, meta={}, **stamp)
        document = insert(
            conn, tables, "documents", user_id=users[0], title="Notes", mime_type="text/plain",
            storage_path="", size_bytes=20, content="first part. second part.", summary="", source="upload", **stamp,
        )
        for ordinal, content in enumerate(["first part.", "second part."]):
            insert(
                conn, tables, "document_chunks", document_id=document, user_id=users[0],
                ordinal=ordinal, content=content, embedding=[0.3, 0.4], **stamp,
            )

    return count_rows(engine)


def count_rows(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            name: conn.execute(sa.text(f"SELECT COUNT(*) FROM {name}")).scalar_one()
            for name in EXPECTED_COUNTS
        }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    engine = sa.create_engine(argv[1], poolclass=sa.pool.NullPool)
    counts = seed(engine)
    engine.dispose()
    if counts != EXPECTED_COUNTS:
        print(f"seed produced {counts}, expected {EXPECTED_COUNTS}", file=sys.stderr)
        return 1
    print(f"seeded {sum(counts.values())} rows in {len(counts)} tables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
