# AURA AI

AURA is a personal employee. It starts work from your first message: you give it
an assignment, it plans, does the work with the systems you have assigned to it,
checks the result and reports back. You decide what it may do on its own and
what needs your approval.

FastAPI backend, Next.js frontend, a tool-calling agent, a background worker.

Run it on your own computer and it can work on that computer too: run commands,
work with files in folders you connect, open apps, check the system and, when a
program offers nothing better, use the screen. See **[DEVICE.md](./DEVICE.md)**.

**Use it:** in the browser at <https://aura-ai-psi-self.vercel.app>, or download
the desktop app for Windows, macOS or Linux from the home page's **Download**
button ([latest release](https://github.com/udaysk-lab/AURA-AI-/releases/latest),
details in [desktop/README.md](./desktop/README.md)).

> **Running it for other people?** Read [DEPLOY.md](./DEPLOY.md): the secrets to
> generate, the spend cap, the database upgrade and a release checklist.
> `ENVIRONMENT=production` makes the app refuse to start while anything
> insecure is outstanding.

---

## What it can do today

- **Work through a task.** AURA recalls similar past work and your preferences,
  writes a plan, acts, checks what it changed and reports. Approving an action
  continues the same task. A Stop button ends it. See
  [docs/TASKS-AND-MEMORY.md](./docs/TASKS-AND-MEMORY.md).
- **Use the systems you assign.** Inbox, Calendar, Tasks, Memory and Planning are
  always on. Research, Documents, Delegation and Focus Guard can be added, and on
  a local install so can This computer. A tool runs only if its system is
  assigned.
- **Read and send mail, manage the calendar and to-dos,** brief you for meetings
  and the day. With Google connected it uses Gmail and Calendar; without, it
  works on a sample workspace.
- **Research and documents.** Web research with a search key; upload, search,
  summarise and compare documents.
- **Work while you are away.** The heartbeat triages mail and prepares your day.
  Schedules run a standing instruction on a cron. Automations fire a fixed list
  of actions on an event or a time. Each schedule and automation has its own
  approval setting.
- **Be reached from other places.** Email, Telegram, Slack and a CLI webhook feed
  the same agent, once you have confirmed which sender is you.
- **Keep secrets it cannot read.** The vault stores credentials the model never
  sees.
- **Remember how you like things done** and what it has done before, with
  controls to view, edit, pause and reset. See
  [docs/TASKS-AND-MEMORY.md](./docs/TASKS-AND-MEMORY.md).
- **Work on your computer** (desktop app and local installs only).

Without a model key, AURA runs an offline keyword demo so every screen works.
It is not an assistant; add a key to get one.

---

## Quick start

### The hosted app

Open <https://aura-ai-psi-self.vercel.app>, sign in and give AURA something to
do in the chat. Choose how much it may do on its own with the mode selector next
to the message box (see [Permissions](#permissions)).

### The desktop app

Download it from the home page. It is the API, the worker and the web app as one
program. It keeps its data in `~/.aura`, opens in your browser already signed in
and asks for a model key in **Settings, AI model**. It can work on your computer.
Details, first-launch notes and building it yourself:
[desktop/README.md](./desktop/README.md).

### From source

The fastest path needs no database, no API keys and no Google account. AURA
boots on SQLite with a sample workspace and the offline demo.

```bash
# Backend
cd backend
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload

# Worker: makes it act while you are away (second terminal)
cd backend && python worker.py

# Frontend (third terminal)
cd frontend && npm install && npm run dev
```

The app is on <http://localhost:3000>, the API on <http://localhost:8000>
(`/docs`, `/health`). Sign in with any email. `⌘K` opens the command palette.

The database schema is created and upgraded by the app when it starts. There is
nothing to run (see [docs/MIGRATIONS.md](./docs/MIGRATIONS.md)).

**As your computer's assistant:**

```bat
start-aura-local.bat      REM Windows
```
```bash
./aura-local.sh           # macOS / Linux
```

Same app, plus access to the machine it runs on. One launch does everything (the
worker runs in the same process), it binds to `127.0.0.1`, and the sign-in screen
offers **Open AURA on this computer** because a local install has one user.
There is a terminal client too:

```bash
./aura.sh "what's eating my disk in ~/projects"
./aura.sh --status
```

Computer access is off in every other configuration, including the hosted site.

**Docker:** `cp .env.example .env && docker compose up --build` starts Postgres
(pgvector), Redis, the API, the worker and the frontend.

---

## Permissions

You choose a **permission mode**. AURA cannot change it.

| Mode | What happens to an action that changes something |
|---|---|
| **Ask first** (default) | A card shows exactly what it would do and waits. |
| **Auto** | A separate safety reviewer decides. Needs a connected model. |
| **Skip approvals** | It runs without a card. |

Reads and AURA's own workspace notes run without asking in every mode. On top of
the mode you can write **rules** that block, ask or allow a system, a tool, or a
tool limited to a path, command, recipient, address or app. Block comes first,
then ask, then allow. One gate decides every action, whether it came from chat,
a schedule or a message, and a few things always need you: permanently deleting
files, connecting a folder or app, and changing permissions or settings.

Cards offer allow once, allow for 10 minutes, always allow, or don't. Every
permission change is written to a tamper-evident log.

The full reference is in **[docs/PERMISSIONS.md](./docs/PERMISSIONS.md)**.

## Systems, tasks and memory

A **system** is a group of tools AURA can use (Inbox, Calendar, Research and so
on). You assign them in **Systems**. A **task** runs as a loop: recall, plan,
act, verify, report. AURA keeps a record of each task and the preferences you
state or correct, filed by task type. You can view, edit, pause, turn off and
reset them, and secrets are removed before anything is stored.

The details are in **[docs/TASKS-AND-MEMORY.md](./docs/TASKS-AND-MEMORY.md)**.

---

## How it fits together

```
Next.js (App Router)  ──►  FastAPI  ──►  Task loop (recall, plan, act, verify, report)
      │                                        │
   ⌘K palette                                  ▼
   task panel                         ┌─ Permission gate ─┐  one decision for every tool call
   mode selector                      │  mode · rules ·   │
                                      │  hard limits      │
                                      └─────────┬─────────┘
                                                ▼
        Systems (plugins) → skills → tools      Two memories: past work, preferences
        Inbox · Calendar · Tasks · Memory ·     (plus facts)
        Planning · Research · Documents ·
        Delegation · Focus · This computer
                                                ▼
                 Channels · Documents · Vault · Google · Web search
                                                ▼
                                       PostgreSQL / SQLite
                                                ▲
              Worker: tasks that need a next step · automations ·
                      schedules · heartbeat · memory compaction
```

**The task loop** (`agent/taskloop.py`) builds the prompt from the persona,
recalled past work, preferences, facts and the names of your vault keys, calls
the model, sends every tool call through the gate, runs the allowed ones, checks
the result, and feeds it back until the task is done, needs you, hits a limit or
is stopped. Its state is saved after every call so an approval, a restart or a
crash resumes the same task. The coordinator knows nothing about any individual
tool.

**Provider adapters** (`llm.py`) put OpenAI, Anthropic and an offline mock behind
one interface. The mock is a keyword router. It has no vision, cannot act as the
Auto reviewer, and is only there so every screen works with no keys.

---

## Switching on the real agent

AURA runs Claude by default. Get a key at **<https://console.anthropic.com>**
(API Keys; billing must be on), then:

```bash
# .env in the project root
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-5-5
```

Restart the backend and check `/setup`, which says whether the provider is live.
On the desktop app or any local install, paste the key in **Settings, AI model**
instead; it is written to the settings file on that computer and takes effect
without a restart.

The key belongs in the environment, not the in-app vault. The vault is for a
user's own third-party secrets. A model key is infrastructure: needed before any
user exists, on every request. In the database it would mean a database leak
also leaks your billing credential.

### Everything else is optional

| Want | Set |
|---|---|
| Better memory and document search | `OPENAI_API_KEY`, used **only** for embeddings |
| A cheaper model for background work | `BACKGROUND_MODEL` |
| A different model for the Auto reviewer | `REVIEWER_MODEL` (else `BACKGROUND_MODEL`, else the main model) |
| Gmail and Calendar | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`, redirect `http://localhost:8000/api/auth/google/callback` |
| Sending email without Gmail | `SMTP_HOST` / `SMTP_FROM` / `SMTP_USERNAME` / `SMTP_PASSWORD` |
| Web research | `SEARCH_PROVIDER=brave\|serper\|tavily` and `SEARCH_API_KEY` |
| Shared rate limits across workers | `REDIS_URL` |
| Postgres | `DATABASE_URL=postgresql+psycopg://aura:aura@localhost:5432/aura` |

Each missing piece degrades honestly: no embeddings means word-overlap search, no
search key means research uses your own data and says so, no send route means
`send_email` reports that nothing was sent.

`/setup` lists what is configured and what to do about what is not. It is
reachable without signing in, because the likeliest reason you cannot sign in is
that something there is wrong. It names settings, never values.

### Configuration files

| File | For |
|---|---|
| [`.env.example`](./.env.example) | Every setting, with the defaults, for running from source or on a computer |
| [`backend/.env.example`](./backend/.env.example) | The backend alone, for development |
| [`.env.production.example`](./.env.production.example) | A server you run for other people |
| [DEPLOY.md](./DEPLOY.md) | The hosted site, secrets, background work, releases |
| [DEVICE.md](./DEVICE.md) | Settings for working on your computer |

### Spend guard

An agent loop, a heartbeat and inbound messages are three ways to spend money
unattended. `DAILY_SPEND_CAP_USD` (default 5) is the backstop. It is checked
before each model call, and background work is limited to 40% of it so
unattended spending cannot use up what you would notice. The Auto reviewer's
calls count too. Settings shows today's spend by source.

The cost comes from a price table in `services/usage.py`, used only for the cap.
It is not billing. Check it against current provider pricing; an unrecognised
model is priced high on purpose. Set a billing alert with the provider as well.

---

## Project layout

```
backend/app/
  main.py · config.py · db.py · models.py · schemas.py · security.py
  migrate.py             The start-up schema runner (Alembic is the only schema path)
  middleware.py          Security headers, rate limits, the local-install Host guard
  web.py                 Serves the exported web app (desktop app only)
  llm.py                 OpenAI / Anthropic / Mock adapters
  plugins.py             Systems: the catalogue and what is assigned
  permissions/
    gate.py              The one decision function
    catalogue.py         Every tool: its system and its kind of action
    rules.py · match.py · shellparse.py · pathglob.py   Rules and how they match
    floors.py            The hard limits
    access.py            Connected folders, app grants, the blocked list, presence
    approvals.py         Approval cards: queueing, answering, expiry
    reviewer.py          The Auto-mode safety reviewer
    triggers.py          What schedules, automations and inbound messages may not do
    events.py · changes.py · legacy.py   The change log and its notices
  agent/
    coordinator.py       Entry points for a turn; streaming
    taskloop.py          The task loop: plan, act, verify, report, resume, stop
    verifiers.py         Read-back checks after changes
    skills.py            The skill catalogue and per-user state
    tools.py             The assistant tools and the registry
    device_tools.py      The computer tools (local installs only)
    prompts.py           System prompt and the reviewer and extractor prompts
  services/
    workmemory.py        Past work: recording and recall
    preferences.py       Preferences: capture, conflicts, retrieval
    tasktypes.py · redaction.py
    identity.py          The assistant's name, voice and look
    heartbeat.py · schedules.py · memory.py · documents.py · research.py
    vault.py · channels.py · google.py · briefing.py · triage.py · usage.py · demo.py
  automation/engine.py   Rule compilation, matching, cron
  device/                Computer access (off unless LOCAL_MODE)
  api/                   auth · chat · work · permissions · workspace · intelligence ·
                         hub · assistant · device · local · remote · cron · account
backend/alembic/         The schema migrations
worker.py                Tasks that need a next step, automations, schedules,
                         heartbeat, compaction (in-process on a local install)
aura_cli.py              Terminal client
aura_desktop.py          The desktop app's launcher

desktop/                 PyInstaller recipe, build script, Windows installer, icons
docs/                    PERMISSIONS.md · TASKS-AND-MEMORY.md · MIGRATIONS.md
.github/workflows/       ci.yml (tests on Linux, Windows, macOS) · desktop-release.yml

frontend/
  app/                   Landing, login, password reset, setup, the app pages
  components/            Sidebar, task panel, mode picker, approval card,
                         command palette, toasts, UI primitives
  lib/api.ts             Typed client; the single source of the API contract
  lib/downloads.ts       Release file names the Download buttons point at
```

---

## API surface

| Area | Endpoints |
|---|---|
| Auth | `POST /api/auth/register`, `/login`, `/demo`, `/local` (local installs), `/password/forgot`, `/password/reset`; `GET /api/auth/config`, `/google/start`, `/me`, `/integrations` |
| Chat | `POST /api/chat`, `/api/chat/stream` (SSE), `/api/chat/continue`, `/api/chat/stop`; conversation CRUD; `PATCH /api/chat/conversations/{id}/memory` |
| Tasks | `GET /api/work-items`, `/work-items/{id}`, `/task-types`; `PATCH`, `DELETE /api/work-items/{id}`; `POST /api/work-items/{id}/feedback` |
| Preferences | `GET/POST /api/preferences`, `PATCH/DELETE /api/preferences/{id}` |
| Memory | `GET/POST /api/memories`, `PATCH/DELETE /api/memories/{id}`, `GET/PATCH /api/memory/settings`, `POST /api/memory/reset` |
| **Permissions** | `GET /api/permissions`, `PUT /api/permissions/mode`, `/boundaries`; `GET/POST /api/permissions/rules`, `PATCH/DELETE /api/permissions/rules/{id}`; `GET /api/permissions/events`, `/events/verify`, `/catalogue` |
| Approvals | `GET /api/pending-actions`, `POST /api/pending-actions/{id}` |
| **Systems** | `GET /api/plugins`, `/plugins/summary`, `POST /api/plugins/{id}/install`, `/uninstall` |
| Skills | `GET /api/skills`, `/skills/stats`, `/skills/activity`, `PATCH /api/skills/{code}`, `POST /api/skills/{code}/teach` |
| **Channels** | `GET /api/channels`, `POST /api/channels/connect`, `/{kind}/rotate`, `/{kind}/disconnect`, `PUT /api/channels/{kind}/owner`, `POST /api/channels/inbound/{kind}` |
| **Vault** | `GET /api/vault`, `PUT /api/vault`, `DELETE /api/vault/{key}` |
| **Documents** | `GET /api/documents`, `/documents/search`, `POST /api/documents/upload`, `/documents/text`, `/{id}/summarize` |
| **Schedules** | `GET/POST /api/schedules`, `PATCH /api/schedules/{id}`, `POST /api/schedules/{id}/run` |
| Assistant | `GET/PATCH /api/assistant` |
| Heartbeat | `GET /api/heartbeat`, `/heartbeat/latest`, `POST /api/heartbeat/run` |
| **This computer** (local only) | `GET /api/device`, `/device/system`, `/device/files`, `/device/processes`, `/device/background`, `/device/folders`, `/device/apps`; `POST /api/device/run`, `/device/blocklist`; `PUT /api/device/screen-control`, `/device/apps/{id}` |
| **Local settings** (local only) | `GET/PUT /api/local/provider`, `POST /api/local/provider/test`, `GET/PUT /api/local/remote` |
| **Remote access** (local only) | `GET /api/remote/status`, `POST /api/remote/pairing`, `POST /api/remote/pair`, `GET /api/remote/devices`, `DELETE /api/remote/devices/{id}` |
| Platform cron | `GET /api/cron/tick` (Vercel crons; runs the worker when `CRON_SECRET` is set) |
| Email · Calendar · To-dos · Automations · Delegations | see `/docs` |

---

## Tests

```bash
cd backend && pytest -q
```

Everything runs on throwaway SQLite with the mock provider: no keys, no
infrastructure. CI (`.github/workflows/ci.yml`) runs it on Linux, Windows and
macOS, once more without the optional device packages, runs the migrations
against SQLite and PostgreSQL, and type-checks and builds both versions of the
frontend.

What the tests cover:

- **Permissions** (`test_perm_*`): the gate's order, every tool's catalogue entry,
  rules and how they match, approval cards, the Auto reviewer, schedules,
  inbound messages, connected folders, screen control, the change log, and the
  upgrade of 1.0 settings.
- **Review fixes** (`test_sec_*`): regression tests for each security fix.
- **Tasks and memory**: the loop, resuming after an approval, past-work recall,
  preferences, secret removal, work-item and memory controls.
- **Migrations** (`test_migrations.py`): every revision on an empty database, a
  1.0.0 database and a stamped one; locking; backups.
- **Computer access** (`test_device.py`): mostly about refusal. It is off by
  default, folders cannot be left with `..`, links or letter case, start-up
  items and git hooks cannot be written, and an action that needs approval does
  not run until it is given.
- **The local install** (`test_desktop.py`): the Host guard, the sign-in key,
  pairing, the settings screens, the launcher.
- **Production guards** (`test_production_guards.py`): the spend cap, background
  budget, rate-limit windows, security headers, `send_email` never claiming a
  send it did not make, export excluding secret values, and account deletion.

The desktop builds have their own end-to-end check, `--smoke-test`, which the
release workflow runs against every build (see
[desktop/README.md](./desktop/README.md)).

---

## Known limitations

- **Never load-tested.** The tests run on three operating systems and the desktop
  builds are started and checked end to end, but nobody has measured behaviour
  under real traffic.
- **One worker process only.** Schedule de-duplication is per process, so a second
  worker double-fires everything.
- **Background work on the hosted site runs three times a day.** Vercel's Hobby
  plan allows daily crons only, so `vercel.json` calls the worker at 01:00, 09:00
  and 17:00 UTC (once `CRON_SECRET` is set). A schedule fires on the next tick
  after its time, and a task left waiting while the page is closed continues on
  the next tick. The desktop app runs its worker continuously.
- **Commands are not sandboxed.** A command runs as your operating-system user and
  can reach anything that user can. What stands in front of it is the gate (mode,
  rules, cards), block rules, the install's denylist, the refusal of commands
  that name AURA's own files, and the change log. That is not an
  operating-system wall. See [DEVICE.md](./DEVICE.md).
- **Auto is a model's judgement.** It can be wrong. Use Ask first where a mistake
  would cost you.
- **Screen control is pixels, not structure.** AURA looks at a screenshot and
  clicks a position. A window that moves between the screenshot and the click
  means a misclick. Prefer the command line where a task has one.
- **Without a model key it cannot see,** and it cannot review actions. The demo
  model has no vision, so screen control needs a real model, and Auto is
  unavailable.
- **Schedules can use files and commands** in connected folders, under the
  schedule's own approval setting. They cannot use the screen, the clipboard or
  apps. Nothing else runs commands unattended.
- **A restart loses the keyboard to background commands.** AURA can still list
  one, read its output and stop it, but not type into it.
- **Inbound channel tokens do not expire.** They are rotatable and rate-limited.
- **Similarity runs in Python.** Embeddings live in JSON columns so one schema
  works on SQLite and Postgres. Fine to the low tens of thousands of chunks.
- **Streaming is step-level, not token-level.** The chat shows each tool as it runs
  and each approval as it is queued, but the reply arrives whole.
- **Secret removal is pattern-based.** Do not paste secrets into chat.
- **The vault protects against the model, not against you.** Anyone with the
  database and `SECRET_KEY` can read it. Use a real key-management service in
  production.
- **The cron matcher is minimal:** `*`, values, lists, `a-b`, `*/n`.
- **`ALLOW_DEMO_LOGIN=true` is a password-free login.** Turn it off in production;
  startup warns if you do not.
- **Remote access is plain HTTP.** A paired phone talks to the computer unencrypted
  on your network. Use a network you trust, or Tailscale. Off by default.
- **The desktop builds are not code-signed.** Windows SmartScreen and macOS
  Gatekeeper warn on first open.
- **No automatic updates.** The desktop app says when a newer release is out and
  links to it; installing it is a download.
