# Deploying AURA

AURA ships two ways:

| | Where it runs | How it's built |
| --- | --- | --- |
| **AURA in the browser** | One Vercel project: the Next.js frontend and the FastAPI backend as two services, Postgres on Supabase | Every push to `main` |
| **AURA for your computer** | The desktop app, downloaded from the home page | A version tag (`v1.0.0`) runs `.github/workflows/desktop-release.yml` |

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
| `ANTHROPIC_API_KEY` | needed for the real assistant; without it AI replies are a keyword demo |
| `OPENAI_API_KEY` | optional: semantic memory and document search |
| `CRON_SECRET` | optional but recommended: turns on background work (below) |
| `SMTP_HOST`, `SMTP_FROM`, `SMTP_USERNAME`, `SMTP_PASSWORD` | optional: "Forgot password?" and emailed schedules |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | optional: Gmail + Calendar, "Continue with Google" |

`DATABASE_URL_OVERRIDE` exists because the Neon store Vercel attached earlier
injects a locked `DATABASE_URL`; the override wins over it without deleting
anything.

Never set `LOCAL_MODE`, `DEVICE_CONTROL` or `REMOTE_ACCESS` here. They're for
someone's own computer; startup refuses device control in production.

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
  runs the worker: automations, schedules, the heartbeat and memory compaction,
  for accounts used in the last 30 days.

Without the secret nobody can make the site do work (and spend model tokens) by
calling that URL; they only get the keep-alive. Schedules fire on the next tick
after their time, so on Hobby a 7:30 schedule may run up to eight hours late.
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

The app creates its tables on start-up (`AUTO_CREATE_SCHEMA`, on by default)
and adds new columns itself; it never drops or changes one. To hand the schema
to Alembic instead:

```
cd backend
DATABASE_URL=<the direct, non-pooler URL> alembic stamp head   # an existing database
# then set AUTO_CREATE_SCHEMA=false on Vercel
```

A new, empty database takes `alembic upgrade head`. After a model change:
`alembic revision --autogenerate -m "what changed"`. CI checks the migrations
still match the models.

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

1. Set the version in `backend/app/version.py` (e.g. `1.0.1`) and update
   `desktop/RELEASE_NOTES.md`.
2. Push a matching tag: `git tag v1.0.1 && git push origin v1.0.1` — or, where
   only branches can be pushed, a `release/v1.0.1` branch, and the workflow
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
