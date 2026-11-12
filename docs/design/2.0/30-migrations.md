# 30 — Schema migrations: Alembic as the only schema path (AURA 2.0)

Binding: 00-decisions.md items 13-14. Paths are relative to aura-ai-/backend unless noted. "Hosted" = Vercel + Supabase
(any non-LOCAL_MODE server); "desktop" = aura_desktop.py, source or frozen. DECISION: marks what 00-decisions.md leaves open.

## 0. Facts this spec relies on (checked in code)
- `alembic==1.19.2` is already a runtime requirement in requirements.txt; `mako` (its dependency) is imported at module level
  by `alembic.util.pyfiles`. No requirement changes. desktop/aura.spec EXCLUDES both and ships no scripts (fixed in 5).
- env.py calls `fileConfig(alembic.ini)`, which inside the app would reset its logging; it is rewritten (5).
  `alembic_version.version_num` is VARCHAR(32), so revision ids are at most 32 characters.
- 7 test modules build the schema with create_all; test_cold_start tests apply_additive_migrations.
- Prototype (outside the repo): with `cfg.attributes["connection"]` in a runner-held transaction Alembic does not commit;
  on SQLite with `isolation_level=None` + `BEGIN IMMEDIATE`, stamp + DDL roll back as one unit. Importing alembic and
  loading ScriptDirectory costs about 70 ms, so the fast path does not import alembic.

## 1. The runner

### 1.1 Modules and API
- `app/migrate.py` (new): the runner, its policy wrappers and the CLI. It imports alembic lazily, only on the slow path.
- `app/migrate_legacy.py` (new): `LEGACY_1_0_0: dict[str, frozenset[str]]`. This maps each table to its columns exactly as
  0001 creates them (29 tables), written out as a literal. A test proves the snapshot equals what 0001 builds.
- `app/migration_ops.py` (new): helpers that migration scripts import (section 6). It also holds `LEGACY_PENDING_DROP`.

```python
EXPECTED_HEAD = "0002_legacy_defaults"   # bumped by every revision; test asserts == ScriptDirectory head

class State(StrEnum): EMPTY, LEGACY, BEHIND, AT_HEAD, AHEAD, PARTIAL
class SchemaError(Exception):        # deterministic failure: no automatic retry
    state: State; detail: str
class SchemaBusy(Exception): ...     # lock wait timed out: transient

@dataclass
class MigrationReport:
    state_before: State; revision_before: str | None; revision_after: str | None
    actions: list[str]          # e.g. ["stamp 0001_initial", "upgrade 0001_initial -> 0002_legacy_defaults"]
    backup_path: str | None; cached: bool; duration_ms: int
    ok: bool; transient: bool; error: str | None

def upgrade_to_head(engine: Engine, *, allow_migrate: bool = True, backup: bool = True,
                    lock_wait_s: float = 60.0) -> MigrationReport      # raises SchemaError / SchemaBusy / DB errors
def ensure_schema(context: Literal["api", "worker", "desktop", "cli"]) -> MigrationReport   # policy wrapper
def status() -> MigrationReport | None     # last report of this process (no I/O)
def migrations_dir() -> Path               # section 5
def alembic_config(connection=None) -> alembic.config.Config
def main(argv=None) -> int                 # `python -m app.migrate upgrade|status|check-legacy` (has a __main__ guard)
```

### 1.2 Call sites (all of them; nothing else touches the schema)
| Where | Call | On failure |
|---|---|---|
| `app/main.py` lifespan, replacing `init_db()` | `ensure_schema("api")` | never raises; see 1.4 |
| `worker.py` `main()` and `run_once()`, replacing `init_db()` | `ensure_schema("worker")` | log, `sys.exit(1)` |
| `aura_desktop.py` `main()`, after `apply_environment` and `os.chdir(home)`, before `Server(...)` | `ensure_schema("desktop")` | dialog, exit 3 |
| CLI `python -m app.migrate upgrade` | `ensure_schema("cli")` | prints the report as JSON; exit 1 |
| `app/api/cron.py` | nothing: it runs inside the app, where lifespan already ran | — |

The desktop launcher runs the runner first. When the lifespan calls it again in the same process, it hits the
per-process cache and costs nothing (1.5).

### 1.3 State machine
`classify(conn, known_revisions)` runs a fixed number of queries on one connection:
```
if not table_exists(conn, "alembic_version"):  rows = []      # pg: to_regclass(current_schema()||'.alembic_version'); sqlite: sqlite_master
else: rows = SELECT version_num FROM alembic_version
if len(rows) > 1:                    return PARTIAL("alembic_version has N rows; history is linear")
if len(rows) == 1:
    rev = rows[0]
    if rev == EXPECTED_HEAD:        return AT_HEAD
    if rev in known_revisions:      return BEHIND
    return AHEAD                     # unknown to this code: a newer build (or garbage; the two cannot be told apart)
# no row, or an empty alembic_version (DECISION: an interrupted stamp counts as unstamped)
cols = one query: {table: {columns}} for the current schema
       pg:     SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = current_schema()
       sqlite: SELECT m.name, p.name FROM sqlite_master m JOIN pragma_table_info(m.name) p WHERE m.type = 'table'
present = LEGACY_1_0_0.keys() & cols.keys()
if not present:                      return EMPTY      # unrelated tables (e.g. other apps in public) are ignored
missing = sorted((t, c) for t, cs in LEGACY_1_0_0.items() for c in cs if c not in cols.get(t, ()))
if missing:                          return PARTIAL("missing: " + first 10 of missing)
return LEGACY                        # extra columns and extra tables are tolerated
```
The legacy shape check compares names only. It does not compare types, nullability or indexes. The 1.0.0 boot path
(create_all + add-missing-columns) always produced every 0001 table and column, so a shortfall means the database is
something else. DECISION: PARTIAL is never repaired automatically.

What the runner does for each state:
| State | Action | Result |
|---|---|---|
| EMPTY | `upgrade head` | ok |
| LEGACY (unstamped 1.0.0) | SQLite: integrity check + backup; then `stamp 0001_initial`, then `upgrade head`, in ONE transaction | ok |
| BEHIND (stamped 0001 or any known older revision) | SQLite: integrity check + backup; `upgrade head` | ok |
| AT_HEAD | nothing | ok |
| AHEAD | nothing; WARNING log | hosted: ok (serve). desktop: SchemaError (1.4) |
| PARTIAL | nothing | SchemaError |

### 1.4 `upgrade_to_head` pseudocode
```
key = engine.url.render_as_string(hide_password=True)
if _CACHE.get(key) == EXPECTED_HEAD: return report(AT_HEAD, cached=True)            # 0 queries
with engine.connect() as conn:                                                       # fast path: 1 connection
    try:    rows = conn.exec_driver_sql("SELECT version_num FROM alembic_version").all()   # 1 query
    except DBAPIError: rows = None; conn.rollback()
    if rows is not None and len(rows) == 1 and rows[0][0] == EXPECTED_HEAD:
        _CACHE[key] = EXPECTED_HEAD; return report(AT_HEAD)
# slow path (rare): load scripts
script = ScriptDirectory.from_config(alembic_config())
if script.get_current_head() != EXPECTED_HEAD: raise SchemaError(PARTIAL, "packaging: EXPECTED_HEAD != scripts head")
known = {r.revision for r in script.walk_revisions()}
mig_engine = engine if pg else _sqlite_migration_engine(engine.url)
with mig_engine.connect() as conn:
    pre = classify(conn, known)                 # outside the lock: only to decide on a backup, and on AHEAD/PARTIAL
    if pre in (AHEAD, PARTIAL): return/raise per table above
    if pre in (LEGACY, BEHIND) and not allow_migrate: raise SchemaError(pre, "migration not allowed here")
    if sqlite and pre in (LEGACY, BEHIND) and backup: integrity_check(conn); backup_path = backup_sqlite(...)
    with locked_transaction(conn, lock_wait_s):          # 2.1 / 2.3
        state = classify(conn, known)                    # re-read under the lock: another process may have finished
        if state == LEGACY:          command.stamp(cfg(conn), "0001_initial"); actions += ["stamp 0001_initial"]
        if state in (EMPTY, LEGACY, BEHIND): command.upgrade(cfg(conn), "head"); actions += [...]
        if state in (AHEAD, PARTIAL): raise/return as above
        if sqlite: assert no rows from "PRAGMA foreign_key_check" else raise SchemaError(PARTIAL, rows[:5])
        assert read_revision(conn) == EXPECTED_HEAD
    # commit happens when the with-block exits
_CACHE[key] = EXPECTED_HEAD
```
`cfg(conn)` is `alembic_config(conn)`: a Config with `attributes["connection"] = conn`. Alembic then sees an external
transaction and neither begins nor commits one. The runner never runs a downgrade.

### 1.5 Policy wrapper `ensure_schema(context)` and the failure decision
- Engine: `app.db.engine`. allow_migrate is False only when `VERCEL_ENV == "preview"` and `MIGRATE_ON_PREVIEW` is not
  `true`. DECISION: a branch preview must never migrate the live database (Vercel env vars are often shared with Preview).
- Each call stores its report in the module-level `_LAST` (read by `status()`). Logging: INFO with one line per action and a
  total; ERROR on failure with `state`, `detail`, and (SQLite) the backup path. Passwords never appear (render with
  hide_password).
- DECISION, hosted ("api"): never raise and never crash startup. Failures are recorded, and `SchemaGuardMiddleware`
  (app/middleware.py) answers every `/api/*` path except `/api/health/preflight` with 503
  `{"detail": "AURA is finishing a database update. Try again shortly.", "code": "schema_not_ready"}` and `Retry-After: 30`.
  `/health`, `/health/ready`, docs and static files pass through.
  - If the failure is transient (`SchemaBusy`, `sqlalchemy.exc.OperationalError`/`InterfaceError`, SQLSTATE 55P03
    lock_not_available, 57014 query_canceled), the middleware re-runs `ensure_schema("api")` at most once per 30 s per
    process. It runs it in the threadpool under a `threading.Lock`; the request that triggered it waits for the result.
    A deterministic failure is not retried in-process. The next cold start retries it.
  - Why: a crashing lifespan shows on Vercel only as an opaque FUNCTION_INVOCATION_FAILED, and the operator cannot reach
    the database, so a running app that reports the cause (preflight, logs) is the only diagnostic channel. Each upgrade is
    one transaction, so a failure leaves the old schema intact for an Instant Rollback (10). (1.0.0 swallowed it and 500'd.)
  - AHEAD on hosted: serve normally with a WARNING. This is the expected state during a rollout or an Instant Rollback
    after an expand migration, and expand/contract guarantees that the previous code works.
- DECISION, desktop ("desktop"): fail closed. On any failure, AHEAD included, `show_error()` (or stderr when headless) says:
  "AURA couldn't update its database (<state>: <detail>). Your data is unchanged[; a copy was saved at <backup_path>].
  Details: <log file>". The launcher exits with code 3. With `--smoke-report` it writes
  `{"failed": ["database upgrade"], "error": ...}`.
  - Why: one user, no rollout skew. AHEAD means an older build was installed, whose models may expect columns a newer
    contract dropped. Rollback + backup make stopping safe, and the message says what to do.
- "worker": raise after logging, so a supervisor (docker restart policy) retries. A worker never ticks against an unknown
  schema.
- "cli": print `json.dumps(asdict(report))`. Exit codes: 0 ok, 1 failure, 2 AHEAD.
- `/health` adds `"schema": <revision_after or "not_ready">`, taken from `status()` (no DB call).
- `app/preflight.py` gains a check `schema`/"Database schema". Its level is "ok" at head (detail: the revision) and "warn"
  when AHEAD or failed (detail: state + detail, fix: "Redeploy; if it persists, roll back (DEPLOY.md, Migrations)").
  DECISION: it is never "fail", because a production preflight failure raises in the lifespan and would hide the cause.

## 2. Locking and transactions

### 2.1 Postgres, which must work through the Supabase transaction pooler (pgbouncer transaction mode, port 6543)
- Session advisory locks (`pg_advisory_lock`) are unsafe behind a transaction pooler: the server session changes between
  transactions, so the lock leaks or is released on the wrong backend. Only transaction-scoped locks are used.
- `LOCK_KEY = 0x41555241` (1096107585, "AURA"). DECISION.
- `locked_transaction(conn, wait)`. Each attempt is its own short transaction:
  `BEGIN; SELECT pg_try_advisory_xact_lock(LOCK_KEY)`. On false: `ROLLBACK`, sleep 1 s, retry until `wait` (60 s) has
  passed, then raise `SchemaBusy`. On true, stay in that same transaction:
  `SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '120s'`. Then classify, stamp, upgrade, verify and COMMIT.
  The advisory lock is released at commit or rollback.
- Alembic transaction mode: one external transaction for the whole upgrade (no `transaction_per_migration`). Postgres DDL
  is transactional, so the upgrade is all-or-nothing. Consequence (rule 6.4): no `CREATE INDEX CONCURRENTLY`, no
  `ALTER TYPE ... ADD VALUE`, no `VACUUM`, no `autocommit_block` in this release.
- The 5 s lock_timeout stops DDL from queueing an ACCESS EXCLUSIVE lock behind live traffic from old instances and stalling
  them. A timeout rolls everything back and is transient (1.5).
- The runner uses the app engine (NullPool + `prepare_threshold=None` behind the pooler, as db.py configures).
  DECISION: there is no separate migration URL.

### 2.2 Two serverless instances start together
A takes the lock and migrates (well under 5 s for this release). B polls once a second. After A commits, B takes the lock,
classifies AT_HEAD, commits an empty transaction and caches. Neither instance serves requests before its lifespan returns.
If A's function is killed mid-upgrade, its connection closes, Postgres rolls the transaction back and releases the lock,
and B (or the next cold start) runs the whole upgrade.

### 2.3 SQLite
- `_sqlite_migration_engine(url)`: `create_engine(url, poolclass=NullPool, connect_args={"check_same_thread": False})` with:
  - on connect: `dbapi.isolation_level = None`, `PRAGMA busy_timeout = 30000`, `PRAGMA foreign_keys = OFF`;
  - on begin: `conn.exec_driver_sql("BEGIN IMMEDIATE")`.

  This gives a transactional upgrade (verified by the prototype). It also stops batch-mode table rebuilds from firing
  ON DELETE CASCADE: the app's engine sets `foreign_keys=ON`, and SQLite turns `DROP TABLE` into a delete. The app engine's
  pool never sees these settings.
- The lock is `BEGIN IMMEDIATE`, which takes SQLite's write lock and waits up to busy_timeout (30 s), then raises
  OperationalError (transient).
- Desktop runs one process: `running_aura()` hands a second launch to the first. The launcher migrates before uvicorn and
  before the embedded worker start, so nothing else touches the file. Developer setups with uvicorn plus a separate
  worker.py on one file are serialised by BEGIN IMMEDIATE, and the loser classifies AT_HEAD.
- `:memory:` and empty-path URLs skip the backup and the integrity check.

## 3. Serverless cost
- Cold start at head: one connection and one query (`SELECT version_num FROM alembic_version`), with no alembic import and
  no ScriptDirectory. This replaces today's create_all (one has_table query per table) plus information_schema scans, so a
  cold start gets cheaper. Later lifespans and worker calls in the same process cost nothing (cache keyed by URL).
- Slow path (once per release): about 70 ms (alembic + scripts) plus lock and DDL, paid by the first cold start(s) only.
- Old instances during a rollout keep running the previous code and never re-check. Expand-only revisions keep that code
  working: new columns are nullable or have a server default, new tables are unknown to it, and legacy columns keep NOT NULL
  plus a server default (section 9).
  A 1.0.0 instance cold-starting after 2.0 migrated runs its create_all + add-missing-columns, which finds its columns and
  changes nothing; after a contract it would re-add dropped columns (harmless), which is why contract waits (9.3).

## 4. SQLite safety (desktop and local)
- Integrity: `PRAGMA integrity_check` must return exactly one row, `ok`. It runs only when the state is LEGACY or BEHIND,
  i.e. once per upgrade. On failure: SchemaError(PARTIAL, "database file is damaged"), no backup, no migration.
- Backup (DECISION):
  - Location: `<db dir>/backups/`, e.g. `~/.aura/backups/` (inside the protected data folder, decision 6).
  - Name: `<db stem>-<from>-<UTC %Y%m%dT%H%M%SZ>.db`, where `<from>` is the stamped revision or `1.0.0-unstamped`.
    Example: `aura-0001_initial-20261112T090000Z.db`.
  - Method: `sqlite3.connect(src).backup(sqlite3.connect(tmp))`, which yields a consistent copy that includes WAL content.
    Copying the raw file would miss the WAL. The copy is written to `<name>.part`, then `os.replace` gives it its final name.
  - Before the copy, require free space of at least 2 x (db size + wal size) (`shutil.disk_usage`). Otherwise fail with
    SchemaError(..., "not enough disk space for a backup"); there is no migration without a backup.
  - Retention: keep the 3 newest `<stem>-*.db` by mtime; delete older ones after a successful upgrade only. Deletion
    failures are logged, not fatal.

## 5. Packaging
- `alembic_config(connection=None)`:
  ```
  cfg = Config()                                  # no ini file: independent of cwd and alembic.ini
  cfg.set_main_option("script_location", str(migrations_dir()).replace("%", "%%"))
  cfg.set_main_option("path_separator", "os")
  if connection is not None: cfg.attributes["connection"] = connection
  ```
- `migrations_dir()`: if `getattr(sys, "frozen", False)`, use `Path(sys._MEIPASS) / "aura_migrations"`; otherwise use
  `Path(app.__file__).resolve().parent.parent / "alembic"` (source runs and the Vercel bundle, whose root is backend/).
  Require `env.py` and `versions/` to exist, else SchemaError(PARTIAL, "migration scripts not found at <path>").
  DECISION: the frozen target folder is named `aura_migrations`, not `alembic`, so it can never shadow the alembic package.
- `alembic/env.py` rewrite:
  ```
  config = context.config
  conn = config.attributes.get("connection")
  if config.config_file_name and conn is None: fileConfig(config.config_file_name, disable_existing_loggers=False)  # CLI only
  def include_object(obj, name, type_, reflected, compare_to):     # keeps `alembic check` clean during expand
      if reflected and compare_to is None:
          if type_ == "table" and (name, None) in LEGACY_PENDING_DROP: return False
          if type_ == "column" and (obj.table.name, name) in LEGACY_PENDING_DROP: return False
      return True
  def run(connection):
      context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True,
                        render_as_batch=connection.dialect.name == "sqlite", include_object=include_object)
      with context.begin_transaction(): context.run_migrations()
  if conn is not None: run(conn)
  else: build NullPool engine from settings.database_url (+ the pooler connect_args from db.py); with it.connect() as c: run(c)
  ```
  Offline mode stays as it is. `alembic.ini` stays for authoring (`alembic revision`, `alembic check`).
- Vercel: the backend service root is `backend/`, and there is no .vercelignore, so `alembic/` is bundled with `app/`.
  alembic and mako come from requirements.txt. Verification after deploy: `/health` shows `"schema": "<head>"` (runbook).
- desktop/aura.spec:
  - remove `"alembic"` and `"mako"` from `excludes`;
  - `hiddenimports += collect_submodules("alembic", filter=lambda n: not n.startswith("alembic.testing")) + collect_submodules("mako")`;
  - `datas += [(str(BACKEND/"alembic"/"env.py"), "aura_migrations"), (str(BACKEND/"alembic"/"script.py.mako"), "aura_migrations"),
    (str(BACKEND/"alembic"/"versions"/"*.py"), "aura_migrations/versions")]` (a glob, so no `__pycache__`).

  `collect_submodules("app")` already brings in app.migrate, app.migrate_legacy and app.migration_ops. Frozen Python can
  run the .py scripts from disk.
- `--smoke-test` (aura_desktop.smoke_test) adds:
  - `check("database schema at head", health.get("schema") == EXPECTED_HEAD)`, using the `/health` JSON already fetched;
  - `check("no schema guard", status of GET /api/auth/config == 200)` (already asserted; keep it as the 503 detector);
  - after the server stops: `check("one alembic_version row", ...)`, opening `<home>/aura.db` with sqlite3 and asserting
    exactly one row equal to EXPECTED_HEAD.

  The smoke home is new, so this exercises the EMPTY path, i.e. every script, frozen, on all 4 release builds. The legacy
  path is pure Python and is covered by CI (section 8).

## 6. Authoring rules for every revision in this release
### 6.1 Ids, files, chain (applies to all specs, including 20-taskloop-memory.md)
- DECISION: there is one linear chain. Ids are area-prefixed and are NOT renumbered at landing:
  - `0001_initial` (exists) and `0002_legacy_defaults` (this spec) are fixed.
  - Every later revision is `<area><NN>_<slug>`, matching `^[a-z]{2}\d{2}_[a-z0-9_]+$`, at most 32 characters, and the
    file name equals the id + `.py`.
  - Areas: `tl` task loop/memory (20-spec: tl01..tl03), `pm` permissions/rules/approval modes, `sy` systems/computer
    access, `sc` this spec.
  - NN 01-89 are expand revisions (additive + data transforms); NN 90-99 are contract revisions.
  - Prefixes keep ids stable across rebases, so test data, docs and cross-spec references never change.
- `down_revision` = the head on the branch when the package lands. The LEAD sets it at landing (a one-line edit) and bumps
  `EXPECTED_HEAD` in app/migrate.py in the same commit. Implementers write `down_revision = "<current head when you
  started>"` and note it in their report.
- Graph order is what counts. Alembic orders by the chain, not by names.
- Tests enforce: a single head, no branch points, the id regex and length, and that EXPECTED_HEAD equals the head.
- Every contract revision (NN >= 90) must sit after every expand revision (a test checks that no expand revision follows a
  contract revision).

### 6.2 Both dialects, batch mode
- Write every ALTER inside `with op.batch_alter_table(table) as b:` (the default `recreate="auto"`). This issues plain
  ALTERs on Postgres and rebuilds the table on SQLite when needed. Give `existing_type`, `existing_nullable` and
  `existing_server_default` on every `alter_column`.
- Server defaults come only from the `app.migration_ops` helpers, which render per dialect:
  `sql_true()`/`sql_false()` ('true'/'false' vs '1'/'0'), `sql_now()` (now() vs CURRENT_TIMESTAMP),
  `sql_json_empty_list()`/`sql_json_empty_object()` ('[]'::json vs '[]'), `sql_str(s)` (quoted literal), `sql_int(n)`.
- New NOT NULL columns on existing tables must have a server_default. Constraints and indexes must be named
  (`ix_<table>_<col>`, `uq_<table>_<cols>`, `fk_<table>_<col>`), because batch mode needs names to drop them later.
- Types: use sa.String(n), Text, Integer, Boolean, DateTime(timezone=True) and sa.JSON (not JSONB), to match 0001.
  Keep ids at String(36).

### 6.3 Guards
1.0.0 dev databases, and databases from the aura-2.0 branch before the runner landed, may already hold objects that
create_all made. Every create or add goes through a guard: `create_table_if_missing`, `add_column_if_missing`,
`create_index_if_missing`. Every contract drop is guarded the same way: `drop_column_if_present`, `drop_table_if_present`.
All of them live in app/migration_ops.py and reflect through `sa.inspect(op.get_bind())`.

### 6.4 Data transforms
- Use SQL Core against `sa.table(...)`/`sa.column(...)` stubs declared in the revision, via `op.get_bind()`.
- Never import `app.models`, `app.config`, `app.db` or any service; only `app.migration_ops` may be imported. Any mapping
  (for example task-type maps, or decision 14's trust-to-rules table) is a frozen literal inside the revision.
- Transforms must be idempotent: insert-if-absent keyed on a natural key, so re-running is a no-op.
- Iterate in Python only over rows that need work. Generate ids with `uuid.uuid4()` and timestamps with
  `datetime.now(timezone.utc)`.
- No network calls, no secrets, no `print`; use `logging.getLogger("alembic.runtime.migration")`.
- Nothing non-transactional (2.1).

### 6.5 Downgrade policy
- DECISION: expand revisions without data loss implement `downgrade()` (drop what they added, remove the defaults they
  set). Data-transform and contract revisions raise `NotImplementedError("irreversible; restore a backup")`.
- The runner never downgrades. Downgrade exists for developers and for the round-trip test.

### 6.6 Harness each revision must pass (tests/test_migrations.py, parametrised; no new file per revision)
EMPTY -> head with no `compare_metadata` diffs (SQLite; Postgres when `AURA_TEST_POSTGRES_URL` is set); seeded LEGACY and
seeded stamped-0001 -> head with rows intact; its own `test_<revision id>_data`; a downgrade/upgrade round trip if
reversible; `test_unmapped_columns_are_safe`; `alembic check` in CI.

## 7. Removing create_all, apply_additive_migrations and AUTO_CREATE_SCHEMA
- `app/db.py`: delete `_default_literal`, `apply_additive_migrations` and `init_db`. Keep engine, SessionLocal, Base,
  get_db and `_is_transaction_pooler`. Every caller switches to `app.migrate.ensure_schema` (1.2).
- `app/config.py`: delete `auto_create_schema`. Settings already ignore unknown env vars. `ensure_schema` logs one WARNING
  if `AUTO_CREATE_SCHEMA` is set: "AUTO_CREATE_SCHEMA is no longer used; migrations run automatically".
- Tests (keep the suite at about 30 s). New `tests/schema.py`:
  - `ensure_test_schema(engine)`: create_all, then create `alembic_version` if missing and set its single row to
    `EXPECTED_HEAD`;
  - `drop_test_schema(engine)`: drop_all + `DROP TABLE IF EXISTS alembic_version`.

  The 7 modules swap `Base.metadata.create_all(...)` -> `ensure_test_schema(engine)` and `drop_all` -> `drop_test_schema`.
  Their lifespans then take the 1-query fast path. DECISION: tests keep create_all through this helper for speed.
  Parity with the migrations is enforced by `test_models_match_migrations` (an `alembic check` equivalent on SQLite, about
  0.5 s) and by `alembic check` in CI.
- test_cold_start.py: replace the two apply_additive tests with `test_head_check_is_one_connection_one_query` and
  `test_cached_check_opens_no_connection`, using the same NullPool counting fixture (plus a statement counter via the
  `before_cursor_execute` event).
- docker-compose.yml: the api `command` becomes `uvicorn app.main:app --host 0.0.0.0 --port 8000`, with the
  `alembic upgrade ... || echo` line and its comment removed. The worker needs no change (it calls ensure_schema).
  The api and worker starting together is covered by the advisory lock.
- DEPLOY.md "Database migrations" is replaced by section 10's text (no `alembic stamp`, no AUTO_CREATE_SCHEMA).
  README.md:433-436 gets one sentence: "Schema changes are Alembic revisions; the app applies them at start-up."

## 8. CI (.github/workflows/ci.yml)
- Keep the backend matrix. It now also runs tests/test_migrations.py on SQLite on 3 OSes.
- Remove the AUTO_CREATE_SCHEMA env from the "Migrations match the models" step; keep `alembic upgrade head` + `alembic check`.
- New job `migrations` (ubuntu-latest, Python 3.12), matrix `db: [sqlite, postgres]`. For postgres it adds
  `services.postgres: image postgres:16, env POSTGRES_USER/PASSWORD/DB=aura, ports 5432:5432, health-cmd pg_isready`.
  Steps:
  1. Check out the branch into `.`, and `v1.0.0` into `legacy/` (`actions/checkout`, `ref: v1.0.0`, `path: legacy`).
     Install backend/requirements.txt. 1.0.0 has the same pins, so one venv serves both trees.
  2. URL: SQLite `sqlite:///$RUNNER_TEMP/m.db`; Postgres `postgresql+psycopg://aura:aura@localhost:5432/aura`.
     The script also builds a second engine with `prepare_threshold=None` + NullPool, to mimic the pooler.
  3. For each `way` in `unstamped` and `stamped`, on a fresh database:
     - unstamped: `cd legacy/backend && DATABASE_URL=$URL python -c "from app.db import init_db; init_db()"`. This is
       exactly the old boot path. Then add one extra column (`ALTER TABLE users ADD COLUMN legacy_extra VARCHAR(10)`) to
       prove tolerance.
     - stamped: `cd backend && DATABASE_URL=$URL python -m alembic upgrade 0001_initial`.
     - Seed: `python backend/tests/migrations/seed_v1.py $URL` (SQL Core only): 2 users, an assistant each (stage
       'trusted', onboarded true, counters set), user_settings autonomy 'relaxed' and 'full', an 'always' approval_grant,
       user_skills with learned_notes, memories of kinds preference/style/fact, an automation_rule, a schedule, a
       conversation with 3 messages, a document with 2 chunks.
     - Run: `cd backend && DATABASE_URL=$URL python -m app.migrate upgrade`; exit 0.
     - Verify: `python backend/tests/migrations/verify_v1.py $URL --way <way>`: state_before LEGACY/BEHIND (CLI JSON); one
       alembic_version row equal to head; row counts unchanged; SQLite+unstamped: a backup file under backups/; inserting
       a new assistant/user_settings/user_skill/automation_rule through the current ORM with no legacy attributes succeeds
       (the sign-in `get_or_create` path); each revision's `verify_v1(conn)` function, if it defines one.
     - Run `python -m app.migrate upgrade` again: exit 0, `actions == []`.
     - Then `DATABASE_URL=$URL python -m alembic check`.
  4. Postgres only: on a fresh unstamped database, start two `python -m app.migrate upgrade` processes at once (`&` + wait).
     Both exit 0, the revision is head, and exactly one report has non-empty `actions`.
- Locally: `/home/claude/scripts/pg.sh start`, then `export AURA_TEST_POSTGRES_URL=$(/home/claude/scripts/pg.sh url)`.
  Use `pg.sh reset [db]` for a clean database and `pg.sh stop` when done. The cluster runs as user `claude` on
  127.0.0.1:55432, user `aura`, trust auth. `python -m pytest -q -p no:cacheprovider tests/test_migrations.py` then also
  runs the Postgres cases. The CI scripts take the same URL. A 1.0.0 tree for the unstamped way, without touching the
  repo's worktree list: `mkdir /tmp/aura-v1 && git -C /home/claude/aura-ai- archive v1.0.0 | tar -x -C /tmp/aura-v1`.

## 9. Expand/contract for this release
### 9.1 `0002_legacy_defaults` (expand; down_revision `0001_initial`)
The code that stops writing these columns lands before they are dropped. Without server defaults, an INSERT that omits a
NOT NULL column fails, and `get_or_create` of the Assistant runs on every sign-in. Each column keeps NOT NULL and gains a
server default, so old code still reads valid values from rows that new code creates.
| Table.column | 0001 type | Postgres | SQLite |
|---|---|---|---|
| assistants.hatched_at | DateTime(tz) | `SET DEFAULT now()` | `DEFAULT CURRENT_TIMESTAMP` |
| assistants.interactions / actions_taken / corrections | Integer | `SET DEFAULT 0` | `DEFAULT 0` |
| assistants.stage | String(30) | `SET DEFAULT 'stranger'` | `DEFAULT 'stranger'` |
| assistants.onboarded | Boolean | `SET DEFAULT true` | `DEFAULT 1` |
| user_settings.autonomy_level | String(20) | `SET DEFAULT 'conservative'` | `DEFAULT 'conservative'` |
| user_settings.tone | String(50) | `SET DEFAULT 'concise-professional'` | `DEFAULT 'concise-professional'` |
| user_skills.learned_notes | JSON | `SET DEFAULT '[]'::json` | `DEFAULT '[]'` |
| automation_rules.requires_confirmation | Boolean | `SET DEFAULT true` | `DEFAULT 1` |
DECISION: `onboarded` defaults to true. During the rollout, a user created by 2.0 code who hits a 1.0.0 instance must not
be sent into the removed hatch flow.

DECISION: `learned_notes` and `requires_confirmation` are included pre-emptively. The 20-spec drops `UserSkill.learned_notes`
in its contract, and decision 7's `approval_mode` replaces `requires_confirmation`. A default on a column that is still
mapped is harmless.

Operations: one `batch_alter_table` per table, with `alter_column(col, existing_type=<0001 type>, existing_nullable=False,
server_default=<helper>)`. On Postgres these are catalog-only `ALTER COLUMN ... SET DEFAULT` statements: no rewrite, and
an ACCESS EXCLUSIVE lock held for milliseconds. On SQLite, batch mode rebuilds the 4 tables, keeping extra legacy columns,
`ix_*` indexes, `uq_user_skill` and the FK to users. Downgrade: the same columns with `server_default=None`. There is no
data transform.
- `app/migration_ops.LEGACY_PENDING_DROP` starts as `{("assistants","hatched_at"), ("assistants","interactions"),
  ("assistants","actions_taken"), ("assistants","corrections"), ("assistants","stage"), ("assistants","onboarded"),
  ("user_settings","autonomy_level"), ("user_settings","tone"), ("user_skills","learned_notes"),
  ("automation_rules","requires_confirmation"), ("approval_grants", None)}`.
- Each entry only takes effect once the model attribute is gone (include_object in 5).
- Rule for every 2.0 model change: if a mapped NOT NULL column loses its attribute and is not in this table, the lead adds it
  to 0002 BEFORE that change lands, or the owning area adds a `<area>NN_` expand revision that sets a server default.
  `test_unmapped_columns_are_safe` enforces this: at head (SQLite), every DB column with no model attribute must be nullable
  or have a server default, and must be in LEGACY_PENDING_DROP.

### 9.2 Order of expand revisions
0002 lands first, in the same work package as the runner. It must be on the live site before or with any code that stops
writing those columns. Area expand revisions follow (tl01..tl03, pm01.., sy01..). Data transforms that read legacy columns
run inside these expand revisions, while the columns still exist: decision 14 (autonomy_level + always-grants -> modes and
rules) in `pm`, and learned_notes/tone/memory kinds -> preferences in `tl02`.

### 9.3 Contract (later commit)
- Revisions `pm90_contract`, `tl90_contract`, `sc90_contract` (that chain order), all drops guarded; together they drop exactly
  LEGACY_PENDING_DROP and leave it empty. `sc90`: assistants.hatched_at, interactions, actions_taken, corrections, stage,
  onboarded. `pm90`: user_settings.autonomy_level, automation_rules.requires_confirmation (once approval_mode replaces it),
  table approval_grants. `tl90`: user_settings.tone, user_skills.learned_notes. SQLite: batch rebuild. Downgrade raises.
- Why a separate, later commit and deploy (after runbook step 3 passes on the expand deploy): old instances and any Instant
  Rollback target select every mapped column, so drops under 1.0.0 code break every Assistant/UserSettings load; the
  transforms must be checked on real data before their source is destroyed; a drop cannot be undone without a restore,
  which the operator cannot do. Desktop has no skew: a build ships whatever is head when tagged, and the backup covers it.

## 10. Failure, rollback, operator runbook (live site; the operator has no database access)
What happens: a migration SQL error or lock timeout rolls the whole transaction back (schema stays at the old revision);
new instances serve 503 `schema_not_ready` on /api, retry transient failures every 30 s, and preflight shows "Database
schema: warn" with the cause. PARTIAL: the same, never retried in-process. AHEAD (rollback after an expand): serves, warns.

Runbook (this replaces the DEPLOY.md "Database migrations" section):
1. Before merging a release with revisions, confirm that Vercel Preview does not use the production
   `DATABASE_URL_OVERRIDE`. If it must, leave `MIGRATE_ON_PREVIEW` unset (previews will then 503 instead of migrating).
2. Deploy as usual. The first cold start migrates (a few seconds); concurrent starts wait for it.
3. Check `https://<site>/health`. `"schema"` must equal the release's head (listed in desktop/RELEASE_NOTES.md for each
   release). Then open `/api/health/preflight`: "Database schema: ok". Sign in, open chat, settings.
4. If /api returns 503 `schema_not_ready` for more than 2 minutes: read the preflight detail and the Vercel log line
   `aura.migrate ERROR`, then Vercel -> Deployments -> previous deployment -> Instant Rollback (the DB is unchanged, so the
   old code works). Send both to the developer; redeploy the same build at most once.
5. If the deploy is healthy but the new code misbehaves: Instant Rollback is safe while only expand revisions have run (the
   DB stays AHEAD, the old code ignores the new columns and tables).
6. Contract deploy: only after step 3 has passed on the expand deploy and the site has run for at least 24 h (DECISION).
   After a contract, rolling back to code older than the contract needs a database restore (Supabase backups, done by the
   owner); do not roll back past it.

Desktop failure: a dialog names the backup and the log, and the app exits. Reinstalling the previous version and restoring
`~/.aura/backups/<file>` over `aura.db` returns the user to the exact pre-upgrade state.

## 11. Tests (named)
tests/test_migrations.py (SQLite always; each also runs on Postgres when AURA_TEST_POSTGRES_URL is set):
`test_expected_head_matches_scripts`; `test_revision_ids_follow_convention` (regex, at most 32 characters, filename equals the id, single head, linear, contracts last); `test_legacy_snapshot_matches_0001`; `test_models_match_migrations`; `test_empty_upgrades_to_head`; `test_legacy_unstamped_is_stamped_and_upgraded`; `test_legacy_with_extra_columns_and_tables_is_accepted`; `test_legacy_missing_column_is_partial_and_untouched`; `test_empty_alembic_version_counts_as_unstamped`; `test_two_version_rows_is_partial`; `test_stamped_0001_upgrades`; `test_at_head_is_one_query`; `test_cache_skips_database`; `test_ahead_is_not_migrated`; `test_ahead_hosted_serves_desktop_refuses`; `test_failed_migration_rolls_back_everything` (inject a failing fake revision through a temp script dir); `test_lock_serialises_two_runners` (Postgres: two threads, two connections; SQLite: two processes); `test_lock_timeout_raises_busy` (hold the lock in a second connection, lock_wait_s=1); `test_pg_runner_works_with_prepare_threshold_none_nullpool`; `test_sqlite_backup_created_named_and_rotated` (4 upgrades -> 3 files kept); `test_sqlite_backup_includes_wal_content`; `test_no_backup_for_at_head_or_empty`; `test_integrity_failure_blocks_upgrade` (corrupt a page of a copied file); `test_low_disk_blocks_upgrade` (monkeypatch disk_usage); `test_sqlite_fk_cascade_not_triggered_by_batch`; `test_preview_does_not_migrate`; `test_migrations_dir_source_and_frozen` (monkeypatch sys.frozen/_MEIPASS); `test_config_independent_of_cwd` (chdir to tmp); `test_app_logging_survives_runner`; `test_0002_legacy_defaults_data` (insert assistant/user_settings/user_skill/automation_rule omitting legacy columns -> defaults present); `test_0002_roundtrip`; `test_unmapped_columns_are_safe`; `test_cli_exit_codes`.

Other modules:
- test_cold_start.py: `test_head_check_is_one_connection_one_query`, `test_cached_check_opens_no_connection`
- test_production_guards.py: `test_schema_guard_returns_503_on_api`, `test_schema_guard_allows_health_and_preflight`,
  `test_guard_retries_transient_after_30s`, `test_guard_does_not_retry_deterministic`,
  `test_preflight_schema_warn_not_fail_in_production`, `test_health_reports_schema`
- test_desktop.py: `test_desktop_migrates_before_server`, `test_desktop_failure_shows_backup_and_exits_3`,
  `test_spec_ships_migrations_and_alembic` (parse aura.spec text: no alembic/mako in excludes, the aura_migrations datas)
- worker: `test_worker_exits_on_schema_failure` (in test_smoke.py)
- CI scripts: tests/migrations/seed_v1.py and verify_v1.py (section 8).

## 12. Work packages (ordered; each lands green)
| # | Title | Files | Done when |
|---|---|---|---|
| M1 | Runner core | app/migrate.py, app/migrate_legacy.py, app/migration_ops.py, alembic/env.py, tests/test_migrations.py | all runner and SQLite tests in section 11 pass; Postgres ones pass against pg.sh |
| M2 | Revision 0002 | alembic/versions/0002_legacy_defaults.py, app/migrate.py (EXPECTED_HEAD), app/migration_ops.py (LEGACY_PENDING_DROP) | 0002 tests; `alembic check` clean |
| M3 | Wire-in, remove auto-create | app/db.py, app/config.py, app/main.py, worker.py, app/middleware.py, app/preflight.py, tests/schema.py, the 7 test modules, tests/test_cold_start.py, tests/test_production_guards.py | full suite green in about 30 s; no `create_all` left under app/ |
| M4 | Desktop and packaging | backend/aura_desktop.py, desktop/aura.spec, tests/test_desktop.py | desktop tests; `python desktop/build.py` + `--smoke-test` pass locally on Linux |
| M5 | CI | .github/workflows/ci.yml, backend/tests/migrations/seed_v1.py, backend/tests/migrations/verify_v1.py | both matrix legs green on a PR |
| M6 | Ops docs | docker-compose.yml, DEPLOY.md, README.md | `docker compose up` migrates once; runbook text in place |
| M7 | Contract (later commit, after the live expand deploy is verified) | alembic/versions/sc90_contract.py (+ pm90/tl90 from their specs), app/migration_ops.py (empty LEGACY_PENDING_DROP), app/migrate.py | the CI legacy paths reach the new head; suite green |

M1-M3 must reach the live site together, and before any package that removes a model attribute or adds a table.
After M3 there is no create_all: area packages (tl, pm, sy) must ship their revisions with their models.

## For the lead
1. Preview safety (1.5, runbook step 1). Decision 13 says "runs automatically at startup". If Vercel Preview shares the
   production DB URL, any preview of aura-2.0 would migrate the live DB. I added the `VERCEL_ENV=preview` guard; please
   confirm, and check the project's Preview env vars.
2. There is a rollout window for decision 14. Once `pm` transforms autonomy_level and grants into modes and rules, a user
   who changes trust on a still-running 1.0.0 instance is not re-copied. The window lasts minutes. Optionally, pm90 could
   re-run the idempotent transform for users that have no mode row before dropping.
3. Decision 13 ("1.0.0 database ... recognised") is satisfied by verifying names only. A pre-1.0 database missing any 0001
   column is refused (PARTIAL), not repaired. That is safe, but such a user's app will not start. I know of no pre-1.0
   desktop releases (only the v1.0.0 tag exists).
