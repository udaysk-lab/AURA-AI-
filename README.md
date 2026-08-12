# AURA AI

A personal executive assistant with an identity, a memory, and a set of skills it
gets better at. FastAPI backend, Next.js frontend, a tool-calling agent that plans
before it acts, works in the background, reaches you wherever you are, and earns
permission rather than assuming it.

---

> **Deploying for real people?** Read [DEPLOY.md](./DEPLOY.md). It covers getting a
> Claude API key, the secrets you must generate, the spend cap, and a pre-launch
> checklist. `ENVIRONMENT=production` makes the app refuse to start while anything
> insecure is outstanding.

## Run it

The fastest path needs **no database, no API keys, no Google account**. AURA boots
on SQLite with a seeded sample workspace and an offline agent, so every screen
works immediately.

```bash
# Backend
cd backend
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload

# Worker — this is what makes it act while you're away (second terminal)
cd backend && python worker.py

# Frontend (third terminal)
cd frontend && npm install && npm run dev
```

→ App on <http://localhost:3000>, API on <http://localhost:8000> (`/docs`, `/health`).

Sign in with any email, name your assistant, and you land in a populated workspace.
`⌘K` opens the command palette from anywhere.

**Docker:** `cp .env.example .env && docker compose up --build` brings up Postgres
(pgvector), Redis, the API, the worker and the frontend.

---

## The six ideas this is built on

### 1. Skills, not tools

A *tool* is a function the model can call. A **skill** is the unit you think in: a
named capability with a code, a track record, and notes it has learned about how
*you* like it done.

```
[SKILL·EM01] Processed 7 unread · 3 flagged · 4 awaiting reply
[SKILL·MP01] Briefed: Northwind partner call · agenda and context ready
[SKILL·RS02] Briefed Northwind Ventures · 4 emails · 5 sources
```

Twenty-one skills across Email, Calendar, Meetings, Tasks, Memory, People,
Research, Documents, Planning and System. Each can be switched off — and switching
it off strips its tools from the model's toolset entirely. **It cannot call what it
cannot see**, which is a real guarantee rather than a prompt instruction. A test
asserts no tool exists outside a skill, because an orphan tool would be permanently
un-disableable.

Teaching a skill stores a correction against it, replayed into the prompt whenever
that skill is in scope. That's the mechanism behind "learns how you like things
done" — a specific string in a specific prompt, not vibes.

### 2. Plugins bundle skills

The **hub** is a catalogue of installable plugin packs. Five core plugins ship
enabled and can't be removed; optional ones (Research, Documents, Delegation, Focus
Guard) unlock additional skills. Uninstalling *disables* rather than deletes, so a
skill's learned notes and run history survive and reinstalling picks up where you
left off.

Plugins needing a connector that doesn't exist yet — GitHub, Slack, travel,
expenses — are listed greyed out with the reason. Showing them is more honest than
hiding them, and far more honest than shipping a stub that pretends to work.

### 3. It works while you don't

The **heartbeat** runs on a timer: syncs, triages new mail, pulls commitments out
of flagged email into tasks, preps your next meeting, flags what's slipping. It
leaves a short report at the top of your dashboard.

**Schedules** are the other half — standing instructions that run a *prompt*
through the full agent on a cron ("every weekday at 7:30, give me my briefing").
Deliberately separate from automations, which fire a fixed list of tool calls:
you want a hundred cheap automations and three expensive schedules, not the
reverse by accident.

Two hard rules, enforced in code rather than prompt: background work only runs
skills you've enabled whose autonomy floor your tier clears, and it never fires an
irreversible action.

Memory compaction runs daily — merge near-duplicates, promote what gets used, let
the unused fade. Without it memory only grows, retrieval degrades as duplicates
crowd each other out, and every prompt costs more for less signal.

### 4. Trust is graduated, and it accrues

| Tier | Runs without asking |
|---|---|
| **Strict** | Reads only |
| **Conservative** | + its own data (tasks, memory, notifications) |
| **Relaxed** | + externally visible but recoverable (booking a meeting) |
| **Full** | Everything, including sending email |

Every confirmation offers **Allow once / Allow 10 minutes / Always allow / Don't**.
"Always" writes a standing grant that overrides the tier for that one tool — which
is what turns the confirmation dialog from a toll booth into the way trust builds.
Grants are listed and revocable in Settings.

### 5. Reach it anywhere

**Channels** — web, email, Telegram, Slack, CLI — all funnel into the same agent
with the same skills and the same permission gate. A message from Telegram is not
more trusted than one typed into the app, and there is exactly one code path
deciding what the assistant may do.

A webhook has no login session, so each channel gets its own token: generated
server-side, shown to you exactly once, compared in constant time, scoped to one
channel of one account, rotatable. If an inbound message asks the assistant to send
an email, it still queues for your approval.

### 6. It uses secrets it cannot read

The **vault** stores credentials with one design constraint: *the model never sees
a value.* Not in a prompt, not in a tool argument, not in a result, not in a log.

- Values are Fernet-encrypted at rest and never returned by the API — only a key, a
  label and a masked hint (`sk_…456`).
- Tools reference a secret as `{{vault:stripe_key}}`. The model can write that
  because it's just a name; it can't write the value because it was never given it.
- `resolve()` runs deterministically in Python immediately before an outbound call,
  and its output goes to the network — never back into the conversation.

Substitution happens after the model has finished deciding and before the request
leaves the process. That ordering is the whole trick.

---

## Architecture

```
Next.js (App Router)  ──►  FastAPI  ──►  Coordinator agent
      │                                       │
   ⌘K palette              ┌──────────────────┼──────────────────┐
   toasts, mobile          ▼                  ▼                  ▼
                    Plugin registry      Skill registry       Memory
                    (12 plugins)         (21 skills /         (semantic,
                          │               31 tools)            compacted)
                          ▼                  │
                    Autonomy gate ──► PendingAction / ApprovalGrant
                          │
              ┌───────────┼───────────┬──────────────┐
              ▼           ▼           ▼              ▼
          Channels    Documents     Vault      Integrations
        (web · email  (chunked,   (encrypted,  (Gmail, Calendar,
         telegram      embedded)   never read)  web search)
         slack · cli)
                          │
                  PostgreSQL / SQLite
                          ▲
        Worker: automations · schedules · heartbeat · compaction
```

**The agent loop** (`agent/coordinator.py`): detect corrections → retrieve memory →
build the prompt from persona + memory + learned notes + vault key names → call the
model → gate each tool call through the autonomy tier → dispatch → log a skill run →
feed results back → repeat until it answers or the step budget runs out. The
coordinator knows nothing about any individual tool.

**Provider adapters** (`llm.py`) normalise OpenAI, Anthropic and an offline mock
behind one interface. The mock is a keyword router — not intelligence, but enough
that every screen, tool path and confirmation flow works with no keys.

---

## Switching on the real agent

AURA runs Claude by default. Get a key at **<https://console.anthropic.com>** →
API Keys (billing must be enabled on the account), then:

```bash
# .env in the project root
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-5
```

Restart the backend and check `/setup` — it will tell you the provider is live.

**Why the env file and not the in-app vault?** The vault is for a *user's* third-party
secrets: encrypted per row, decrypted on demand, never readable by the model. Your
Claude key is *infrastructure* — needed before any user exists, on every request, at
process start. In the database it would mean a database leak also leaks your billing
credential, with a decrypt on every call for no security gain.

### Everything else is optional

| Want | Set |
|---|---|
| Better memory + document search | `OPENAI_API_KEY` — used **only** for embeddings; Claude still reasons |
| Gmail + Calendar | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`, redirect `http://localhost:8000/api/auth/google/callback` |
| Sending email without Gmail | `SMTP_HOST` / `SMTP_FROM` / `SMTP_USERNAME` / `SMTP_PASSWORD` |
| Web research | `SEARCH_PROVIDER=brave\|serper\|tavily` + `SEARCH_API_KEY` |
| Shared rate limits across workers | `REDIS_URL` |
| Postgres | `DATABASE_URL=postgresql+psycopg://aura:aura@localhost:5432/aura` |

Each missing piece degrades honestly rather than breaking: no embeddings means
lexical search, no search key means research uses your own data and says so, no send
route means `send_email` reports that nothing was sent instead of pretending.

`/setup` lists exactly what's configured and the command to fix what isn't. It's
reachable without signing in, because the most likely reason you *can't* sign in is
that something in there is wrong. It names settings, never values.

### Spend guard

An agent loop plus a heartbeat plus inbound webhooks is three ways to spend money
unattended. `DAILY_SPEND_CAP_USD` (default $5) is the backstop: checked **before**
each model call, not after, and background work is limited to 40% of it so
unattended spending can never starve the interactive budget you'd actually notice.
Settings shows today's spend broken down by source.

Costs come from a price table in `services/usage.py` used *only* for the cap — it's
not billing. Verify it against current provider pricing; an unrecognised model is
priced pessimistically on purpose. Set a billing alert in the Anthropic console too.

---

## Project layout

```
backend/app/
  main.py · config.py · db.py · models.py (27 tables) · schemas.py · security.py
  llm.py                 OpenAI / Anthropic / Mock adapters
  plugins.py             Plugin catalogue and install state
  agent/
    coordinator.py       The loop, corrections, approvals, streaming
    skills.py            21-skill catalogue and per-user state
    autonomy.py          Risk classes, four tiers, standing grants
    tools.py             31 tools + registry
    prompts.py           System prompt, rule compiler, correction detector
  services/
    identity.py          Assistant persona and the growth arc
    heartbeat.py         Proactive background work
    schedules.py         Cron-driven agent prompts
    memory.py            Semantic store, retrieval, compaction
    documents.py         Extract, chunk, embed, search, compare
    research.py          Pluggable web search + sourced synthesis
    vault.py             Encrypted secrets the model can't read
    channels.py          Inbound routing for every surface
    google.py            Gmail + Calendar sync
    briefing.py · triage.py · demo.py
  automation/engine.py   Rule compilation, matching, cron
  api/                   auth · chat · workspace · intelligence · hub · assistant
worker.py                automations · schedules · heartbeat · compaction

frontend/
  app/                   Landing, login, onboarding, 13 app pages
  components/
    Mascot.tsx           The assistant's face — shifts with its stage
    CommandPalette.tsx   ⌘K navigation and actions
    Toast.tsx            Toasts, useAction, useHotkey
    Sidebar.tsx          Identity, growth, grouped nav, mobile drawer
    ui.tsx               Primitives, skill-log lines, markdown renderer
  lib/api.ts             Typed client — single source of the API contract
```

---

## API surface

| Area | Endpoints |
|---|---|
| Auth | `POST /api/auth/demo`, `GET /api/auth/google/start`, `/auth/me`, `/auth/integrations` |
| Chat | `POST /api/chat`, `POST /api/chat/stream` (SSE), conversation CRUD |
| Skills | `GET /api/skills`, `/skills/stats`, `/skills/activity`, `PATCH /api/skills/{code}`, `POST /api/skills/{code}/teach` |
| **Plugins** | `GET /api/plugins`, `/plugins/summary`, `POST /api/plugins/{id}/install`, `/uninstall` |
| **Channels** | `GET /api/channels`, `POST /api/channels/connect`, `/{kind}/rotate`, `POST /api/channels/inbound/{kind}` |
| **Vault** | `GET /api/vault`, `PUT /api/vault`, `DELETE /api/vault/{key}` |
| **Documents** | `GET /api/documents`, `/documents/search`, `POST /api/documents/upload`, `/documents/text`, `/{id}/summarize` |
| **Schedules** | `GET/POST /api/schedules`, `PATCH /api/schedules/{id}`, `POST /api/schedules/{id}/run` |
| Identity | `GET/PATCH /api/assistant`, `POST /api/assistant/hatch` |
| Heartbeat | `GET /api/heartbeat`, `/heartbeat/latest`, `POST /api/heartbeat/run` |
| Trust | `GET /api/pending-actions`, `POST /api/pending-actions/{id}` (`reject`\|`once`\|`always`\|`window`), `GET/DELETE /api/grants` |
| Email · Calendar · Tasks · Memory · Automations · Delegations | see `/docs` |

---


## Tests

```bash
cd backend && pytest -q
```

Two suites, both on throwaway SQLite with the mock provider — no keys, no infra.

`test_smoke.py` covers behaviour: skills, plugins, autonomy tiers, channels,
documents, schedules, identity, memory. `test_production_guards.py` covers the
things that stay invisible until they cost money or leak something — spend cap
enforcement, background budget isolation, rate-limit windows, security headers,
`send_email` never claiming a send it didn't make, export excluding secret values,
and account deletion cascading properly.

---

## Known limitations

Honest list, because these are what will bite first:

- **Not executed in this environment.** Written and reviewed without a runnable
  sandbox — never started or test-run end to end, and never load-tested. Run
  `pytest -q` first; it exercises most of the backend. Expect to fix a small number
  of issues on first boot.
- **Schema: two modes.** In dev, `init_db()` runs `create_all` then patches in
  missing columns (`db.py: apply_additive_migrations`) so an existing `aura.db`
  survives a model change — additive only, never drops or retypes. For production
  set `AUTO_CREATE_SCHEMA=false` and use the Alembic setup in `backend/alembic/`.
- **One worker process only.** Schedule de-duplication is per-process, so a second
  worker double-fires everything. A Redis lock around `run_due` is the fix.
- **Inbound channel tokens don't expire.** Rotatable, but no TTL.
- **Similarity runs in Python.** Memory and document embeddings live in JSON
  columns so one schema works on SQLite and Postgres. Fine to the low tens of
  thousands of chunks. Past that, migrate to `vector(1536)` and swap the ranking
  loop for `ORDER BY embedding <=> :q`.
- **Streaming is skill-level, not token-level.** The adapters make non-streaming
  calls so tool-calling behaves identically across providers.
- **Correction detection is regex-triggered.** A correction phrased without a
  marker phrase is missed; the model is only consulted once a marker fires, which
  keeps cost near zero but means recall is imperfect. Teaching a skill always works.
- **The vault protects against the model, not against you.** Anyone with your JWT
  can't read values, but anyone with the database *and* `SECRET_KEY` can. Use a
  real KMS in production.
- **Inbound channels have no rate limit.** Add one before exposing them publicly —
  a leaked token currently costs you tokens as fast as someone can POST.
- **The cron matcher is minimal** — `*`, values, lists, `a-b`, `*/n`. Swap in
  croniter for anything more.
- **OAuth state is in-process memory.** Move `_oauth_state` to Redis before running
  multiple workers.
- **`ALLOW_DEMO_LOGIN=true` is a password-free login.** Turn it off in production;
  startup warns if you don't.

---

## Where this goes next

The plugin registry is the extension point. Slack, GitHub, Notion, travel and
expenses each become a service module plus a plugin entry — no coordinator change.
Voice and SMS are channel entries. Document RAG already reuses the embedding path.
Multi-agent is the first change that would genuinely restructure the loop.
