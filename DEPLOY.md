# Deploying AURA

Two halves, deployed separately:

| Half | Host | Directory |
| --- | --- | --- |
| Next.js frontend | Vercel | `frontend/` |
| FastAPI backend + Postgres + cron worker | Render | `backend/` |

They're joined by exactly two settings. Get these wrong and nothing works:

- **`NEXT_PUBLIC_API_URL`** on Vercel → the backend's public URL
- **`CORS_ORIGINS`** on Render → the frontend's public origin

---

## Why the deployed site currently fails

`lib/api.ts` falls back to `http://localhost:8000` when `NEXT_PUBLIC_API_URL`
isn't set. On a deployed page that means the visitor's browser tries to reach
*their own machine* on port 8000. No backend change can fix it — the request
never leaves the laptop it was made from.

`NEXT_PUBLIC_*` variables are inlined into the JavaScript bundle at **build**
time. Adding one in Vercel changes nothing until you redeploy.

---

## 1. Deploy the backend

### Option A — the blueprint (recommended)

`render.yaml` at the repo root describes the whole backend: web service,
Postgres, and the cron worker.

1. Push this repo to GitHub.
2. Render → **New** → **Blueprint** → pick the repo.
3. Render reads `render.yaml` and prompts for the values marked `sync: false`.
   Fill them in as below.
4. Apply. First build takes a few minutes.

### Values Render will ask for

| Variable | What to put |
| --- | --- |
| `TOKEN_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `CORS_ORIGINS` | `https://aura-ai-psi-self.vercel.app` — exact, no trailing slash |
| `FRONTEND_URL` | same as above |
| `BACKEND_URL` | the Render URL, e.g. `https://aura-api.onrender.com` |
| `ANTHROPIC_API_KEY` | optional, but without it there's no real reasoning |
| `OPENAI_API_KEY` | optional; also unlocks semantic memory/document search |

`BACKEND_URL` is a chicken-and-egg: you won't know the URL until the service
exists. Put a placeholder, deploy, then edit it and redeploy. It only matters
for the Google OAuth redirect.

Set `TOKEN_ENCRYPTION_KEY` to **the same value** on both the web service and
the cron worker. Different keys mean the worker can't decrypt what the API
stored.

### Option B — by hand

New → Web Service → Docker → root directory `backend`. Add a Postgres instance,
then set every variable from `backend/.env.example` in the Environment tab.

### Verify

```
curl https://aura-api.onrender.com/health
curl https://aura-api.onrender.com/health/ready
curl https://aura-api.onrender.com/api/health/preflight
```

`/health` should return `{"status":"ok"}`. `/health/ready` proves the database
connection works. `/api/health/preflight` names any setting still missing — it
returns variable *names*, never values, so it's safe to leave reachable.

---

## 2. Point the frontend at it

Vercel → your project → **Settings** → **Environment Variables**:

```
NEXT_PUBLIC_API_URL = https://aura-api.onrender.com
```

Apply it to Production, Preview and Development. Then **Deployments → Redeploy**.
Nothing changes until you rebuild.

### Verify

Open the site. The line under the sign-in card should read
`Model provider: anthropic` (or `mock` if you skipped the key). If it still says
"Connecting to API…", open DevTools → Network and look at the failing request:

- Request URL is `localhost:8000` → the variable didn't reach the build. Confirm
  it's set for the Production environment and that you redeployed.
- Status **CORS error** → `CORS_ORIGINS` doesn't match. It must be the exact
  origin, scheme included, no trailing slash.
- Status **503** or a long hang → the free instance is asleep. First request
  takes 30–60s.

---

## Things that will bite you

**Free instances sleep after 15 minutes idle.** The first request after that
takes 30–60 seconds. The sign-in screen looks broken during the wait. Upgrade to
a paid instance if that matters.

**Free Postgres expires after 30 days.** Render deletes it. Diarise a migration
to the paid tier before then.

**Vercel preview deployments get their own URLs.** Only the origins in
`CORS_ORIGINS` can call the API, so previews fail unless you add them too.

**Demo login is password-free.** Anyone who knows an email address can sign in
as that user. `render.yaml` leaves it on so the deployed demo is usable at all —
set `ALLOW_DEMO_LOGIN=false` and configure Google OAuth before anyone puts real
data in.

**`ENVIRONMENT` is `staging`, not `production`, on purpose.** In production
`main.py` refuses to boot while any preflight check fails, and production also
forbids demo login — so with no model key and no OAuth, a production deploy
would crash-loop. Staging logs the identical warnings and still serves. Move to
production once you have a model key and real authentication.

---

## Google OAuth (optional)

1. Google Cloud Console → APIs & Services → Credentials → OAuth client ID → Web.
2. Authorised redirect URI: `https://aura-api.onrender.com/api/auth/google/callback`
   — must match `BACKEND_URL` exactly.
3. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` on Render, redeploy.
4. Enable the Gmail and Calendar APIs for the project.

The "Continue with Google" button appears on the sign-in screen once the backend
reports the client as configured.

---

## Local development

```bash
# Terminal 1
cd backend
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.txt
copy .env.example .env                              # optional; defaults work
uvicorn app.main:app --reload

# Terminal 2
cd frontend
copy .env.example .env.local
npm install
npm run dev

# Terminal 3 (optional — automations, schedules, heartbeat)
cd backend
python worker.py
```

Locally, CORS trusts any localhost port, so Next.js quietly moving to `:3001`
when `:3000` is taken won't break anything.
