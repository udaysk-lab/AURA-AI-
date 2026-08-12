# Deploying AURA

Written for the case where a real person other than you will use this. If that's
not true yet, `README.md` is enough.

---

## 0. Get a Claude API key

AURA runs Claude by default. You need your own key — nobody can create one for you.

1. Sign in at **<https://console.anthropic.com>**
2. **Settings → Billing** and add a payment method (a key without credit returns
   `credit_balance_too_low` on every call)
3. **API Keys → Create Key**, name it something you'll recognise later
   (`aura-prod`), copy it immediately — it's shown once
4. Put it in your environment, never in a file you commit:

```bash
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-5
```

**Why the environment and not the vault?** The vault (`services/vault.py`) is for a
*user's* third-party secrets — it's encrypted per-row and decrypted on demand
precisely because the model must never read it. Your Claude key is *infrastructure*:
it's needed before any user exists, on every request, at process start. Putting it in
the database would mean a database leak also leaks your billing credential, and would
add a decrypt to every call for no security gain. Environment variables, injected by
your platform's secret manager, are the right home.

**Rotating it:** create the new key first, deploy, confirm `/api/health/preflight`
reports the model as ok, *then* revoke the old one. Revoking first means downtime.

Optionally set `OPENAI_API_KEY` too — it's used **only for embeddings**, which make
memory and document search match meaning instead of keywords. Claude still does all
the reasoning. Without it everything works, just less well, and preflight says so.

---

## 1. Generate secrets

```bash
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print('TOKEN_ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
```

`SECRET_KEY` signs login tokens — with the default value anyone who has read this
repository can forge a session. `TOKEN_ENCRYPTION_KEY` encrypts stored OAuth tokens
and vault secrets; set it explicitly, because if it's derived from `SECRET_KEY` then
rotating `SECRET_KEY` silently makes every stored credential unreadable.

Neither is recoverable. Store both in your platform's secret manager.

---

## 2. Environment

Copy `.env.production.example` and fill it in. The settings that actually change
behaviour in production:

| Variable | Why it matters |
|---|---|
| `ENVIRONMENT=production` | Makes preflight failures fatal, disables `/docs`, enables HSTS |
| `DEBUG=false` | Stops internal error text reaching clients |
| `ALLOW_DEMO_LOGIN=false` | Otherwise anyone who knows an email can sign in as them |
| `DATABASE_URL` | Postgres. SQLite will corrupt under concurrent writes |
| `AUTO_CREATE_SCHEMA=false` | Hands schema control to Alembic |
| `CORS_ORIGINS` | Your exact frontend origin. Never `*` with credentials on |
| `REDIS_URL` | Shared rate-limit counters; without it each worker counts separately |
| `DAILY_SPEND_CAP_USD` | The backstop against a runaway loop |

With `ENVIRONMENT=production`, **the app refuses to start** while any blocking
preflight check fails. That's deliberate: a container that won't boot gets noticed,
an insecure one that boots fine does not.

---

## 3. Database

```bash
cd backend
export DATABASE_URL=postgresql+psycopg://user:pass@host:5432/aura
alembic revision --autogenerate -m "initial schema"   # first time only
alembic upgrade head
```

Then set `AUTO_CREATE_SCHEMA=false` so `create_all` and Alembic aren't both trying to
own the schema.

Every deploy after that:

```bash
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**pgvector (optional).** Memory and document embeddings live in JSON columns and are
compared in Python — fine to roughly ten thousand chunks per user. Past that:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

then migrate the `embedding` columns to `vector(1536)` and replace the ranking loops
in `services/memory.py` and `services/documents.py` with
`ORDER BY embedding <=> :query_vec`. Nothing else changes.

---

## 4. Processes

Two, not one:

```bash
# API
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

# Worker — automations, schedules, heartbeat, nightly memory compaction
python worker.py
```

Without the worker, AURA is reactive only: nothing fires on a schedule, the
heartbeat never runs, and memory never gets compacted. Run **exactly one** worker.
Two will double-fire schedules, because the 90-second guard against duplicate
execution is per-process.

Health endpoints for your platform:

- `GET /health` — liveness. Cheap, no database call.
- `GET /health/ready` — readiness. Touches the database; returns 503 when degraded so
  a load balancer drains the instance instead of sending it traffic.
- `GET /api/health/preflight` — configuration diagnostics, also rendered at `/setup`.

---

## 5. Frontend

```bash
cd frontend
NEXT_PUBLIC_API_URL=https://api.yourdomain.com npm run build
npm start
```

`NEXT_PUBLIC_*` values are **baked in at build time**, not read at runtime. Changing
the API URL means rebuilding, not restarting.

---

## 6. Google Workspace (optional)

Google Cloud Console → **APIs & Services**:

1. Enable the **Gmail API** and **Google Calendar API**
2. Create an **OAuth 2.0 Client ID** (Web application)
3. Authorised redirect URI: `https://api.yourdomain.com/api/auth/google/callback`
4. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`

The scopes requested are `gmail.modify` and `calendar` — enough to read, label,
archive and send, and to create and delete events. Google requires verification
before more than 100 external users can grant them; until then, add testers on the
OAuth consent screen.

Refresh tokens are Fernet-encrypted before they touch the database and are never
returned by any endpoint.

**Two flows share one endpoint.** `GET /api/auth/google/start` behaves differently
depending on whether the request carries a session:

| Arriving with | Mode | Result |
|---|---|---|
| no token | `signin` | The Google account identifies you — find or create a user by that email |
| a bearer token | `connect` | Google is attached to the account you're **already** signed in as |

The mode and the user id are bound to the CSRF state at start, not inferred at
callback. That matters: without it, the callback's only clue about who the tokens
belong to is the Google email, so connecting a personal Gmail while signed in as
someone else silently switches you to a different account. Connecting a Google
address already linked to another AURA user is refused rather than moved.

**Partial consent is normal and must be handled.** Google lets users untick
individual scopes, so "connected" is not the same as "can do the thing".
`/api/auth/integrations` returns a per-capability breakdown; the Settings card
shows which permissions actually came back and offers Reconnect when any are
missing.

**Refresh tokens arrive once.** Google returns one on first consent only, which is
why `authorization_url` sets `prompt=consent` and why the callback never overwrites
a stored refresh token with an empty one. A connection holding only an access token
stops working after an hour — `needs_reconnect` flags that case explicitly.

Disconnecting calls Google's revoke endpoint as well as deleting the local row, so
the grant doesn't linger in the user's Google security settings.

---

## 7. Pre-launch checklist

Security:

- [ ] `SECRET_KEY` and `TOKEN_ENCRYPTION_KEY` are random, unique, in a secret manager
- [ ] `ALLOW_DEMO_LOGIN=false`
- [ ] `DEBUG=false`, `ENVIRONMENT=production`
- [ ] `CORS_ORIGINS` lists exact HTTPS origins, no wildcard
- [ ] TLS terminated in front of the API; HTTP redirects to HTTPS
- [ ] `/api/health/preflight` returns `ready: true`

Cost and abuse — this is the part people skip:

- [ ] `DAILY_SPEND_CAP_USD` set to a number you'd be relaxed about losing
- [ ] `RATE_LIMIT_ENABLED=true` with `REDIS_URL` set
- [ ] A billing alert configured in the Anthropic console, independent of AURA
- [ ] Inbound channels connected only for channels you actually use — each live
      token is a way to spend your money
- [ ] `HEARTBEAT_DEFAULT_INTERVAL_MINUTES` sane (30+; every 5 minutes across many
      users adds up fast)

Operations:

- [ ] Automated Postgres backups, and a restore you have actually tested
- [ ] Exactly one worker process
- [ ] Logs collected somewhere searchable — every response carries `X-Request-ID`
- [ ] Uptime check on `/health/ready`

---

## 8. What is deliberately not production-grade

Being explicit so you don't discover these the hard way:

**Never load-tested.** The code has been reviewed but not benchmarked. Assume the
first bottleneck is Python-side similarity search over embeddings.

**Similarity search is O(n) in Python.** See the pgvector note above.

**One worker only.** Schedule de-duplication is per-process. Multiple workers need a
distributed lock — Redis `SETNX` around `run_due` is the small version of that fix.

**Inbound channel tokens don't expire.** They're rotatable but have no TTL. Rotate
them on a schedule you decide.

**No per-user cost attribution to a payment method.** The spend cap protects *you*,
the operator. If you intend to charge users, you need metered billing on top of
`UsageRecord`.

**Costs in the spend guard are estimates.** `PRICES` in `services/usage.py` is a
hardcoded table used to enforce the cap, not to bill. Verify it against current
provider pricing; an unrecognised model is priced pessimistically on purpose.

**The vault protects against the model, not against you.** Anyone holding both the
database and `TOKEN_ENCRYPTION_KEY` can decrypt everything in it. For a multi-tenant
deployment, use a real KMS with per-tenant keys.

**No email deliverability setup.** If you send via SMTP from your own domain,
configure SPF, DKIM and DMARC or your assistant's mail will land in spam.

---

## 9. First-run smoke test

After deploying, in order:

1. `curl https://api.yourdomain.com/health` → `{"status":"ok"}`
2. `curl https://api.yourdomain.com/api/health/preflight` → `ready: true`
3. Open `/setup` in a browser — every check green or an understood warning
4. Sign in, complete onboarding, name the assistant
5. Ask it *"what does my day look like"* — confirm skill lines appear in the reply
6. Ask it to *"email someone"* — confirm it **queues for approval** rather than sending
7. Settings → confirm today's spend is non-zero (proves usage accounting works)
8. Wait one heartbeat interval → confirm a background report appears on the dashboard

Step 6 is the one that matters. If an email sends without asking, stop and check
`DESTRUCTIVE_TOOLS` and the account's autonomy tier before letting anyone else near it.
