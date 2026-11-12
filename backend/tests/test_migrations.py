"""The migration runner (app/migrate.py) and revision 0002.

Every test that needs a database runs on SQLite, and also on Postgres when
AURA_TEST_POSTGRES_URL is set (locally: /home/claude/scripts/pg.sh start, then
export AURA_TEST_POSTGRES_URL=$(/home/claude/scripts/pg.sh url)). The Postgres
database is wiped (DROP SCHEMA public CASCADE) before each test.

The two 1.0.0 database shapes are built from revision 0001 itself, so they stay
valid when the models move on: "stamped" is 0001 with its alembic_version row,
"unstamped" is the same without it, which is what the old create_all boot left.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections import namedtuple
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("ALLOW_DEMO_LOGIN", "true")

import pytest  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from alembic import command  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402
from sqlalchemy import create_engine, event, text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

import app.db as app_db  # noqa: E402
from app import migrate, models  # noqa: E402,F401
from app.migrate import SchemaBusy, SchemaError, State  # noqa: E402
from app.migrate_legacy import LEGACY_1_0_0  # noqa: E402
from app.migration_ops import LEGACY_PENDING_DROP  # noqa: E402

PG_URL = os.environ.get("AURA_TEST_POSTGRES_URL")
BACKEND = Path(__file__).resolve().parents[1]
FIRST = "0001_initial"
STAMP = "stamp 0001_initial"
SEEDED = {"users": 2, "assistants": 2, "user_settings": 2, "user_skills": 2, "automation_rules": 1}


# ---------------------------------------------------------------------------
# Fixtures and builders
# ---------------------------------------------------------------------------


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.skipif(not PG_URL, reason="AURA_TEST_POSTGRES_URL not set"))])
def kind(request):
    return request.param


@pytest.fixture(autouse=True)
def _isolated_runner(monkeypatch):
    """Each test starts with no cached head, no last report, no preview env."""
    monkeypatch.setattr(migrate, "_CACHE", {})
    monkeypatch.setattr(migrate, "_LAST", None)
    monkeypatch.setattr(migrate, "_LAST_ATTEMPT", None)
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("MIGRATE_ON_PREVIEW", raising=False)


def _make_engine(kind: str, tmp_path: Path):
    if kind == "sqlite":
        eng = create_engine(
            f"sqlite:///{tmp_path / 'aura.db'}",
            poolclass=NullPool,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_connection, _record):  # same as the app engine
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return eng
    admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with admin.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    admin.dispose()
    # What db.py builds behind a transaction pooler.
    return create_engine(PG_URL, poolclass=NullPool, connect_args={"prepare_threshold": None})


@pytest.fixture
def engine(kind, tmp_path):
    eng = _make_engine(kind, tmp_path)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def sqlite_0001_template(tmp_path_factory):
    """A SQLite file holding exactly what revision 0001 builds, stamped."""
    path = tmp_path_factory.mktemp("template") / "aura.db"
    eng = create_engine(f"sqlite:///{path}", poolclass=NullPool)
    _upgrade_to(eng, FIRST)
    eng.dispose()
    return path


def _upgrade_to(eng, revision: str) -> None:
    with eng.connect() as conn:
        command.upgrade(migrate.alembic_config(conn), revision)
        conn.commit()


def make_stamped_0001(eng, template: Path | None, tmp_path: Path) -> None:
    if eng.dialect.name == "sqlite":
        shutil.copyfile(template, tmp_path / "aura.db")
    else:
        _upgrade_to(eng, FIRST)


def make_unstamped(eng, template, tmp_path) -> None:
    make_stamped_0001(eng, template, tmp_path)
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))


def seed(eng) -> None:
    """Rows as 1.0.0 wrote them, through plain SQL."""
    pg = eng.dialect.name == "postgresql"
    json_value = "CAST(:j AS json)" if pg else ":j"
    now = "2026-01-01 00:00:00"
    with eng.begin() as conn:
        for n in (1, 2):
            conn.execute(
                text(
                    "INSERT INTO users (id, email, name, avatar_url, timezone, password_hash,"
                    " is_active, is_demo, created_at, updated_at)"
                    " VALUES (:id, :e, 'U', '', 'UTC', '', :t, :f, :now, :now)"
                ),
                {"id": f"u{n}", "e": f"u{n}@x.test", "t": True, "f": False, "now": now},
            )
            conn.execute(
                text(
                    "INSERT INTO assistants (id, user_id, name, personality, avatar, pronoun,"
                    " hatched_at, interactions, actions_taken, corrections, stage, goals,"
                    f" onboarded, created_at, updated_at) VALUES (:id, :u, 'Aura', 'concise', 'teal',"
                    f" 'it', :now, 7, 3, 1, 'trusted', {json_value}, :t, :now, :now)"
                ),
                {"id": f"a{n}", "u": f"u{n}", "now": now, "j": "[]", "t": True},
            )
            conn.execute(
                text(
                    "INSERT INTO user_settings (id, user_id, briefing_time, briefing_enabled,"
                    " autonomy_level, tone, theme, heartbeat_enabled, heartbeat_interval_minutes,"
                    f" quiet_hours, extra, created_at, updated_at) VALUES (:id, :u, '08:00', :t,"
                    f" :lvl, 'concise-professional', 'warm', :t, 30, '22:00-07:00', {json_value}, :now, :now)"
                ),
                {"id": f"s{n}", "u": f"u{n}", "t": True, "lvl": ("relaxed", "full")[n - 1], "now": now, "j": "{}"},
            )
            conn.execute(
                text(
                    "INSERT INTO user_skills (id, user_id, code, enabled, run_count, success_count,"
                    f" learned_notes, created_at, updated_at) VALUES (:id, :u, 'EM', :t, 0, 0, {json_value}, :now, :now)"
                ),
                {"id": f"k{n}", "u": f"u{n}", "t": True, "now": now, "j": '["short"]'},
            )
        conn.execute(
            text(
                "INSERT INTO automation_rules (id, user_id, name, natural_language, trigger_type,"
                " trigger_config, actions, enabled, requires_confirmation, run_count, created_at,"
                f" updated_at) VALUES ('r1', 'u1', 'R', '', 'manual', {json_value}, {json_value.replace(':j', ':a')},"
                " :t, :t, 0, :now, :now)"
            ),
            {"j": "{}", "a": "[]", "t": True, "now": now},
        )


def counts(eng) -> dict[str, int]:
    with eng.connect() as conn:
        return {t: conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar() for t in SEEDED}


def user_tables(eng) -> dict[str, set[str]]:
    insp = sa.inspect(eng)
    return {t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names() if t != "alembic_version"}


def version_rows(eng) -> list[str]:
    with eng.connect() as conn:
        return [r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version"))]


def has_table(eng, name: str) -> bool:
    return sa.inspect(eng).has_table(name)


def backups(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "backups").glob("*.db")) if (tmp_path / "backups").exists() else []


def fake_scripts(tmp_path, monkeypatch, revision: str, upgrade_body: str) -> None:
    """A copy of the real scripts plus one extra revision, made current."""
    real = migrate.migrations_dir()
    down = migrate.EXPECTED_HEAD
    folder = tmp_path / "scripts"
    (folder / "versions").mkdir(parents=True)
    shutil.copy(real / "env.py", folder)
    shutil.copy(real / "script.py.mako", folder)
    for f in (real / "versions").glob("*.py"):
        shutil.copy(f, folder / "versions")
    (folder / "versions" / f"{revision}.py").write_text(
        "import sqlalchemy as sa\nfrom alembic import op\n"
        f"revision = {revision!r}\ndown_revision = {down!r}\nbranch_labels = None\ndepends_on = None\n\n"
        f"def upgrade():\n{upgrade_body}\n\ndef downgrade():\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(migrate, "migrations_dir", lambda: folder)
    monkeypatch.setattr(migrate, "EXPECTED_HEAD", revision)


# ---------------------------------------------------------------------------
# Scripts and snapshot (no database)
# ---------------------------------------------------------------------------


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(migrate.alembic_config())


def test_expected_head_matches_scripts():
    assert _script().get_current_head() == migrate.EXPECTED_HEAD


def test_revision_ids_follow_convention():
    script = _script()
    revisions = list(script.walk_revisions())  # newest first
    assert len(script.get_heads()) == 1
    children: dict[str, int] = {}
    for rev in revisions:
        assert isinstance(rev.down_revision, (str, type(None))), f"{rev.revision} has several parents"
        children[rev.down_revision] = children.get(rev.down_revision, 0) + 1
    assert all(n == 1 for n in children.values()), "branch point in the history"

    fixed = {FIRST, "0002_legacy_defaults"}
    for rev in revisions:
        assert len(rev.revision) <= 32, rev.revision
        if rev.revision in fixed:
            continue
        assert re.fullmatch(r"[a-z]{2}\d{2}_[a-z0-9_]+", rev.revision), rev.revision
        assert Path(rev.path).name == f"{rev.revision}.py", rev.path
    assert Path(script.get_revision("0002_legacy_defaults").path).name == "0002_legacy_defaults.py"

    # Contracts (NN 90-99) come last: once one has appeared, only contracts follow.
    ordered = [r.revision for r in reversed(revisions)]
    flags = [bool(re.fullmatch(r"[a-z]{2}9\d_.*", r)) for r in ordered]
    assert flags == sorted(flags), f"an expand revision follows a contract: {ordered}"


def test_legacy_snapshot_matches_0001(engine, sqlite_0001_template, tmp_path):
    make_stamped_0001(engine, sqlite_0001_template, tmp_path)
    built = {t: frozenset(cols) for t, cols in user_tables(engine).items()}
    assert built == LEGACY_1_0_0


def test_migrations_dir_source_and_frozen(tmp_path, monkeypatch):
    assert migrate.migrations_dir() == BACKEND / "alembic"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    with pytest.raises(SchemaError, match="migration scripts not found"):
        migrate.migrations_dir()
    shipped = tmp_path / "aura_migrations"
    (shipped / "versions").mkdir(parents=True)
    (shipped / "env.py").write_text("", encoding="utf-8")
    assert migrate.migrations_dir() == shipped


# ---------------------------------------------------------------------------
# States and transitions
# ---------------------------------------------------------------------------


def test_models_match_migrations(engine):
    migrate.upgrade_to_head(engine)
    with engine.connect() as conn:
        command.check(migrate.alembic_config(conn))  # raises on any model/schema diff


def test_empty_upgrades_to_head(engine):
    report = migrate.upgrade_to_head(engine)
    assert report.ok and report.state_before == State.EMPTY
    assert report.actions == [f"upgrade base -> {migrate.EXPECTED_HEAD}"]
    assert report.revision_after == migrate.EXPECTED_HEAD and not report.cached
    assert version_rows(engine) == [migrate.EXPECTED_HEAD]


def test_legacy_unstamped_is_stamped_and_upgraded(engine, sqlite_0001_template, tmp_path):
    make_unstamped(engine, sqlite_0001_template, tmp_path)
    seed(engine)
    report = migrate.upgrade_to_head(engine)
    assert report.state_before == State.LEGACY
    assert report.actions == [STAMP, f"upgrade {FIRST} -> {migrate.EXPECTED_HEAD}"]
    assert version_rows(engine) == [migrate.EXPECTED_HEAD]
    assert counts(engine) == SEEDED
    if engine.dialect.name == "sqlite":
        [saved] = backups(tmp_path)
        assert re.fullmatch(r"aura-1\.0\.0-unstamped-\d{8}T\d{6}Z\.db", saved.name)
        assert report.backup_path == str(saved)


def test_legacy_with_extra_columns_and_tables_is_accepted(engine, sqlite_0001_template, tmp_path):
    make_unstamped(engine, sqlite_0001_template, tmp_path)
    seed(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN legacy_extra VARCHAR(10)"))
        conn.execute(text("CREATE TABLE some_other_app (id INTEGER PRIMARY KEY)"))
    report = migrate.upgrade_to_head(engine)
    assert report.ok and report.state_before == State.LEGACY
    assert "legacy_extra" in user_tables(engine)["users"]
    assert has_table(engine, "some_other_app")
    assert counts(engine) == SEEDED


def test_legacy_missing_column_is_partial_and_untouched(engine, sqlite_0001_template, tmp_path):
    make_unstamped(engine, sqlite_0001_template, tmp_path)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users DROP COLUMN password_reset_sent_at"))
    before = user_tables(engine)
    with pytest.raises(SchemaError) as caught:
        migrate.upgrade_to_head(engine)
    assert caught.value.state == State.PARTIAL and "users.password_reset_sent_at" in caught.value.detail
    assert user_tables(engine) == before and not has_table(engine, "alembic_version")
    assert backups(tmp_path) == []


def test_empty_alembic_version_counts_as_unstamped(engine, sqlite_0001_template, tmp_path):
    make_unstamped(engine, sqlite_0001_template, tmp_path)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"))
    report = migrate.upgrade_to_head(engine)
    assert report.state_before == State.LEGACY and report.actions[0] == STAMP
    assert version_rows(engine) == [migrate.EXPECTED_HEAD]


def test_two_version_rows_is_partial(engine):
    migrate.upgrade_to_head(engine)
    migrate._CACHE.clear()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('zz99_other')"))
    with pytest.raises(SchemaError) as caught:
        migrate.upgrade_to_head(engine)
    assert caught.value.state == State.PARTIAL and "2 rows" in caught.value.detail


def test_stamped_0001_upgrades(engine, sqlite_0001_template, tmp_path):
    make_stamped_0001(engine, sqlite_0001_template, tmp_path)
    seed(engine)
    report = migrate.upgrade_to_head(engine)
    assert report.state_before == State.BEHIND and report.revision_before == FIRST
    assert report.actions == [f"upgrade {FIRST} -> {migrate.EXPECTED_HEAD}"]
    assert version_rows(engine) == [migrate.EXPECTED_HEAD]
    assert counts(engine) == SEEDED
    if engine.dialect.name == "sqlite":
        [saved] = backups(tmp_path)
        assert saved.name.startswith("aura-0001_initial-")


def test_at_head_is_one_query(engine):
    migrate.upgrade_to_head(engine)
    migrate._CACHE.clear()
    statements: list[str] = []
    event.listen(engine, "before_cursor_execute", lambda c, cur, stmt, *a: statements.append(stmt))
    report = migrate.upgrade_to_head(engine)
    assert report.state_before == State.AT_HEAD and report.actions == []
    assert len(statements) == 1


def test_cache_skips_database(engine):
    migrate.upgrade_to_head(engine)
    opened: list[int] = []
    event.listen(engine, "do_connect", lambda *a, **k: opened.append(1))
    report = migrate.upgrade_to_head(engine)
    assert report.cached and opened == []


def test_ahead_is_not_migrated(engine):
    migrate.upgrade_to_head(engine)
    migrate._CACHE.clear()
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = 'zz99_future'"))
    shape = user_tables(engine)
    report = migrate.upgrade_to_head(engine)
    assert report.ok and report.state_before == State.AHEAD and report.actions == []
    assert version_rows(engine) == ["zz99_future"] and user_tables(engine) == shape


def test_ahead_hosted_serves_desktop_refuses(engine, monkeypatch):
    migrate.upgrade_to_head(engine)
    migrate._CACHE.clear()
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = 'zz99_future'"))
    monkeypatch.setattr(app_db, "engine", engine)
    hosted = migrate.ensure_schema("api")
    assert hosted.ok and hosted.state_before == State.AHEAD
    assert migrate.ensure_schema("worker").ok
    desktop = migrate.ensure_schema("desktop")
    assert not desktop.ok and "zz99_future" in desktop.error
    assert version_rows(engine) == ["zz99_future"]


def test_failed_migration_rolls_back_everything(engine, sqlite_0001_template, tmp_path, monkeypatch):
    make_unstamped(engine, sqlite_0001_template, tmp_path)
    seed(engine)
    shape = user_tables(engine)
    fake_scripts(
        tmp_path / "x", monkeypatch, "sc01_fails",
        "    op.create_table('rollback_probe', sa.Column('id', sa.Integer(), primary_key=True))\n"
        "    raise RuntimeError('boom')",
    )
    with pytest.raises(RuntimeError, match="boom"):
        migrate.upgrade_to_head(engine, backup=False)
    # Stamp, 0002 and the new table: all undone together.
    assert not has_table(engine, "alembic_version") and not has_table(engine, "rollback_probe")
    assert user_tables(engine) == shape and counts(engine) == SEEDED
    with engine.connect() as conn:
        assert conn.execute(text("SELECT autonomy_level FROM user_settings WHERE id = 's2'")).scalar() == "full"


@pytest.mark.parametrize("fail", [False, True])
def test_ensure_schema_records_failure_without_raising(engine, tmp_path, monkeypatch, fail):
    monkeypatch.setattr(app_db, "engine", engine)
    if fail:
        monkeypatch.setattr(migrate, "upgrade_to_head", lambda *a, **k: (_ for _ in ()).throw(SchemaBusy("held")))
    report = migrate.ensure_schema("api")
    assert report.ok is (not fail) and migrate.status() is report
    if fail:
        assert report.transient and "held" in report.error
        with pytest.raises(SchemaBusy):
            migrate.ensure_schema("worker")


def test_preview_does_not_migrate(engine, sqlite_0001_template, tmp_path, monkeypatch):
    make_stamped_0001(engine, sqlite_0001_template, tmp_path)
    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setenv("VERCEL_ENV", "preview")
    report = migrate.ensure_schema("api")
    assert not report.ok and "not allowed" in report.error
    assert version_rows(engine) == [FIRST]
    monkeypatch.setenv("MIGRATE_ON_PREVIEW", "true")
    assert migrate.ensure_schema("api").ok
    assert version_rows(engine) == [migrate.EXPECTED_HEAD]


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------


ROUNDS = 4


def test_lock_serialises_two_runners(kind, engine, sqlite_0001_template, tmp_path):
    if kind == "postgres":
        make_unstamped(engine, sqlite_0001_template, tmp_path)
        seed(engine)
    reports: list[dict] = []
    if kind == "postgres":
        results: list = []

        def run():
            eng = create_engine(PG_URL, poolclass=NullPool, connect_args={"prepare_threshold": None})
            try:
                results.append(migrate.upgrade_to_head(eng))
            finally:
                eng.dispose()

        # Separate caches per runner, as two processes would have.
        threads = [threading.Thread(target=run) for _ in range(2)]
        migrate._CACHE.clear()
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
        reports = [{"actions": r.actions} for r in results]
    else:
        # Fresh unstamped 1.0.0 file each round; both processes import first, then
        # start at the same wall-clock instant, so they really collide.
        for round_no in range(ROUNDS):
            folder = tmp_path / f"race{round_no}"
            folder.mkdir()
            eng = create_engine(f"sqlite:///{folder / 'aura.db'}", poolclass=NullPool)
            make_unstamped(eng, sqlite_0001_template, folder)
            seed(eng)
            go = time.time() + 1.0
            env = {**os.environ, "DATABASE_URL": f"sqlite:///{folder / 'aura.db'}"}
            code = (
                "import sys, time; from app import migrate; "
                f"time.sleep(max(0.0, {go!r} - time.time())); sys.exit(migrate.main(['upgrade']))"
            )
            procs = [
                subprocess.Popen(
                    [sys.executable, "-c", code],
                    cwd=BACKEND, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                )
                for _ in range(2)
            ]
            round_reports = []
            for p in procs:
                out, _ = p.communicate(timeout=60)
                assert p.returncode == 0, out
                round_reports.append(json.loads(out.strip().splitlines()[-1]))
            assert all(r["ok"] for r in round_reports), round_reports
            assert sum(1 for r in round_reports if r["actions"]) == 1, round_reports
            assert sum(1 for r in round_reports if r["backup_path"]) == 1, round_reports
            assert len(backups(folder)) == 1
            assert version_rows(eng) == [migrate.EXPECTED_HEAD] and counts(eng) == SEEDED
            eng.dispose()
            reports = round_reports
    assert len(reports) == 2
    assert sum(1 for r in reports if r["actions"]) == 1
    if kind == "postgres":
        assert version_rows(engine) == [migrate.EXPECTED_HEAD] and counts(engine) == SEEDED


def test_lock_serialises_two_threads_sqlite(kind, engine, sqlite_0001_template, tmp_path):
    _sqlite_only(kind)
    make_unstamped(engine, sqlite_0001_template, tmp_path)
    seed(engine)
    url = engine.url
    barrier = threading.Barrier(2)
    results: list = []
    errors: list = []

    def run():
        eng = create_engine(url, poolclass=NullPool, connect_args={"check_same_thread": False})
        try:
            barrier.wait(10)
            results.append(migrate.upgrade_to_head(eng))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            eng.dispose()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == [] and len(results) == 2
    assert sum(1 for r in results if r.actions) == 1
    assert sum(1 for r in results if r.backup_path) == 1 and len(backups(tmp_path)) == 1
    assert version_rows(engine) == [migrate.EXPECTED_HEAD] and counts(engine) == SEEDED


def test_backup_names_never_collide(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    names = {migrate._reserve_backup_name(folder, "aura-x-20260101T000000Z") for _ in range(5)}
    assert len(names) == 5 and all(p.exists() for p in names)


def test_head_database_creates_no_lock_file(kind, engine, tmp_path):
    _sqlite_only(kind)
    migrate.upgrade_to_head(engine)
    migrate._CACHE.clear()
    migrate.upgrade_to_head(engine)
    before = set(tmp_path.iterdir())
    migrate._CACHE.clear()
    migrate.upgrade_to_head(engine)
    assert set(tmp_path.iterdir()) == before


def test_lock_timeout_raises_busy(kind, engine, tmp_path):
    if kind == "postgres":
        holder = engine.connect()
        holder.execute(text(f"SELECT pg_advisory_xact_lock({migrate.LOCK_KEY})"))
    else:
        holder = sqlite3.connect(tmp_path / "aura.db", isolation_level=None)
        holder.execute("PRAGMA journal_mode=WAL")  # as a running app leaves it
        holder.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        with pytest.raises(SchemaBusy):
            migrate.upgrade_to_head(engine, lock_wait_s=1)
        assert time.monotonic() - started >= 1
    finally:
        if kind == "postgres":
            holder.rollback()
        holder.close()
    assert migrate.upgrade_to_head(engine).ok  # and it works once the lock is free


def test_pg_runner_works_with_prepare_threshold_none_nullpool(engine, kind):
    if kind != "postgres":
        pytest.skip("Postgres only")
    assert isinstance(engine.pool, NullPool)
    assert migrate.upgrade_to_head(engine).ok
    migrate._CACHE.clear()
    assert migrate.upgrade_to_head(engine).state_before == State.AT_HEAD


# ---------------------------------------------------------------------------
# SQLite safety
# ---------------------------------------------------------------------------


def _sqlite_only(kind):
    if kind != "sqlite":
        pytest.skip("SQLite only")


def test_sqlite_backup_created_named_and_rotated(kind, engine, tmp_path):
    _sqlite_only(kind)
    migrate.upgrade_to_head(engine)
    for _ in range(4):
        migrate._CACHE.clear()
        with engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": FIRST})
        report = migrate.upgrade_to_head(engine)
        assert report.state_before == State.BEHIND and Path(report.backup_path).exists()
        time.sleep(0.02)
    kept = backups(tmp_path)
    assert len(kept) == 3 and Path(report.backup_path) in kept
    for f in kept:
        assert re.fullmatch(r"aura-0001_initial-\d{8}T\d{6}Z(-\d+)?\.db", f.name), f.name
    assert not list((tmp_path / "backups").glob("*.part"))


def test_sqlite_backup_includes_wal_content(kind, engine, sqlite_0001_template, tmp_path):
    _sqlite_only(kind)
    make_unstamped(engine, sqlite_0001_template, tmp_path)
    keeper = sqlite3.connect(tmp_path / "aura.db", isolation_level=None)
    keeper.execute("PRAGMA journal_mode=WAL")
    keeper.execute("PRAGMA wal_autocheckpoint=0")
    keeper.execute(
        "INSERT INTO users (id, email, name, avatar_url, timezone, password_hash, is_active, is_demo,"
        " created_at, updated_at) VALUES ('w1', 'wal@x.test', 'W', '', 'UTC', '', 1, 0, '2026-01-01', '2026-01-01')"
    )
    try:
        assert (tmp_path / "aura.db-wal").stat().st_size > 0
        # The point: the main file alone does not hold the row.
        raw = tmp_path / "raw.db"
        shutil.copyfile(tmp_path / "aura.db", raw)
        assert sqlite3.connect(raw).execute("SELECT count(*) FROM users").fetchone() == (0,)
        report = migrate.upgrade_to_head(engine)
    finally:
        keeper.close()
    saved = sqlite3.connect(report.backup_path)
    assert saved.execute("SELECT email FROM users").fetchall() == [("wal@x.test",)]
    saved.close()


def test_no_backup_for_at_head_or_empty(kind, engine, tmp_path):
    _sqlite_only(kind)
    first = migrate.upgrade_to_head(engine)
    migrate._CACHE.clear()
    second = migrate.upgrade_to_head(engine)
    assert first.backup_path is None and second.backup_path is None
    assert not (tmp_path / "backups").exists()


def test_integrity_failure_blocks_upgrade(kind, engine, sqlite_0001_template, tmp_path):
    _sqlite_only(kind)
    make_stamped_0001(engine, sqlite_0001_template, tmp_path)
    seed(engine)
    db = tmp_path / "aura.db"
    page = sqlite3.connect(db).execute("PRAGMA page_size").fetchone()[0]
    pages = db.stat().st_size // page
    with open(db, "r+b") as fh:
        fh.seek((pages - 2) * page)
        fh.write(b"\xff" * page)
    with pytest.raises(SchemaError, match="damaged") as caught:
        migrate.upgrade_to_head(engine)
    assert caught.value.state == State.PARTIAL
    assert backups(tmp_path) == []
    raw = sqlite3.connect(db)
    assert raw.execute("SELECT version_num FROM alembic_version").fetchall() == [(FIRST,)]
    raw.close()


def test_low_disk_blocks_upgrade(kind, engine, sqlite_0001_template, tmp_path, monkeypatch):
    _sqlite_only(kind)
    make_unstamped(engine, sqlite_0001_template, tmp_path)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda *_: usage(100, 100, 0))
    with pytest.raises(SchemaError, match="not enough disk space"):
        migrate.upgrade_to_head(engine)
    assert not has_table(engine, "alembic_version") and backups(tmp_path) == []


def test_sqlite_fk_cascade_not_triggered_by_batch(kind, engine, sqlite_0001_template, tmp_path, monkeypatch):
    """A batch rebuild of a PARENT table must not cascade-delete its children,
    although the app's own engine runs with foreign_keys=ON."""
    _sqlite_only(kind)
    make_stamped_0001(engine, sqlite_0001_template, tmp_path)
    seed(engine)
    fake_scripts(
        tmp_path / "x", monkeypatch, "sc01_rebuild_users",
        "    with op.batch_alter_table('users') as b:\n"
        "        b.alter_column('timezone', existing_type=sa.String(length=64),"
        " existing_nullable=False, server_default=sa.text(\"'UTC'\"))\n",
    )
    report = migrate.upgrade_to_head(engine)
    assert report.ok and report.revision_after == "sc01_rebuild_users"
    assert counts(engine) == SEEDED
    with engine.connect() as conn:
        assert "DEFAULT" in conn.execute(text("SELECT sql FROM sqlite_master WHERE name = 'users'")).scalar()


# ---------------------------------------------------------------------------
# Process behaviour
# ---------------------------------------------------------------------------


def test_config_independent_of_cwd(kind, engine, tmp_path, monkeypatch):
    _sqlite_only(kind)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert migrate.upgrade_to_head(engine).ok
    assert version_rows(engine) == [migrate.EXPECTED_HEAD]


def test_app_logging_survives_runner(engine):
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    aura = logging.getLogger("aura.migrate")
    aura.disabled = False
    migrate.upgrade_to_head(engine)
    assert root.handlers == handlers and root.level == level
    assert not aura.disabled and not logging.getLogger("aura").disabled


def test_cli_exit_codes(engine, monkeypatch, capsys):
    monkeypatch.setattr(app_db, "engine", engine)

    def run(*args) -> tuple[int, dict]:
        code = migrate.main(list(args))
        return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert run("status")[1]["state"] == "EMPTY"
    code, body = run("upgrade")
    assert code == 0 and body["ok"] and body["state_before"] == "EMPTY"
    code, body = run("upgrade")
    assert code == 0 and body["actions"] == []
    migrate._CACHE.clear()
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = 'zz99_future'"))
    assert run("upgrade")[0] == 2
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('zz98_other')"))
    code, body = run("upgrade")
    assert code == 1 and not body["ok"] and body["state_before"] == "PARTIAL"


# ---------------------------------------------------------------------------
# Revision 0002
# ---------------------------------------------------------------------------


def test_0002_legacy_defaults_data(engine):
    migrate.upgrade_to_head(engine)
    now = "2026-01-01 00:00:00"
    pg = engine.dialect.name == "postgresql"
    empty_json = "CAST('{}' AS json)" if pg else "'{}'"
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email, name, avatar_url, timezone, password_hash, is_active, is_demo, created_at, updated_at)"
                          " VALUES ('u1', 'a@x.test', 'A', '', 'UTC', '', :t, :f, :n, :n)"), {"t": True, "f": False, "n": now})
        conn.execute(text(f"INSERT INTO assistants (id, user_id, name, personality, avatar, pronoun, goals, created_at, updated_at)"
                          f" VALUES ('a1', 'u1', 'Aura', 'concise', 'teal', 'it', {'CAST(' + chr(39) + '[]' + chr(39) + ' AS json)' if pg else chr(39) + '[]' + chr(39)}, :n, :n)"), {"n": now})
        conn.execute(text("INSERT INTO user_settings (id, user_id, briefing_time, briefing_enabled, theme, heartbeat_enabled,"
                          f" heartbeat_interval_minutes, quiet_hours, extra, created_at, updated_at) VALUES ('s1', 'u1', '08:00', :t, 'warm', :t, 30, '22:00-07:00', {empty_json}, :n, :n)"),
                     {"t": True, "n": now})
        conn.execute(text("INSERT INTO user_skills (id, user_id, code, enabled, run_count, success_count, created_at, updated_at)"
                          " VALUES ('k1', 'u1', 'EM', :t, 0, 0, :n, :n)"), {"t": True, "n": now})
        conn.execute(text("INSERT INTO automation_rules (id, user_id, name, natural_language, trigger_type, trigger_config, actions, enabled, run_count, created_at, updated_at)"
                          f" VALUES ('r1', 'u1', 'R', '', 'manual', {empty_json}, {empty_json.replace('{}', '[]')}, :t, 0, :n, :n)"), {"t": True, "n": now})
    with engine.connect() as conn:
        a = conn.execute(text("SELECT hatched_at, interactions, actions_taken, corrections, stage, onboarded FROM assistants")).one()
        assert a[0] is not None and a[1:5] == (0, 0, 0, "stranger") and bool(a[5]) is True
        s = conn.execute(text("SELECT autonomy_level, tone FROM user_settings")).one()
        assert tuple(s) == ("conservative", "concise-professional")
        notes = conn.execute(text("SELECT learned_notes FROM user_skills")).scalar()
        assert (json.loads(notes) if isinstance(notes, str) else notes) == []
        assert bool(conn.execute(text("SELECT requires_confirmation FROM automation_rules")).scalar()) is True


def test_0002_roundtrip(engine):
    keys = [(t, c) for t, c in sorted(LEGACY_PENDING_DROP) if c and t in {"assistants", "user_settings", "user_skills", "automation_rules"}]

    def defaults() -> dict:
        insp = sa.inspect(engine)
        return {(t, c): next(x for x in insp.get_columns(t) if x["name"] == c)["default"] for t, c in keys}

    # Later revisions (pm02 is a data step and cannot be undone) are not part of this round trip.
    with engine.connect() as conn:
        command.upgrade(migrate.alembic_config(conn), "pm01_permissions")
        conn.commit()
    assert all(v is not None for v in defaults().values()), defaults()
    with engine.connect() as conn:
        command.downgrade(migrate.alembic_config(conn), FIRST)
        conn.commit()
    assert all(v is None for v in defaults().values()), defaults()
    assert version_rows(engine) == [FIRST]
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("INSERT INTO user_skills (id, user_id, code, enabled, run_count, success_count, created_at, updated_at)"
                          " VALUES ('k1', 'nobody', 'EM', :t, 0, 0, '2026-01-01', '2026-01-01')"), {"t": True})
    migrate._CACHE.clear()
    assert migrate.upgrade_to_head(engine).state_before == State.BEHIND
    assert all(v is not None for v in defaults().values())


# ---------------------------------------------------------------------------
# Revision pm02 (permissions, data): the 1.0 trust settings become rows
# ---------------------------------------------------------------------------


def test_pm02_permissions_data_data(engine, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.config import settings

    # Hosted shape: no device rows. (The device-control mapping is covered in test_perm_legacy.py.)
    monkeypatch.setattr(settings, "device_control", "off", raising=False)
    monkeypatch.setattr(settings, "local_mode", False, raising=False)
    with engine.connect() as conn:
        command.upgrade(migrate.alembic_config(conn), "pm01_permissions")
        conn.commit()
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=30)

    def table(conn, name):
        return sa.Table(name, sa.MetaData(), autoload_with=conn)

    with engine.begin() as conn:
        users, uset = table(conn, "users"), table(conn, "user_settings")
        for uid, level in (("u-strict", "strict"), ("u-relaxed", "relaxed"), ("u-full", "full"), ("u-cons", "conservative")):
            conn.execute(users.insert().values(id=uid, email=f"{uid}@x.test", name=uid, avatar_url="", timezone="UTC",
                                              password_hash="", is_active=True, is_demo=False, created_at=now, updated_at=now))
            conn.execute(uset.insert().values(id=f"s-{uid}", user_id=uid, briefing_time="08:00", briefing_enabled=True,
                                             autonomy_level=level, theme="warm", heartbeat_enabled=True,
                                             heartbeat_interval_minutes=30, quiet_hours="22:00-07:00", extra={},
                                             created_at=now, updated_at=now))
        # a user without a settings row at all
        conn.execute(users.insert().values(id="u-bare", email="bare@x.test", name="b", avatar_url="", timezone="UTC",
                                          password_hash="", is_active=True, is_demo=False, created_at=now, updated_at=now))
        grants = table(conn, "approval_grants")
        for gid, tool, scope, exp, revoked, target in (
            ("g1", "send_email", "always", None, False, "Bob@Example.com"),
            ("g2", "open_app", "window", now + timedelta(minutes=30), False, ""),
            ("g3", "open_app", "window", now - timedelta(minutes=30), False, ""),
            ("g4", "delete_file", "always", None, False, ""),
            ("g5", "notify", "always", None, True, ""),
        ):
            conn.execute(grants.insert().values(id=gid, user_id="u-cons", tool_name=tool, scope=scope, expires_at=exp,
                                               revoked=revoked, target=target, created_at=now, updated_at=now))
        conn.execute(table(conn, "schedules").insert().values(
            id="sch1", user_id="u-full", name="n", prompt="p", natural_language="", cron="0 8 * * *", timezone="UTC",
            enabled=True, deliver_to="notification", last_result="", run_count=0, created_at=now, updated_at=now))
        conn.execute(table(conn, "automation_rules").insert().values(
            id="auto1", user_id="u-cons", name="A", natural_language="", trigger_type="manual", trigger_config={},
            actions=[{"tool": "create_event"}, {"tool": "delete_event"}], enabled=True, run_count=0,
            created_at=now, updated_at=now))
        pend = table(conn, "pending_actions")
        for pid, convo, created in (("p-new", "c1", now - timedelta(hours=1)), ("p-auto", None, now - timedelta(hours=2)), ("p-old", "c1", old)):
            conn.execute(pend.insert().values(id=pid, user_id="u-cons", conversation_id=convo, tool_name="send_email",
                                             arguments={}, preview="", status="pending", skill_code="",
                                             created_at=created, updated_at=created))

    report = migrate.upgrade_to_head(engine)
    assert version_rows(engine) == [migrate.EXPECTED_HEAD] and report.revision_after == migrate.EXPECTED_HEAD

    with engine.connect() as conn:
        modes = dict(conn.execute(text("SELECT user_id, permission_mode FROM user_settings")).all())
        assert modes == {"u-strict": "ask", "u-relaxed": "ask", "u-full": "skip", "u-cons": "ask"}
        levels = dict(conn.execute(text("SELECT user_id, autonomy_level FROM user_settings")).all())
        assert levels["u-full"] == "full" and levels["u-strict"] == "strict", "the old column is untouched"
        rules = conn.execute(text("SELECT user_id, effect, system, tool, target_kind, pattern, applies_to, source, expires_at"
                                  " FROM permission_rules")).all()
        by = {}
        for r in rules:
            by.setdefault(r[0], []).append(r)
        assert {r[1] for r in by["u-strict"]} == {"ask"} and len(by["u-strict"]) == 7
        assert {r[3] for r in by["u-relaxed"]} == {"create_event", "write_file", "edit_file", "move_file", "make_directory",
                                                  "open_app", "close_app", "set_clipboard", "scroll_screen", "fetch_url"}
        assert {(r[2], r[6]) for r in by["u-full"]} == {(s, "schedule:sch1") for s in (
            "core-calendar", "core-inbox", "core-memory", "core-planning", "core-tasks")}
        cons = {(r[3], r[4], r[5], r[6]) for r in by["u-cons"]}
        assert cons == {("send_email", "recipient", "bob@example.com", ""), ("open_app", "", "", ""),
                        ("create_event", "", "", "automation:auto1")}
        assert {r[7] for r in rules} == {"legacy"} and "u-bare" not in by
        assert conn.execute(text("SELECT COUNT(*) FROM permission_rules WHERE tool = 'open_app' AND expires_at IS NOT NULL")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM device_folders")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM device_app_blocks")).scalar() == 0
        pend = {r[0]: r[1:] for r in conn.execute(text("SELECT id, status, origin, expires_at FROM pending_actions")).all()}
        assert pend["p-new"][0] == "pending" and pend["p-new"][1] == "chat" and pend["p-new"][2] is not None
        assert pend["p-auto"][1] == "automation" and pend["p-old"][0] == "expired"
        extra = conn.execute(text("SELECT extra FROM user_settings WHERE user_id = 'u-cons'")).scalar()
        extra = json.loads(extra) if isinstance(extra, str) else extra
        assert extra["permissions_migrated"]["from_tier"] == "conservative" and extra["legacy_events_pending"][0]["rules"] == 3
        assert extra["legacy_events_pending"][0]["grants_not_carried"] == ["delete_file"]
    assert has_table(engine, "approval_grants"), "nothing the 1.0 release had is dropped"

    # running the transform again changes nothing: migrated users are skipped
    import importlib.util

    spec = importlib.util.spec_from_file_location("pm02_again", BACKEND / "alembic" / "versions" / "pm02_permissions_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    env = {"device_control": False, "allowed_roots": [], "exec_mode": "ask", "exec_allowlist": [], "command_denylist": []}
    with engine.begin() as conn:
        again = mod.map_legacy(conn, env)
        assert not {"u-strict", "u-relaxed", "u-full", "u-cons"} & set(again)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM permission_rules")).scalar() == len(rules)


def test_pm02_cannot_be_downgraded(engine):
    migrate.upgrade_to_head(engine)
    with engine.connect() as conn:
        with pytest.raises(NotImplementedError):
            command.downgrade(migrate.alembic_config(conn), "pm01_permissions")


# ---------------------------------------------------------------------------
# Revision tl01 (task loop, expand): work items, work steps, the links to them
# ---------------------------------------------------------------------------


def _upgrade(engine, revision: str) -> None:
    with engine.connect() as conn:
        command.upgrade(migrate.alembic_config(conn), revision)
        conn.commit()


def _table(conn, name):
    return sa.Table(name, sa.MetaData(), autoload_with=conn)


def _user(conn, uid: str, now) -> None:
    conn.execute(_table(conn, "users").insert().values(
        id=uid, email=f"{uid}@x.test", name=uid, avatar_url="", timezone="UTC", password_hash="",
        is_active=True, is_demo=False, created_at=now, updated_at=now))


def test_tl01_work_items_data(engine):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    _upgrade(engine, "pm02_permissions_data")
    with engine.begin() as conn:
        _user(conn, "u1", now)
        conn.execute(_table(conn, "conversations").insert().values(
            id="c1", user_id="u1", title="t", pinned=False, archived=False, created_at=now, updated_at=now))
        conn.execute(_table(conn, "pending_actions").insert().values(
            id="p-ghost", user_id="u1", conversation_id="c1", tool_name="send_email", arguments={}, preview="",
            status="pending", skill_code="", work_item_id="no-such-item", created_at=now, updated_at=now))
        conn.execute(_table(conn, "pending_actions").insert().values(
            id="p-none", user_id="u1", conversation_id="c1", tool_name="send_email", arguments={}, preview="",
            status="pending", skill_code="", created_at=now, updated_at=now))
    migrate._CACHE.clear()
    report = migrate.upgrade_to_head(engine)
    assert report.ok and version_rows(engine) == [migrate.EXPECTED_HEAD]

    insp = sa.inspect(engine)
    assert {"work_items", "work_steps", "preferences"} <= set(insp.get_table_names())
    for table in ("skill_runs", "activity_logs", "usage_records", "pending_actions"):
        cols = {c["name"]: c for c in insp.get_columns(table)}
        assert cols["work_item_id"]["nullable"]
        fks = [fk for fk in insp.get_foreign_keys(table) if fk["referred_table"] == "work_items"]
        assert len(fks) == 1 and fks[0]["constrained_columns"] == ["work_item_id"], table
        assert (fks[0].get("options") or {}).get("ondelete", "").upper() == "SET NULL", table
        assert any(i["column_names"] == ["work_item_id"] for i in insp.get_indexes(table)), table
    pa = {c["name"] for c in insp.get_columns("pending_actions")}
    assert {"tool_call_id", "result"} <= pa

    with engine.begin() as conn:
        pend = dict(conn.execute(text("SELECT id, work_item_id FROM pending_actions")).all())
        assert pend == {"p-ghost": None, "p-none": None}, "a value pointing at no item is cleared"
        item = _table(conn, "work_items")
        conn.execute(item.insert().values(
            id="w1", user_id="u1", conversation_id="c1", trigger="chat", instruction="i", title="t",
            task_type="email", status="running", created_at=now, updated_at=now))
        conn.execute(_table(conn, "work_steps").insert().values(
            id="s1", work_item_id="w1", user_id="u1", seq=0, kind="tool", status="ok", created_at=now, updated_at=now))
        conn.execute(text("UPDATE pending_actions SET work_item_id = 'w1' WHERE id = 'p-none'"))
        row = conn.execute(text("SELECT recallable, steps_total, cost_usd, wait_reason, verification FROM work_items")).one()
        assert bool(row[0]) is True and row[1] == 0 and row[2] == 0 and row[3] == ""
    with pytest.raises(IntegrityError), engine.begin() as conn:  # (work_item_id, seq) is unique
        conn.execute(_table(conn, "work_steps").insert().values(
            id="s2", work_item_id="w1", user_id="u1", seq=0, kind="note", status="ok", created_at=now, updated_at=now))
    with pytest.raises(IntegrityError), engine.begin() as conn:  # and the link is a real foreign key
        conn.execute(text("UPDATE pending_actions SET work_item_id = 'nope' WHERE id = 'p-none'"))
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM work_items WHERE id = 'w1'"))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT work_item_id FROM pending_actions WHERE id = 'p-none'")).scalar() is None
        assert conn.execute(text("SELECT COUNT(*) FROM work_steps")).scalar() == 0, "steps go with their item"
        assert conn.execute(text("SELECT COUNT(*) FROM pending_actions")).scalar() == 2, "cards stay"


def test_tl01_tl02_roundtrip(engine):
    """Down to pm02 removes exactly what tl01 and tl02 added, and pm01's own column stays."""
    migrate.upgrade_to_head(engine)
    with engine.connect() as conn:
        command.downgrade(migrate.alembic_config(conn), "pm02_permissions_data")
        conn.commit()
    insp = sa.inspect(engine)
    assert not {"work_items", "work_steps", "preferences"} & set(insp.get_table_names())
    for table in ("skill_runs", "activity_logs", "usage_records"):
        assert "work_item_id" not in {c["name"] for c in insp.get_columns(table)}
    pa = {c["name"] for c in insp.get_columns("pending_actions")}
    assert "work_item_id" in pa and not {"tool_call_id", "result"} & pa
    assert not [fk for fk in insp.get_foreign_keys("pending_actions") if fk["referred_table"] == "work_items"]
    assert any(i["column_names"] == ["work_item_id"] for i in insp.get_indexes("pending_actions"))
    assert "memory_paused" not in {c["name"] for c in insp.get_columns("user_settings")}
    assert "memory_off" not in {c["name"] for c in insp.get_columns("conversations")}
    assert version_rows(engine) == ["pm02_permissions_data"]
    migrate._CACHE.clear()
    assert migrate.upgrade_to_head(engine).ok
    assert {"work_items", "work_steps", "preferences"} <= set(sa.inspect(engine).get_table_names())


# ---------------------------------------------------------------------------
# Revision tl02 (preference memory, expand + data copy)
# ---------------------------------------------------------------------------


def test_tl02_preferences_data(engine):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=3)
    _upgrade(engine, "tl01_work_items")
    with engine.begin() as conn:
        for uid in ("u1", "u2", "u3"):
            _user(conn, uid, now)
        settings_t = _table(conn, "user_settings")
        for uid, tone in (("u1", "warm-casual"), ("u2", "concise-professional"), ("u3", "")):
            conn.execute(settings_t.insert().values(
                id=f"s-{uid}", user_id=uid, briefing_time="08:00", briefing_enabled=True, autonomy_level="conservative",
                tone=tone, theme="warm", heartbeat_enabled=True, heartbeat_interval_minutes=30,
                quiet_hours="22:00-07:00", extra={}, created_at=now, updated_at=now))
        mem = _table(conn, "memories")
        for mid, uid, kind, content, source, created in (
            ("m1", "u1", "preference", "Keep replies short", "chat", old),
            ("m2", "u1", "style", "x" * 400, "correction", old + timedelta(hours=1)),
            ("m3", "u1", "habit", "Reads email at 9", "chat", old + timedelta(hours=2)),
            ("m4", "u1", "fact", "Priya leads the Series A", "chat", old),
            ("m5", "u2", "preference", "Use British spelling", "chat", old),
        ):
            conn.execute(mem.insert().values(
                id=mid, user_id=uid, kind=kind, content=content, source=source, confidence=0.7, embedding=[],
                use_count=0, pinned=False, created_at=created, updated_at=created))
        skills = _table(conn, "user_skills")
        for sid, uid, code, notes in (
            ("k1", "u1", "EM02", ["Keep replies short", "  never   CC the manager "]),  # first is also memory m1
            ("k2", "u1", "DV02", ["Ask before overwriting"]),
            ("k3", "u1", "MM01", ["Spell out acronyms"]),
            ("k4", "u2", "CA01", ["Prefer mornings"]),
            ("k5", "u3", "EM01", []),
        ):
            conn.execute(skills.insert().values(
                id=sid, user_id=uid, code=code, enabled=True, run_count=0, success_count=0, learned_notes=notes,
                created_at=now, updated_at=now))

    migrate._CACHE.clear()
    assert migrate.upgrade_to_head(engine).ok
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT user_id, task_type, text, source, origin, active, system_scope FROM preferences")).all()
    got = {(r[0], r[2]): (r[1], r[3]) for r in rows}
    assert {r[4] for r in rows} == {"migrated"} and all(bool(r[5]) for r in rows) and {r[6] for r in rows} == {""}
    assert got == {
        ("u1", "Keep replies short"): ("general", "stated"),  # from memories; the same learned note is not added again
        ("u1", "x" * 300): ("general", "correction"),  # cut to 300, source kept
        ("u1", "Reads email at 9"): ("general", "stated"),
        ("u1", "never   CC the manager"): ("email", "correction"),  # EM02; stored as written, trimmed
        ("u1", "Ask before overwriting"): ("files", "correction"),  # DV02
        ("u1", "Spell out acronyms"): ("general", "correction"),  # MM01 has no type
        ("u1", "Tone: warm-casual"): ("general", "stated"),
        ("u2", "Use British spelling"): ("general", "stated"),
        ("u2", "Prefer mornings"): ("scheduling", "correction"),  # CA01
    }, sorted(got)
    assert "u3" not in {r[0] for r in rows}, "default or empty tone and no notes: nothing copied"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM memories")).scalar() == 5, "old rows stay for a rollback"
        assert conn.execute(text("SELECT COUNT(*) FROM preferences WHERE text = 'Keep replies short'")).scalar() == 1

    # running the copy again adds nothing
    import importlib.util

    spec = importlib.util.spec_from_file_location("tl02_again", BACKEND / "alembic" / "versions" / "tl02_preferences.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with engine.begin() as conn:
        assert mod.copy_old_stores(conn) == {"memories": 0, "learned_notes": 0, "tone": 0}
        assert conn.execute(text("SELECT COUNT(*) FROM preferences")).scalar() == 9


def test_unmapped_columns_are_safe(engine):
    """Every database column the models no longer map must be harmless to a new
    INSERT (nullable or defaulted) and be on the pending-drop list."""
    migrate.upgrade_to_head(engine)
    insp = sa.inspect(engine)
    problems = []
    for table in insp.get_table_names():
        if table == "alembic_version":
            continue
        mapped = models.Base.metadata.tables.get(table)
        if mapped is None:
            if (table, None) not in LEGACY_PENDING_DROP:
                problems.append(f"table {table} is not mapped and not pending drop")
            continue
        for col in insp.get_columns(table):
            if col["name"] in mapped.columns:
                continue
            if (table, col["name"]) not in LEGACY_PENDING_DROP:
                problems.append(f"{table}.{col['name']} not in LEGACY_PENDING_DROP")
            if not col["nullable"] and col["default"] is None:
                problems.append(f"{table}.{col['name']} is NOT NULL without a default")
    assert not problems, problems
