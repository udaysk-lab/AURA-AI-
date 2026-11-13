# Deploying AURA

AURA ships two ways:

| | Where it runs | How it's built |
| --- | --- | --- |
| **AURA in the browser** | One Vercel project: the Next.js frontend and the FastAPI backend as two services, Postgres on Supabase | Every push to `main` |
| **AURA for your computer** | The desktop app, downloaded from the home page | A version tag (for example `v2.0.1`) runs `.github/workflows/desktop-release.yml` |

---

## The hosted site (Vercel)

`vercel.json` declares both services and routes between them, so there is one
domain and no CORS to configure:

```
/api/*, /health*, /docs  →  backend  (FastAPI, backend/app/main.py)
everything else          →  frontend (Next.js, frontend/)
```

Functions run in `hnd1` (Tokyo), next to the Supabase project
(`ap-northeast-1`). Keep them together: from Vercel's default `iad1` every page
took 2–11 s, because each request opened a fresh connection across the Pacific.
If the database moves, change `regions` to match (Mumbai `ap-south-1` → `bom1`,
Singapore `ap-southeast-1` → `sin1`, US East `us-east-1` → `iad1`).

### Environment variables (Vercel → Settings → Environment Variables)

| Variable | Value |
| --- | --- |
| `ENVIRONMENT` | `staging` — see below |
| `DEBUG` | `false` |
| `SECRET_KEY` | a long random string: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `TOKEN_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DATABASE_URL_OVERRIDE` | the Supabase **transaction pooler** URL (port 6543) |
| `FRONTEND_URL`, `BACKEND_URL` | `https://aura-ai-psi-self.vercel.app` |
| `CORS_ORIGINS` | the same origin |
| `ALLOW_DEMO_LOGIN` | `false` |
| `ANTHROPIC_API_KEY` | needed for the real assistant; without it AI replies are a keyword demo, and the Auto permission mode is unavailable |
| `OPENAI_API_KEY` | optional: semantic memory and document search |
| `BACKGROUND_MODEL` | optional: a smaller model for tasks that start without a person in the chat (schedules, inbound messages). Blank = the main model |
| `REVIEWER_MODEL` | optional: the model for Auto mode's safety reviewer. Blank = `BACKGROUND_MODEL`, then the main model |
| `CRON_SECRET` | optional but recommended: turns on background work (below) |
| `SMTP_HOST`, `SMTP_FROM`, `SMTP_USERNAME`, `SMTP_PASSWORD` | optional: "Forgot password?" and emailed schedules |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | optional: Gmail + Calendar, "Continue with Google" |

`DATABASE_URL_OVERRIDE` exists because the Neon store Vercel attached earlier
injects a locked `DATABASE_URL`; the override wins over it without deleting
anything.

Never set `LOCAL_MODE`, `DEVICE_CONTROL`, `REMOTE_ACCESS` or any `DEVICE_*`
setting here. They're for someone's own computer; startup refuses device control
in production. The hosted site has no computer tools at all.

The task loop has four limits with working defaults (`MAX_AGENT_STEPS` 8,
`MAX_TASK_STEPS` 40, `SEGMENT_SECONDS` 200, `RESUME_GRACE_SECONDS` 60). On
Vercel keep `SEGMENT_SECONDS` well under the function's 300-second limit: after
that long in one request a task starts no new model call and carries on in the
next one. See [docs/TASKS-AND-MEMORY.md](./docs/TASKS-AND-MEMORY.md).

**`ENVIRONMENT` is `staging`, not `production`, on purpose.** In production
`main.py` refuses to boot while any preflight check fails — no model key, say —
so a production deploy without one crash-loops. Staging logs the same warnings
and still serves. Switch once the model key and real authentication are in.

### Background work: `CRON_SECRET`

Vercel runs functions only while answering a request, so `worker.py`'s timer has
nowhere to live. `vercel.json` registers three daily crons (01:00, 09:00, 17:00
UTC — the Hobby plan allows daily schedules only) that call `/api/cron/tick`:

- every call touches the database, so a Free Supabase project never idles into a
  pause;
- once `CRON_SECRET` is set, Vercel sends it with each call and the tick also
  runs the worker: it first continues tasks that are ready (after an approval, a
  step budget or an interruption) and expires approvals nobody answered, then
  runs automations, schedules, the heartbeat and memory compaction, for
  accounts used in the last 30 days.

Without the secret nobody can make the site do work (and spend model tokens) by
calling that URL; they only get the keep-alive. Schedules fire on the next tick
after their time, so on Hobby a 7:30 schedule may run up to eight hours late. A
task that is waiting for an approval continues straight away if the page is open
when it is answered; with the page closed it continues on the next tick.
Vercel Pro allows a `*/30 * * * *` schedule instead.

A tick stops starting new work after `CRON_BUDGET_SECONDS` (default 240), to
finish inside the function's 300-second limit; whatever didn't fit is still due
on the next tick. Lower it if the project runs without fluid compute, where the
limit is shorter.

### The Hobby deploy rule

On the Hobby plan Vercel only builds a push whose **last commit is authored by
the team owner** (`udaysk-lab <udaysk2008@gmail.com>`) with no `Co-Authored-By`
trailer naming anyone else. Other pushes are silently ignored — no deployment,
no error. Make the last commit of each push the owner's (an empty
`git commit --allow-empty` works), or move to Pro.

### Verify

```
curl https://aura-ai-psi-self.vercel.app/health
curl https://aura-ai-psi-self.vercel.app/health/ready
curl https://aura-ai-psi-self.vercel.app/api/health/preflight
curl https://aura-ai-psi-self.vercel.app/api/cron/tick
```

`/health/ready` proves the database answers. `/api/health/preflight` names any
setting still missing (names, never values) and now opens a real database
connection too, so it's where to look when sign-in fails.

### If Supabase paused anyway

Supabase dashboard → the project → **Restore project**. The data survives, and a
paused project can be restored for up to a year.

### Database migrations

Alembic is the only thing that changes the schema, and the app runs it by itself
at start-up: there is no setting to turn on and no command to run on the hosted
site. The first cold start after a deploy takes a lock, upgrades the database in
one transaction (a few seconds), and any other instance that starts at the same
time waits for it. If the upgrade fails, nothing is half-applied: the database
stays at the old revision.

How the runner behaves for each state a database can be in, what the backups
are, how to read a failure and how to write a revision is in
**[docs/MIGRATIONS.md](./docs/MIGRATIONS.md)**. The points a deployer needs:

- A new, empty database is built from the migrations.
- A 1.0.0 database (no `alembic_version` table, or stamped `0001_initial`) is
  recognised and brought up to date. You do not run `alembic stamp`.
- On SQLite (the desktop app and local runs) a consistent copy is saved first in
  `backups/` next to the database file, and the three newest are kept. **AURA does
  not back up PostgreSQL.** Take a backup yourself (Supabase project backups or
  `pg_dump`) before any release that adds a revision.
- A database that is neither (columns missing from the 1.0.0 shape, or two
  `alembic_version` rows) is left alone and reported.
- A database made by a newer AURA is not changed. The hosted site serves and
  preflight warns; the desktop app refuses to start.
- `AUTO_CREATE_SCHEMA` is gone. If it is still set it is ignored (one warning in
  the log); remove it from Vercel.
- A Vercel **Preview** deployment never migrates, because Preview often shares
  the production `DATABASE_URL`. It answers `503 schema_not_ready` on `/api`
  instead. Set `MIGRATE_ON_PREVIEW=true` only on a Preview that has its own
  database.
- The migration needs the app's own `DATABASE_URL`; there is no separate
  migration URL. Locks are transaction-scoped, so the Supabase transaction
  pooler (port 6543) is fine.

**Releasing a version that has migrations**

1. Before merging, confirm Preview does not use the production database (above).
   For PostgreSQL, take a backup.
2. Deploy as usual. The first cold start migrates.
3. Open `https://<your-site>/health`. `"schema"` must equal the release's
   revision (`desktop/RELEASE_NOTES.md` lists it for each release). Then open
   `/api/health/preflight`: "Database schema: ok". Sign in, open the chat and
   Settings, Permissions.
4. If `/api` answers 503 `schema_not_ready` for more than two minutes, read the
   preflight detail and the Vercel log line `aura.migrate ERROR`, then
   Vercel → Deployments → the previous deployment → **Instant Rollback**. The
   database is unchanged, so the old code works. Send both to the developer;
   redeploy the same build at most once. A lock timeout is retried by the app
   every 30 seconds; a real error is not retried until the next cold start.
5. If the deploy is healthy but the new code misbehaves, Instant Rollback is
   safe while only additive revisions have run: the database is then newer than
   the old code, which ignores the new columns and tables (the app serves
   normally and preflight shows a warning).
6. A release that drops old columns or tables ships separately, only after step
   3 has passed on the additive release and the site has run for 24 hours.
   After that release, rolling back to older code needs a database restore
   (Supabase backups, done by the owner), so do not roll back past it.

**The 1.0 to 2.0 upgrade.** The first start after the upgrade also maps each
account's 1.0 settings onto the 2.0 permission model, once: the trust level
becomes a mode with rules that keep its behaviour, "always" approvals become
allow rules, and waiting approvals get an expiry. Nobody is put on Auto. After
it, check the following with a test account:

- Settings, Permissions shows a mode and the rules the upgrade made, and the
  change log has one "legacy mapped" entry.
- Channels that were connected have no confirmed owner yet. Until the account
  owner sets it in Channels, messages on that channel are kept as notifications
  and start no task.
- The old trust columns and tables are still in the database, unused, until the
  separate release in step 6.

**The desktop app** upgrades `aura.db` every time it starts, before anything
else. If it cannot (for example the database came from a newer AURA), it opens a
dialog that names the reason, the backup file and the log, then exits. Nothing is
changed. To go back to the version you had, reinstall it and copy the file in
`~/.aura/backups/` over `~/.aura/aura.db`.

**Docker / your own server.** `docker compose up` needs nothing extra: the api
and the worker both check the schema when they start, and the lock makes them
safe together. To upgrade without starting the app:
`python -m app.migrate upgrade` (prints a JSON report; exit 0 ok, 1 failed,
2 the database is newer than this build). `python -m app.migrate status` shows
where a database stands without changing it.

**Writing a migration** (developers): after a model change,
`alembic revision --autogenerate -m "what changed"` from `backend/`, then follow
the rules in [docs/MIGRATIONS.md](./docs/MIGRATIONS.md#writing-a-new-revision).
CI builds a 1.0.0 database both ways on SQLite and PostgreSQL, upgrades it, and
runs `alembic check` to confirm the migrations still match the models.

---

## Google OAuth (optional)

1. Google Cloud Console → APIs & Services → Credentials → OAuth client ID → Web.
2. Authorised redirect URIs: `https://aura-ai-psi-self.vercel.app/api/auth/google/callback`
   (and `http://localhost:8000/api/auth/google/callback` for development). No
   JavaScript origins are needed — AURA uses the server-side flow.
3. Enable the Gmail and Calendar APIs, set `GOOGLE_CLIENT_ID` and
   `GOOGLE_CLIENT_SECRET` on Vercel, redeploy.

---

## The desktop app

`desktop/README.md` covers building it yourself. To publish a release:

1. Set the version in `backend/app/version.py` (e.g. `2.0.1`) and update
   `desktop/RELEASE_NOTES.md`, including the database revision the release ends
   at (the head in `backend/app/migrate.py`, `EXPECTED_HEAD`).
2. Push a matching tag: `git tag v2.0.1 && git push origin v2.0.1` — or, where
   only branches can be pushed, a `release/v2.0.1` branch, and the workflow
   makes the tag on the commit it built.
3. The **Desktop release** workflow builds Windows (installer and portable zip),
   macOS (Apple silicon and Intel) and Linux, starts every build and checks it
   answers (`--smoke-test`), installs the Windows installer and checks the
   installed app too, then publishes the release with `SHA256SUMS.txt`.

The home page links to `releases/latest/download/<file>`, so a new release
needs no website change. The file names are a contract between the workflow
and `frontend/lib/downloads.ts`.

The builds aren't code-signed: Windows SmartScreen and macOS Gatekeeper warn on
first open, and the download page says how to get past that. Signing needs a
Windows code-signing certificate and an Apple Developer ID; both slot into the
workflow when there's one to use.

---

## Self-hosting with Docker

`docker compose up --build` brings up Postgres, Redis, the API, the worker and
the frontend on one machine — the shape for a server you run yourself. Set the
same variables as above in `.env`.

---

## Local development

```bash
# Terminal 1 — API
cd backend
python -m venv .venv && .venv\Scripts\activate     # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload

# Terminal 2 — web app
cd frontend
npm install
npm run dev

# Terminal 3 (optional) — automations, schedules, heartbeat
cd backend
python worker.py
```

Or `start-aura-local.bat` / `./aura-local.sh` for the local, device-enabled
setup in one go. Locally, CORS trusts any localhost port.
