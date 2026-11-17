"""Check a database that seed_v1.py filled and `python -m app.migrate upgrade` migrated.

    python backend/tests/migrations/verify_v1.py <url> --way unstamped|stamped \
        --report first.json --again second.json

--report is the CLI's JSON from the first upgrade, --again the one from a second
upgrade straight after. Exit 0 when every check passes. Run it with the same
DATABASE_URL the upgrade used; it imports the current app.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import seed_v1  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        failures.append(name)


def pooler_engine(url: str):
    """Like the app's engine behind a transaction pooler: no pooling, no prepared statements."""
    args = {"prepare_threshold": None} if sa.engine.make_url(url).get_backend_name() == "postgresql" else {}
    return sa.create_engine(url, poolclass=sa.pool.NullPool, connect_args=args)


def read_report(path: str) -> dict:
    # The CLI logs to stderr; the report is the last JSON line of stdout.
    lines = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip().startswith("{")]
    return json.loads(lines[-1])


def revision_checks(conn) -> None:
    """Run the verify_v1(conn) function of every revision that defines one."""
    for path in sorted((BACKEND / "alembic" / "versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"_rev_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        func = getattr(module, "verify_v1", None)
        if func is None:
            continue
        try:
            func(conn)
            check(f"revision {path.stem}: verify_v1", True)
        except Exception as exc:
            check(f"revision {path.stem}: verify_v1", False, repr(exc))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--way", choices=["unstamped", "stamped"], required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--again", required=True)
    args = parser.parse_args()

    from app import migrate

    engine = sa.create_engine(args.url, poolclass=sa.pool.NullPool)
    url = sa.engine.make_url(args.url)
    sqlite = url.get_backend_name() == "sqlite"

    first = read_report(args.report)
    expected_state = "LEGACY" if args.way == "unstamped" else "BEHIND"
    check("first run ok", first["ok"] is True, first)
    check(f"first run saw {expected_state}", first["state_before"] == expected_state, first["state_before"])
    check("first run reached head", first["revision_after"] == migrate.EXPECTED_HEAD, first["revision_after"])
    if args.way == "unstamped":
        check("unstamped database was stamped first", bool(first["actions"]) and first["actions"][0].startswith("stamp"), first["actions"])
    check("first run did something", bool(first["actions"]), first["actions"])

    again = read_report(args.again)
    check("second run ok", again["ok"] is True, again)
    check("second run did nothing", again["actions"] == [], again["actions"])

    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT version_num FROM alembic_version")).all()
        check("one alembic_version row, at head", [r[0] for r in rows] == [migrate.EXPECTED_HEAD], rows)

        expected = dict(seed_v1.EXPECTED_COUNTS)
        expected.pop("approval_grants")  # the table is dropped by pm90
        expected["memories"] = 1  # the preference and style rows were copied to preferences, then deleted by tl90
        counts = {name: conn.execute(sa.text(f"SELECT COUNT(*) FROM {name}")).scalar_one() for name in expected}
        check("row counts as expected", counts == expected, counts)

        insp = sa.inspect(conn)
        left = sorted(f"{t}.{c}" for t, c in (
            [("assistants", n) for n in ("hatched_at", "interactions", "actions_taken", "corrections", "stage", "onboarded")]
            + [("user_settings", "autonomy_level"), ("user_settings", "tone"), ("user_skills", "learned_notes"),
               ("automation_rules", "requires_confirmation")]
        ) if c in {x["name"] for x in insp.get_columns(t)})
        check("legacy columns are gone", not left, left)
        check("approval_grants is gone", not insp.has_table("approval_grants"))

        modes = dict(conn.execute(sa.text(
            "SELECT u.email, s.permission_mode FROM user_settings s JOIN users u ON u.id = s.user_id")).all())
        kinds = sorted(r[0] for r in conn.execute(sa.text("SELECT kind FROM memories")))
        copied = {r[0] for r in conn.execute(sa.text("SELECT text FROM preferences"))}
        check("1.0.0 values survived", modes == {"owner1@example.com": "ask", "owner2@example.com": "skip"}
              and kinds == ["fact"] and {"Likes bullet-point summaries", "Writes in a warm, brief tone",
                                         "prefers short replies", "no meetings before 10"} <= copied,
              (modes, kinds, sorted(copied)))

        if args.way == "unstamped":
            cols = {c["name"] for c in sa.inspect(conn).get_columns("users")}
            check("extra column kept", "legacy_extra" in cols, sorted(cols))

        # New rows: nothing left of the 1.0.0-only columns, so plain inserts work.
        meta = sa.MetaData()
        meta.reflect(bind=conn)
        tables = dict(meta.tables)
        conn.rollback()  # end the read transaction; the inserts below are undone
        trans = conn.begin()
        try:
            stamp = {"created_at": seed_v1.NOW, "updated_at": seed_v1.NOW}
            uid = seed_v1.insert(conn, tables, "users", email=f"{uuid.uuid4()}@example.com", name="New", timezone="UTC",
                                 is_active=True, is_demo=False, **stamp)
            seed_v1.insert(conn, tables, "assistants", user_id=uid, name="Aura", personality="concise", avatar="teal",
                           pronoun="it", goals=[], **stamp)
            got = conn.execute(sa.text("SELECT count(*) FROM assistants WHERE user_id = :u"), {"u": uid}).scalar()
            check("insert without legacy columns works", got == 1, got)
        except Exception as exc:
            check("insert without legacy columns works", False, repr(exc))
        finally:
            trans.rollback()

        revision_checks(conn)

    # The sign-in path through the current ORM, on a pooler-style engine.
    pe = pooler_engine(args.url)
    try:
        from app.models import Assistant, AutomationRule, User, UserSettings, UserSkill

        with Session(pe) as session:
            user = User(email=f"{uuid.uuid4()}@example.com", name="ORM")
            session.add(user)
            session.flush()
            session.add_all([
                Assistant(user_id=user.id), UserSettings(user_id=user.id),
                UserSkill(user_id=user.id, code="email"), AutomationRule(user_id=user.id, name="r"),
            ])
            session.flush()
            session.rollback()
        check("ORM inserts (assistant, settings, skill, rule) work", True)
    except Exception as exc:
        check("ORM inserts (assistant, settings, skill, rule) work", False, repr(exc))
    finally:
        pe.dispose()

    if sqlite:
        backups = Path(url.database).parent / "backups"
        label = "1.0.0-unstamped" if args.way == "unstamped" else "0001_initial"
        found = sorted(p.name for p in backups.glob(f"*-{label}-*.db")) if backups.is_dir() else []
        check(f"SQLite backup written ({label})", len(found) == 1 and first["backup_path"] is not None
              and Path(first["backup_path"]).is_file(), (found, first["backup_path"]))
    else:
        check("no backup file on PostgreSQL", first["backup_path"] is None, first["backup_path"])

    engine.dispose()
    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
