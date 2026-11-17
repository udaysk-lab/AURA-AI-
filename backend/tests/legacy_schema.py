"""The 1.0 trust settings, for the tests of the permissions data step (pm02).

The contract step pm90 drops `user_settings.autonomy_level` and the table `approval_grants`, and the
models no longer map them. The tests of the mapping still need a database that has them, so these
helpers add both to a test database on first use and read and write them with plain SQL.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

_md = sa.MetaData()
GRANTS = sa.Table(
    "approval_grants", _md,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("user_id", sa.String(36), nullable=False),
    sa.Column("tool_name", sa.String(120), nullable=False),
    sa.Column("scope", sa.String(20), nullable=False, server_default="always"),
    sa.Column("expires_at", sa.DateTime(timezone=True)),
    sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("target", sa.String(320), nullable=False, server_default=""),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)


def ensure_legacy(db) -> None:
    """Add the old column and table to this (SQLite) test database if they are not there yet."""
    db.flush()
    conn = db.connection()
    insp = sa.inspect(conn)
    if "autonomy_level" not in {c["name"] for c in insp.get_columns("user_settings")}:
        conn.exec_driver_sql(
            "ALTER TABLE user_settings ADD COLUMN autonomy_level VARCHAR(20) NOT NULL DEFAULT 'conservative'")
    if not insp.has_table("approval_grants"):
        GRANTS.create(conn)


def set_tier(db, user, tier: str) -> None:
    ensure_legacy(db)
    db.execute(sa.text("UPDATE user_settings SET autonomy_level = :t WHERE user_id = :u"), {"t": tier, "u": user.id})


def get_tier(db, user) -> str | None:
    ensure_legacy(db)
    return db.execute(sa.text("SELECT autonomy_level FROM user_settings WHERE user_id = :u"), {"u": user.id}).scalar()


def add_grant(db, user_id: str, tool_name: str, scope: str = "always", expires_at: datetime | None = None,
              revoked: bool = False, target: str = "") -> str:
    ensure_legacy(db)
    gid = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    db.execute(GRANTS.insert().values(
        id=gid, user_id=user_id, tool_name=tool_name, scope=scope, expires_at=expires_at, revoked=revoked,
        target=target, created_at=now, updated_at=now))
    return gid


def grants_for(db, user_id: str, tool_name: str) -> list[dict]:
    """The user's grants for one tool that are not revoked, as dicts."""
    ensure_legacy(db)
    rows = db.execute(sa.select(GRANTS).where(
        GRANTS.c.user_id == user_id, GRANTS.c.tool_name == tool_name, GRANTS.c.revoked.is_(False))).mappings().all()
    return [dict(r) for r in rows]
