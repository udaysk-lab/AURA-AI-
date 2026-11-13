# Database migrations

For maintainers and operators. It says how AURA changes its database schema,
what the start-up runner does in each situation, what to do when it fails, and
the rules for writing a new revision. The release checklist is in
[DEPLOY.md](../DEPLOY.md#database-migrations).

- [How it works](#how-it-works)
- [What the runner does in each database state](#what-the-runner-does-in-each-database-state)
- [Locking and transactions](#locking-and-transactions)
- [Backups](#backups)
- [Running it by hand](#running-it-by-hand)
- [When it fails](#when-it-fails)
- [Expand first, contract later](#expand-first-contract-later)
- [The revisions so far](#the-revisions-so-far)
- [Writing a new revision](#writing-a-new-revision)

---

## How it works

Alembic is the only thing that changes the schema. There is no `create_all`, no
"add the missing columns" step, and no setting to choose (`AUTO_CREATE_SCHEMA` is
ignored; one warning is logged if it is still set).

The runner is `backend/app/migrate.py`. Every way of starting AURA calls it
before anything else uses the database:

| Start path | Context | If it fails |
|---|---|---|
| The API (`uvicorn app.main:app`, the hosted site) | `api` | The API still starts. Every `/api` route except `/api/health/preflight` answers `503 schema_not_ready` until it is fixed. The in-process worker is not started. |
| `worker.py` | `worker` | Raises and exits. |
| The desktop app | `desktop` | Shows a dialog naming the reason, the backup file and the log, then exits. Nothing is changed. |
| `python -m app.migrate upgrade` | `cli` | Prints a report and exits non-zero. |

A database already at the newest revision costs one connection and one query.
Alembic is not even imported on that path.

`EXPECTED_HEAD` in `backend/app/migrate.py` is the revision this build expects.
A test fails if it differs from the head of `backend/alembic/versions`, so it is
bumped in the same commit as every new revision.

## What the runner does in each database state

The runner reads the `alembic_version` table and, if it has none, looks at the
tables.

| State | How it is recognised | What happens |
|---|---|---|
| **Empty** | No AURA tables. | Built from the migrations, 0001 to head. No backup (nothing to lose). |
| **1.0.0, unstamped** | Every table and column of the 1.0.0 schema is present and there is no `alembic_version` row (or the table is empty, from an interrupted stamp). Extra columns and tables are tolerated. | SQLite is backed up. Then it is stamped `0001_initial` and upgraded to head, in one transaction. You do not run `alembic stamp`. |
| **Behind** | One `alembic_version` row naming a revision this build knows, not the head (including a 1.0.0 database stamped `0001_initial`). | SQLite is backed up. Upgraded to head in one transaction. |
| **At head** | The row equals `EXPECTED_HEAD`. | Nothing. |
| **Ahead** | The row names a revision this build does not know (a newer AURA made the database). | Never changed. On the hosted site and in the worker and CLI the app serves, and preflight shows a warning, because older code ignores columns and tables it does not know. The CLI exits 2. **The desktop app refuses to start**, so an older build cannot open a newer database. |
| **Partial** | More than one `alembic_version` row, a 1.0.0 table with a column missing, a damaged SQLite file, a failed foreign-key check after the upgrade, or migration scripts that cannot be found. | Left alone and reported. AURA does not guess. |

A **Vercel Preview** deployment never migrates, because a Preview often shares
the production `DATABASE_URL`. It answers `503 schema_not_ready`. Set
`MIGRATE_ON_PREVIEW=true` only on a Preview that has its own database.

Upgrades run in one transaction, so a failure leaves the old schema in place.
After an upgrade the runner checks that exactly one revision row exists and that
it is the head.

## Locking and transactions

Two instances that start together must not both migrate.

- **PostgreSQL.** A transaction-scoped advisory lock, polled once a second, with
  `lock_timeout` 5 s and `statement_timeout` 120 s inside the transaction. It is
  scoped to the transaction, so it works through a transaction pooler
  (Supabase port 6543). The second instance waits, then sees head and stops.
- **SQLite.** An operating-system lock on `<database>.migrate-lock` beside the
  database serialises the whole sequence (check, integrity check, backup,
  upgrade) across processes. The operating system drops it if the process dies,
  so a crash never blocks the next start. The upgrade runs on its own connection
  with `BEGIN IMMEDIATE` and foreign keys off, so a table rebuild does not
  cascade-delete rows.
- The default wait for the lock is 60 s.

A failure to get the lock, or a lost connection, is **transient**: the app
tries again at most once every 30 s per process while requests arrive. A
deterministic failure (a partial database, a failed revision) is not retried
until the next start.

## Backups

- Only for SQLite, and only when an existing database is about to change (1.0.0
  or behind). A new database and a database at head are not backed up.
- Before the backup, `PRAGMA integrity_check` runs. A damaged file blocks the
  upgrade. So does too little free disk space (twice the database size, WAL
  included, is required).
- The copy is made with SQLite's own backup, so it is consistent and includes
  content still in the write-ahead log.
- Where: a `backups/` folder next to the database file. Name:
  `<database name>-<revision before, or 1.0.0-unstamped>-<UTC time>.db`.
- The newest three are kept.
- PostgreSQL gets no backup from AURA. Take one yourself first (on Supabase,
  the project backups; or `pg_dump`).

To go back on the desktop app: reinstall the previous version, then copy the
file from `~/.aura/backups/` over `~/.aura/aura.db`.

## Running it by hand

From `backend/`:

```bash
python -m app.migrate status        # where the database stands; changes nothing
python -m app.migrate upgrade       # the same thing the app does at start
python -m app.migrate check-legacy  # exit 0 only for an unstamped 1.0.0 database
```

`upgrade` prints a JSON report. Exit codes: 0 ok, 1 failed, 2 the database is
ahead of this build.

`docker compose up` needs nothing extra: the API and the worker both check the
schema when they start, and the lock makes that safe.

Developers can also use `alembic upgrade head` and `alembic check` (CI does)
with `DATABASE_URL` set. The runner is what production uses.

Where to look on the hosted site:

- `/health` returns `"schema"`: the revision this process has confirmed, or
  `not_ready`.
- `/api/health/preflight` shows "Database schema" with the state and error.
- The Vercel log line `aura.migrate ERROR` has the detail.

## When it fails

| What you see | Meaning | What to do |
|---|---|---|
| `/api` answers `503 schema_not_ready` for under a minute after a deploy | A cold start is migrating, or waiting for another instance's lock. | Wait. A lock timeout is retried every 30 s. |
| It stays 503 for more than two minutes | The upgrade failed. | Read the preflight detail and the `aura.migrate ERROR` log line. On Vercel, use **Instant Rollback** to the previous deployment; the database is unchanged, so the old code works. Send both messages to the developer. Redeploy the same build at most once. |
| State `PARTIAL` | The database is neither a new one nor a clean 1.0.0. | Do not edit it by hand. Restore a backup, or send the detail to the developer. |
| State `AHEAD` on a server | A newer build already migrated this database. | Deploy the newer build again, or restore a backup taken before it. |
| Desktop dialog "couldn't update its database" | The upgrade failed or the database is from a newer AURA. | Your data is unchanged. For a newer database, install the newer AURA. Otherwise send the log (`~/.aura/logs/aura.log`) to the developer. A backup path is in the dialog when one was made. |
| "not enough disk space for a backup" | The disk is too full to copy the database. | Free space and start again. |
| "database file is damaged" | SQLite's integrity check failed. | Restore the newest file in `backups/`, or a copy from before. |

Rolling back after a deploy whose revisions were all additive is safe: the
database is newer than the old code, which ignores the new columns and tables.
Rolling back past a contract revision (below) is not; that needs a database
restore.

## Expand first, contract later

Changes to existing columns and tables ship in two steps so a rollback is
possible:

1. **Expand.** Revisions that only add: tables, columns, indexes, data copied to
   new places. The old columns and tables stay, unused.
2. **Contract.** A later release, only after the expand release has run on the
   live site for at least 24 hours and been checked. These drop the old columns
   and tables. Their ids end in 90 to 99 (`pm90`, `tl90`).

No contract revision has shipped yet. Until one does, the 1.0 columns and
tables (trust levels, grants, learned notes, the old tone setting) are still in
the database and not used.

## The revisions so far

| Revision | What it does |
|---|---|
| `0001_initial` | The 1.0.0 schema, as the baseline. |
| `0002_legacy_defaults` | Gives the 1.0.0 columns that 2.0 no longer writes a server default, so 2.0 can add rows without them and older code still reads valid values. Nothing is dropped. |
| `pm01_permissions` | Permission mode, rules, approval-card fields, connected folders, app grants and blocks, schedule and automation approval mode, and the permission log. |
| `pm02_permissions_data` | Once per user, maps the 1.0 settings onto the new model: trust level to mode and rules, "always" grants to allow rules, `DEVICE_EXEC_*` and the command denylist to command rules, `DEVICE_ALLOWED_ROOTS` to connected folders, the default blocked-apps list, expiry on waiting approvals. Nobody is put on Auto. It reads install-level settings only at this moment; changing them afterwards does not redo it. |
| `tl01_work_items` | Work items and their steps, and the links from runs, logs, usage and approvals to a work item. |
| `tl02_preferences` | Preferences, the memory pause and per-chat switches, and the copy of 1.0 preference facts, skill notes and tone into preferences. |

## Writing a new revision

After a model change, from `backend/`:

```bash
alembic revision --autogenerate -m "what changed"
```

then edit what it wrote to follow these rules. Tests and CI enforce most of
them.

**Ids and chain**
- One linear chain, one head. No branches.
- The id is `<area><NN>_<slug>`: two lowercase letters, two digits, an
  underscore, then letters, digits and underscores; at most 32 characters. The
  file is `<id>.py`. Areas used: `pm` permissions, `tl` task loop and memory.
- `NN` 01 to 89 is expand; 90 to 99 is contract. No expand revision may come
  after a contract revision.
- `down_revision` is the head when you merge. Bump `EXPECTED_HEAD` in
  `backend/app/migrate.py` in the same commit.
- Update the revision named in `desktop/RELEASE_NOTES.md` and in the DEPLOY.md
  checklist's expected `/health` value when a release includes the revision.

**Both databases**
- Write every change inside `with op.batch_alter_table(...)`. Give
  `existing_type`, `existing_nullable` and `existing_server_default` on every
  `alter_column`.
- Server defaults come only from the helpers in `app.migration_ops`
  (`sql_true`, `sql_false`, `sql_now`, `sql_json_empty_list`,
  `sql_json_empty_object`, `sql_str`, `sql_int`), which render correctly for
  each database.
- A new `NOT NULL` column on an existing table needs a server default.
- Name every constraint and index (`ix_<table>_<col>`, `uq_<table>_<cols>`,
  `fk_<table>_<col>`).
- Types: `sa.String(n)`, `Text`, `Integer`, `Boolean`, `DateTime(timezone=True)`
  and `sa.JSON` (not `JSONB`). Ids are `String(36)`.

**Guards**
- Every create or add goes through a guard from `app.migration_ops`:
  `create_table_if_missing`, `add_column_if_missing`, `create_index_if_missing`.
  Contract drops use `drop_column_if_present` and `drop_table_if_present`. They
  make a revision safe on databases an older start-up already touched.

**Data changes**
- Use SQLAlchemy Core with `sa.table(...)` and `sa.column(...)` stubs declared in
  the revision.
- Import nothing from the app except `app.migration_ops`. Do not import models,
  config, the database module or any service. A mapping you need is written out
  in the revision as a plain literal. Install-level settings are read through
  `migration_ops.install_env()` and nowhere else.
- Make it safe to run twice: insert only what is absent, keyed on a natural key.
- Do not call the network, read secrets or `print`. Log with
  `logging.getLogger("alembic.runtime.migration")`.
- Do nothing that cannot run inside a transaction.

**Downgrades**
- An expand revision that loses no data implements `downgrade()`.
- A data or contract revision raises `NotImplementedError("irreversible; restore
  a backup")`.
- The runner never downgrades. A downgrade exists for developers and the round
  trip test.

**Tests**
- `backend/tests/test_migrations.py` runs every revision: an empty database to
  head with no differences from the models, a seeded 1.0.0 database and a
  database stamped `0001_initial` to head with rows intact, and a round trip when
  the revision is reversible. Add a `test_<revision id>_data` for any data
  change. The PostgreSQL cases run when `AURA_TEST_POSTGRES_URL` is set.
- CI builds a 1.0.0 database both ways on SQLite and PostgreSQL, upgrades it,
  races two runners on PostgreSQL, and runs `alembic check`.
