"""The schema runner: Alembic is the only way the database schema changes.

Every start-up path (API lifespan, worker, desktop launcher, CLI) calls
`ensure_schema`. At head it costs one connection and one query and does not
import alembic. Otherwise it classifies the database, backs up SQLite, and
stamps and upgrades under a lock in ONE transaction, so a failure leaves the
old schema intact. See aura-specs/30-migrations.md.

    python -m app.migrate upgrade|status|check-legacy
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Literal, NamedTuple

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, Connection, Engine
from sqlalchemy.exc import DatabaseError, DBAPIError, InterfaceError, OperationalError
from sqlalchemy.pool import NullPool

from app.migrate_legacy import LEGACY_1_0_0

log = logging.getLogger("aura.migrate")

EXPECTED_HEAD = "0002_legacy_defaults"  # bumped by every revision; a test asserts it equals the scripts' head
BASE_REVISION = "0001_initial"
LOCK_KEY = 0x41555241  # "AURA"
BACKUPS_TO_KEEP = 3
RETRY_INTERVAL_S = 30.0


class State(StrEnum):
    EMPTY = "EMPTY"
    LEGACY = "LEGACY"
    BEHIND = "BEHIND"
    AT_HEAD = "AT_HEAD"
    AHEAD = "AHEAD"
    PARTIAL = "PARTIAL"


class SchemaError(Exception):
    """A deterministic failure: retrying cannot help."""

    def __init__(self, state: State, detail: str):
        super().__init__(f"{state}: {detail}")
        self.state = state
        self.detail = detail


class SchemaBusy(Exception):
    """Could not get the migration lock in time. Transient."""


@dataclass
class MigrationReport:
    state_before: State | None
    revision_before: str | None
    revision_after: str | None
    actions: list[str]
    backup_path: str | None
    cached: bool
    duration_ms: int
    ok: bool
    transient: bool
    error: str | None


class Classification(NamedTuple):
    state: State
    detail: str
    revision: str | None


_CACHE: dict[str, str] = {}
_LAST: MigrationReport | None = None
_LAST_ATTEMPT: float | None = None
_RETRY_LOCK = threading.Lock()


def _monotonic() -> float:
    return time.monotonic()


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


# ---------------------------------------------------------------------------
# Scripts location and Alembic configuration
# ---------------------------------------------------------------------------


def migrations_dir() -> Path:
    import app

    if getattr(sys, "frozen", False):
        path = Path(sys._MEIPASS) / "aura_migrations"  # type: ignore[attr-defined]
    else:
        path = Path(app.__file__).resolve().parent.parent / "alembic"
    if not (path / "env.py").is_file() or not (path / "versions").is_dir():
        raise SchemaError(State.PARTIAL, f"migration scripts not found at {path}")
    return path


def alembic_config(connection: Connection | None = None):
    from alembic.config import Config

    cfg = Config()  # no ini file: independent of the working directory
    cfg.set_main_option("script_location", str(migrations_dir()).replace("%", "%%"))
    cfg.set_main_option("path_separator", "os")
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _table_exists(conn: Connection, name: str) -> bool:
    if conn.dialect.name == "postgresql":
        return bool(
            conn.exec_driver_sql(
                "SELECT to_regclass(current_schema() || '.' || %s) IS NOT NULL", (name,)
            ).scalar()
        )
    return bool(
        conn.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).first()
    )


def read_revisions(conn: Connection) -> list[str]:
    if not _table_exists(conn, "alembic_version"):
        return []
    return [r[0] for r in conn.exec_driver_sql("SELECT version_num FROM alembic_version").all()]


def classify(conn: Connection, known_revisions: set[str] | frozenset[str]) -> Classification:
    rows = read_revisions(conn)
    if len(rows) > 1:
        return Classification(State.PARTIAL, f"alembic_version has {len(rows)} rows; history is linear", None)
    if len(rows) == 1:
        rev = rows[0]
        if rev == EXPECTED_HEAD:
            return Classification(State.AT_HEAD, "", rev)
        if rev in known_revisions:
            return Classification(State.BEHIND, "", rev)
        return Classification(State.AHEAD, f"database is at {rev!r}, unknown to this build", rev)

    # No row, or an empty alembic_version: an interrupted stamp counts as unstamped.
    if conn.dialect.name == "postgresql":
        pairs = conn.exec_driver_sql(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema()"
        ).all()
    else:
        pairs = conn.exec_driver_sql(
            "SELECT m.name, p.name FROM sqlite_master m "
            "JOIN pragma_table_info(m.name) p WHERE m.type = 'table'"
        ).all()
    cols: dict[str, set[str]] = {}
    for table, column in pairs:
        cols.setdefault(table, set()).add(column)

    if not (LEGACY_1_0_0.keys() & cols.keys()):
        return Classification(State.EMPTY, "", None)  # unrelated tables are ignored
    missing = sorted(
        (t, c) for t, cs in LEGACY_1_0_0.items() for c in cs if c not in cols.get(t, ())
    )
    if missing:
        shown = ", ".join(f"{t}.{c}" for t, c in missing[:10])
        more = f" (+{len(missing) - 10} more)" if len(missing) > 10 else ""
        return Classification(State.PARTIAL, f"missing: {shown}{more}", None)
    return Classification(State.LEGACY, "", None)  # extra columns and tables are tolerated


# ---------------------------------------------------------------------------
# SQLite: migration engine, integrity, backup
# ---------------------------------------------------------------------------


def _sqlite_migration_engine(url: URL, busy_timeout_ms: int = 30000) -> Engine:
    eng = create_engine(url, poolclass=NullPool, connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _on_connect(dbapi_connection, _record):
        dbapi_connection.isolation_level = None  # we begin by hand
        cur = dbapi_connection.cursor()
        cur.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        # The app engine runs with foreign_keys=ON, which turns a batch-mode
        # DROP TABLE into a cascade delete. This engine never does.
        cur.execute("PRAGMA foreign_keys = OFF")
        cur.close()

    @event.listens_for(eng, "begin")
    def _on_begin(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return eng


def _sqlite_file(url: URL) -> Path | None:
    database = url.database
    if not database or database == ":memory:" or database.startswith("file::memory:"):
        return None
    return Path(database).resolve()


def integrity_check(db_file: Path) -> None:
    conn = sqlite3.connect(str(db_file))
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.DatabaseError as exc:
        raise SchemaError(State.PARTIAL, f"database file is damaged ({exc})") from exc
    finally:
        conn.close()
    if rows != [("ok",)]:
        raise SchemaError(State.PARTIAL, "database file is damaged")


def backup_sqlite(db_file: Path, from_label: str, state: State) -> Path:
    folder = db_file.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    size = db_file.stat().st_size
    wal = db_file.with_name(db_file.name + "-wal")
    if wal.exists():
        size += wal.stat().st_size
    if shutil.disk_usage(folder).free < 2 * size:
        raise SchemaError(state, "not enough disk space for a backup")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = folder / f"{db_file.stem}-{from_label}-{stamp}.db"
    n = 1
    while final.exists():
        n += 1
        final = folder / f"{db_file.stem}-{from_label}-{stamp}-{n}.db"
    part = final.with_name(final.name + ".part")
    src = sqlite3.connect(str(db_file))
    dst = sqlite3.connect(str(part))
    try:
        src.backup(dst)  # a consistent copy including WAL content
    except BaseException:
        dst.close()
        src.close()
        part.unlink(missing_ok=True)
        raise
    dst.close()
    src.close()
    os.replace(part, final)
    return final


def _rotate_backups(db_file: Path) -> None:
    folder = db_file.parent / "backups"
    try:
        files = sorted(folder.glob(f"{db_file.stem}-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError as exc:
        log.warning("Could not list backups: %s", exc)
        return
    for old in files[BACKUPS_TO_KEEP:]:
        try:
            old.unlink()
        except OSError as exc:
            log.warning("Could not delete old backup %s: %s", old, exc)


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------


@contextmanager
def locked_transaction(conn: Connection, wait: float):
    """One transaction that holds the migration lock and commits on exit.

    Postgres: a transaction-scoped advisory lock (safe behind a transaction
    pooler), polled once a second. SQLite: BEGIN IMMEDIATE, issued by the
    migration engine's begin hook.
    """
    if conn.dialect.name == "postgresql":
        deadline = _monotonic() + wait
        while True:
            got = conn.exec_driver_sql(f"SELECT pg_try_advisory_xact_lock({LOCK_KEY})").scalar()
            if got:
                break
            conn.rollback()
            if _monotonic() >= deadline:
                raise SchemaBusy(f"another process holds the migration lock (waited {wait:g}s)")
            time.sleep(min(1.0, max(0.0, deadline - _monotonic())))
        conn.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
        conn.exec_driver_sql("SET LOCAL statement_timeout = '120s'")
    else:
        try:
            conn.exec_driver_sql("SELECT 1")  # autobegin -> BEGIN IMMEDIATE
        except OperationalError as exc:
            if conn.in_transaction():
                conn.rollback()
            raise SchemaBusy(f"database is locked: {exc.orig}") from exc
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


def _report(
    state: State,
    before: str | None,
    after: str | None,
    started: float,
    *,
    actions: list[str] | None = None,
    backup: Path | None = None,
    cached: bool = False,
) -> MigrationReport:
    return MigrationReport(
        state_before=state,
        revision_before=before,
        revision_after=after,
        actions=actions or [],
        backup_path=str(backup) if backup else None,
        cached=cached,
        duration_ms=_ms(started),
        ok=True,
        transient=False,
        error=None,
    )


def upgrade_to_head(
    engine: Engine,
    *,
    allow_migrate: bool = True,
    backup: bool = True,
    lock_wait_s: float = 60.0,
) -> MigrationReport:
    started = time.monotonic()
    key = engine.url.render_as_string(hide_password=True)
    if _CACHE.get(key) == EXPECTED_HEAD:
        return _report(State.AT_HEAD, EXPECTED_HEAD, EXPECTED_HEAD, started, cached=True)

    # Fast path: one connection, one query, no alembic import.
    with engine.connect() as conn:
        try:
            rows = conn.exec_driver_sql("SELECT version_num FROM alembic_version").all()
        except DBAPIError:
            rows = None
        conn.rollback()
    if rows is not None and len(rows) == 1 and rows[0][0] == EXPECTED_HEAD:
        _CACHE[key] = EXPECTED_HEAD
        return _report(State.AT_HEAD, EXPECTED_HEAD, EXPECTED_HEAD, started)

    progress: dict = {}
    try:
        return _slow_path(engine, key, started, allow_migrate, backup, lock_wait_s, progress)
    except Exception as exc:
        exc.aura_backup_path = progress.get("backup")  # type: ignore[attr-defined]
        raise


def _slow_path(engine, key, started, allow_migrate, backup, lock_wait_s, progress) -> MigrationReport:
    from alembic import command
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_config())
    if script.get_current_head() != EXPECTED_HEAD:
        raise SchemaError(
            State.PARTIAL,
            f"packaging: EXPECTED_HEAD {EXPECTED_HEAD} != scripts head {script.get_current_head()}",
        )
    known = {r.revision for r in script.walk_revisions()}

    sqlite = engine.dialect.name == "sqlite"
    db_file = _sqlite_file(engine.url) if sqlite else None

    # Outside the lock: only to decide on a backup, and on AHEAD / PARTIAL.
    try:
        with engine.connect() as conn:
            pre = classify(conn, known)
            conn.rollback()
    except DatabaseError as exc:
        if sqlite and not isinstance(exc, OperationalError):
            raise SchemaError(State.PARTIAL, "database file is damaged") from exc
        raise

    if pre.state == State.PARTIAL:
        raise SchemaError(State.PARTIAL, pre.detail)
    if pre.state == State.AHEAD:
        log.warning("Schema is AHEAD of this build: %s", pre.detail)
        return _report(State.AHEAD, pre.revision, pre.revision, started)
    if pre.state == State.AT_HEAD:
        _CACHE[key] = EXPECTED_HEAD
        return _report(State.AT_HEAD, pre.revision, pre.revision, started)
    if not allow_migrate:
        raise SchemaError(pre.state, "migration not allowed here")

    backup_path: Path | None = None
    if sqlite and db_file is not None and pre.state in (State.LEGACY, State.BEHIND) and backup:
        integrity_check(db_file)
        label = pre.revision if pre.state == State.BEHIND else "1.0.0-unstamped"
        backup_path = backup_sqlite(db_file, label, pre.state)
        progress["backup"] = str(backup_path)

    if sqlite and db_file is not None:
        mig_engine = _sqlite_migration_engine(engine.url, int(min(lock_wait_s, 30) * 1000))
    else:
        mig_engine = engine  # Postgres, or an in-memory SQLite database
    actions: list[str] = []
    try:
        with mig_engine.connect() as conn:
            with locked_transaction(conn, lock_wait_s):
                # Re-read under the lock: another process may have finished.
                now = classify(conn, known)
                if now.state == State.PARTIAL:
                    raise SchemaError(State.PARTIAL, now.detail)
                if now.state == State.AHEAD:
                    log.warning("Schema is AHEAD of this build: %s", now.detail)
                    return _report(State.AHEAD, now.revision, now.revision, started, backup=backup_path)
                from_rev = now.revision
                if now.state == State.LEGACY:
                    command.stamp(alembic_config(conn), BASE_REVISION)
                    actions.append(f"stamp {BASE_REVISION}")
                    from_rev = BASE_REVISION
                if now.state in (State.EMPTY, State.LEGACY, State.BEHIND):
                    command.upgrade(alembic_config(conn), "head")
                    actions.append(f"upgrade {from_rev or 'base'} -> {EXPECTED_HEAD}")
                if sqlite:
                    bad = conn.exec_driver_sql("PRAGMA foreign_key_check").all()
                    if bad:
                        raise SchemaError(State.PARTIAL, f"foreign key violations: {[tuple(r) for r in bad[:5]]}")
                after = read_revisions(conn)
                if after != [EXPECTED_HEAD]:
                    raise SchemaError(now.state, f"revision after upgrade is {after}, expected {EXPECTED_HEAD}")
    finally:
        if mig_engine is not engine:
            mig_engine.dispose()

    _CACHE[key] = EXPECTED_HEAD
    if db_file is not None and backup_path is not None:
        _rotate_backups(db_file)
    for line in actions:
        log.info("migrate: %s", line)
    return _report(now.state, now.revision, EXPECTED_HEAD, started, actions=actions, backup=backup_path)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, SchemaBusy):
        return True
    if isinstance(exc, SchemaError):
        return False
    if isinstance(exc, (OperationalError, InterfaceError)):
        return True
    if isinstance(exc, DBAPIError):
        code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        return code in ("55P03", "57014")
    return False


def _failure_report(exc: Exception, started: float) -> MigrationReport:
    state = exc.state if isinstance(exc, SchemaError) else None
    detail = exc.detail if isinstance(exc, SchemaError) else f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
    return MigrationReport(
        state_before=state,
        revision_before=None,
        revision_after=None,
        actions=[],
        backup_path=getattr(exc, "aura_backup_path", None),
        cached=False,
        duration_ms=_ms(started),
        ok=False,
        transient=is_transient(exc),
        error=detail,
    )


def _migrate_allowed() -> bool:
    if os.environ.get("VERCEL_ENV") == "preview":
        return os.environ.get("MIGRATE_ON_PREVIEW", "").strip().lower() == "true"
    return True


def ensure_schema(context: Literal["api", "worker", "desktop", "cli"]) -> MigrationReport:
    """Bring the database to head. `api`, `desktop` and `cli` return a report with
    ok=False on failure; `worker` raises after logging."""
    global _LAST, _LAST_ATTEMPT
    from app import db

    if os.environ.get("AUTO_CREATE_SCHEMA") is not None:
        log.warning("AUTO_CREATE_SCHEMA is no longer used; migrations run automatically")

    started = time.monotonic()
    _LAST_ATTEMPT = _monotonic()
    try:
        report = upgrade_to_head(db.engine, allow_migrate=_migrate_allowed())
    except Exception as exc:
        report = _failure_report(exc, started)
        log.error(
            "Database schema not ready (%s): %s%s",
            report.state_before or "unknown",
            report.error,
            f" | backup saved at {report.backup_path}" if report.backup_path else "",
        )
        _LAST = report
        if context == "worker":
            raise
        return report

    if report.state_before == State.AHEAD and context == "desktop":
        # One user, no rollout skew: an older build must not open a newer database.
        report.ok = False
        report.error = f"database is at {report.revision_after}, newer than this build ({EXPECTED_HEAD})"
        log.error("Database schema not ready (AHEAD): %s", report.error)
    elif report.actions:
        log.info("Database schema ready: %s in %d ms", "; ".join(report.actions), report.duration_ms)
    _LAST = report
    return report


def retry_if_due(min_interval: float = RETRY_INTERVAL_S) -> MigrationReport | None:
    """Re-run `ensure_schema("api")` after a transient failure, at most once per
    `min_interval` per process. Call from a worker thread: it may block."""
    with _RETRY_LOCK:
        last = _LAST
        if last is None or last.ok or not last.transient:
            return last
        if _LAST_ATTEMPT is not None and _monotonic() - _LAST_ATTEMPT < min_interval:
            return last
        return ensure_schema("api")


def status() -> MigrationReport | None:
    return _LAST


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _describe() -> tuple[Classification, str | None]:
    from alembic.script import ScriptDirectory

    from app import db

    script = ScriptDirectory.from_config(alembic_config())
    known = {r.revision for r in script.walk_revisions()}
    with db.engine.connect() as conn:
        return classify(conn, known), script.get_current_head()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.migrate")
    parser.add_argument("command", choices=["upgrade", "status", "check-legacy"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")

    if args.command == "upgrade":
        report = ensure_schema("cli")
        print(json.dumps(asdict(report)))
        if not report.ok:
            return 2 if report.state_before == State.AHEAD else 1
        return 2 if report.state_before == State.AHEAD else 0

    try:
        found, head = _describe()
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps({"state": found.state, "revision": found.revision, "detail": found.detail, "head": head}))
    if args.command == "check-legacy":
        return 0 if found.state == State.LEGACY else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
