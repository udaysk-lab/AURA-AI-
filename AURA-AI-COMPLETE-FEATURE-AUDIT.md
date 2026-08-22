# Aura AI Complete Feature Audit

**Audited:** 22 August 2026
**Live site:** https://aura-ai-psi-self.vercel.app
**Source:** `D:\UDAY\vibcode\AURA AI` — `main` @ `bbaaacb`+ (one uncommitted change, see §14)
**Nothing was modified during this audit.** No code changed, no deploy, no commit, no database or Supabase change.

---

## 1. Executive Summary

AURA is a substantially complete, unusually well-engineered application. The
backend exposes **~110 endpoints** across 6 routers, the frontend has **19
pages**, and there are **21 skills**, **12 plugins** and **31 tools** in the
registries. Code quality is high: I found **zero `TODO`/`FIXME` markers** in the
entire codebase, which is rare.

The critical finding is not a bug. It is that **the application is deployed in a
configuration where its central premise does not function.**

`/api/health/preflight` reports `llm_provider: "mock"`. Every AI feature — chat,
triage, drafting, briefings, research, document summarisation, memory
extraction — is currently served by an offline keyword router in `llm.py`, not a
language model. Screens populate and buttons respond, so the app *looks* healthy,
but nothing is actually reasoning. For a product whose landing page promises "an
assistant you raise", this is the difference between a demo and the product.

Second: **`worker.py` has nowhere to run on Vercel.** Heartbeat, schedules,
automations and memory compaction are implemented, wired to UI, and dead in
production. That is roughly a third of the advertised behaviour.

Third, and much smaller: the marketing landing page **displays four skill codes
that do not exist** and omits eleven that do.

**A serious honesty caveat on this audit:** I could not log in. I do not enter
passwords into forms, so every authenticated screen is marked **UNCONFIRMED**
rather than guessed at. That is the majority of the application. See §11.

---

## 2. Application Overview

**Purpose.** A personal executive assistant with a persistent identity, a memory
that compacts over time, and a set of named skills it demonstrably improves at.
Not a chat wrapper — the design intent is an assistant you delegate to.

**Target user.** Knowledge workers with heavy inbox and calendar load. Onboarding
copy ("Founder at a seed-stage fintech") points at founders and executives.

**Architecture** (from README and confirmed in source):

```
Next.js App Router ──► FastAPI ──► Coordinator agent
                                    ├── Plugin registry (12)
                                    ├── Skill registry (21 skills / 31 tools)
                                    ├── Memory (semantic, compacted)
                                    └── Autonomy gate ──► PendingAction / Grant
                                         └── Channels · Documents · Vault · Google
                                              └── Postgres (Supabase, Tokyo)
Worker: automations · schedules · heartbeat · compaction   ← NOT RUNNING
```

**Six design ideas** the codebase actually implements, not just claims:

1. **Skills, not tools** — 21 skills owning 31 tools. Disabling a skill removes
   its tools from the model's toolset entirely (`agent/skills.py`).
2. **Plugins bundle skills** — 12 plugins, 5 core and non-removable.
3. **It works while you don't** — heartbeat + cron schedules (`worker.py`).
4. **Graduated trust** — 4 autonomy tiers plus standing grants
   (`agent/autonomy.py`).
5. **Reach it anywhere** — 5 channels through one permission gate.
6. **Secrets it cannot read** — Fernet vault, `{{vault:key}}` substitution
   resolved in Python after the model has finished (`services/vault.py`).

**Data flow.** SQLAlchemy → Supabase Postgres via transaction pooler (port 6543,
Tokyo `ap-northeast-1`). 27 tables. Schema created by `create_all` at startup;
Alembic scaffolding exists but has **no revisions**.

---

## 3. Complete Feature Inventory

### Frontend routes (19 pages)

| Route | Purpose | Status |
| --- | --- | --- |
| `/` | Landing page | **WORKING** |
| `/login` | Sign in + sign up | **WORKING** (one bug, §6) |
| `/onboarding` | 4-step assistant setup | **PARTIALLY WORKING** |
| `/setup` | Config diagnostics, no auth required | **UNCONFIRMED** |
| `/auth/callback` | Google OAuth return | **UNAVAILABLE** (§7) |
| `/dashboard` | Briefing + activity | **UNCONFIRMED** |
| `/chat` | Agent conversation | **UNCONFIRMED** — mock provider |
| `/emails` | Inbox + triage | **UNCONFIRMED** — sample data |
| `/calendar` | Events, conflicts, free slots | **UNCONFIRMED** — sample data |
| `/tasks` | Tasks + quick add | **UNCONFIRMED** |
| `/memory` | Semantic memory | **UNCONFIRMED** — degraded, §5 |
| `/documents` | Upload, search, summarise | **UNCONFIRMED** — degraded |
| `/hub` | Plugin catalogue | **UNCONFIRMED** |
| `/skills` | Skill registry + teaching | **UNCONFIRMED** |
| `/automations` | Rule automation | **BROKEN in prod** (§6) |
| `/schedules` | Cron-driven prompts | **BROKEN in prod** (§6) |
| `/channels` | Web/email/Telegram/Slack/CLI | **UNCONFIRMED** |
| `/notifications` | Notification list | **UNCONFIRMED** |
| `/settings` | Identity, autonomy, vault, integrations, danger zone | **UNCONFIRMED** |

### Backend routers (~110 endpoints)

| Router | Endpoints | Domain |
| --- | --- | --- |
| `auth.py` | 11 | register, login, demo, Google OAuth, me, integrations |
| `chat.py` | 7 | conversations CRUD, chat, SSE stream |
| `workspace.py` | 15 | emails, events, tasks, free slots, conflicts, briefs |
| `intelligence.py` | 16 | skills, assistant identity, heartbeat, grants, compaction |
| `hub.py` | 24 | plugins, channels, vault, documents, schedules, delegations |
| `assistant.py` | 17 | briefing, activity, notifications, memories, automations, settings |
| `account.py` | 4 | spend, usage, export, delete |

### Registries

- **21 skills**: `EM01-03`, `CA01-03`, `MP01`, `TK01-02`, `MM01`, `CT01`, `BR01`,
  `NT01`, `SY01`, `RS01-02`, `DC01-02`, `DL01-02`, `FG01`
- **12 plugins**: 5 core (non-removable), 3 optional, **4 deliberately unavailable**
- **31 tools**, each owned by a skill (a test asserts no orphan tools exist)

---

## 4. Working Features

Confirmed by direct testing.

| Feature | Evidence |
| --- | --- |
| **Landing page** | Renders fully; nav (How it grows, Skills, Pricing, FAQ), hero, CTAs, skill registry section |
| **Login form** | Renders correctly; email + password + submit; correct `autoComplete` hints |
| **Signup toggle** | "Create one" switches heading to "Create your AURA account", reveals Name field, button becomes "Create account" |
| **Signup client validation** | Blocks submit under 8 chars: "Password must be at least 8 characters." |
| **Registration (server)** | Account `udaysk2008@gmail.com` exists — duplicate returns `409 That email is already registered` |
| **Login rejection** | `401 Incorrect email or password` |
| **Email enumeration defence** | Identical 401 for unknown user and wrong password — verified across both test emails |
| **Server-side validation** | `422` for password < 8; `422` for malformed email |
| **Protected route guard** | `/dashboard` while logged out → redirects to `/login` |
| **Session mechanism** | A live token returned `200` from `/api/auth/me` with the correct user object |
| **Database connectivity** | `/health/ready` → `{"status":"ready","checks":{"database":"ok"}}` |
| **Health endpoint** | `/health` → `{"status":"ok","app":"AURA AI","version":"1.0.0"}` |
| **Preflight diagnostics** | `/api/health/preflight` returns 13 structured checks with fixes |
| **Security posture** | `failures: 0` — SECRET_KEY set, dedicated encryption key, demo login off, debug off, CORS scoped |

---

## 5. Partially Working Features

### 5.1 All AI features — degraded to a keyword router
**Severity: CRITICAL**

`llm_provider: "mock"`. `llm.py:351` shows the mock is a regex router
(`|create task|todo:?|to-?do:?`). Affects chat, triage, drafting, briefings,
research, summarisation, memory extraction.

*Works:* every screen renders, requests succeed, results display.
*Doesn't:* there is no reasoning behind any of it.
*Fix:* set `ANTHROPIC_API_KEY`. Preflight names this explicitly.

### 5.2 Semantic memory and document search — lexical fallback
**Severity: HIGH**

Preflight: *"No embedding provider. Memory and document search fall back to word
overlap."* The `/memory` page advertises "Semantic search…" in its placeholder
but cannot match meaning across differing wording.

### 5.3 Onboarding — silent failure path
**Severity: HIGH**

`app/onboarding/page.tsx:83`:

```js
} catch {
  setBusy(false);   // no message, no console log
}
```

`app/(app)/layout.tsx:31` redirects to `/onboarding` while
`assistant.onboarded` is false. If `POST /api/assistant/hatch` ever fails, the
user is stranded permanently one step past a successful login with no
explanation. I found no evidence it is currently failing — `HatchIn`
(`schemas.py:458`) matches the client payload exactly — so this is a latent
robustness gap, not a confirmed break.

### 5.4 Web research — falls back to local data
Preflight: no `SEARCH_PROVIDER`. Research uses the user's own inbox/contacts/
documents and says so. Honest degradation, but the Research plugin's headline
capability is absent.

### 5.5 Outbound email — cannot send
Preflight: *"No send route. Drafts are produced but nothing can actually be
sent."* `send_email` reports failure honestly rather than pretending.

### 5.6 Rate limiting — ineffective on serverless
Preflight: *"Enabled, in-memory (per-process)."* Each Vercel invocation is a new
process, so per-process counters mean the effective limit is unbounded. Needs
`REDIS_URL`.

---

## 6. Broken Features

### 6.1 Login shows "Session expired" for a wrong password
**Severity: CRITICAL · Page: `/login` · Status: fixed locally, not deployed**

*Current:* wrong password renders `Session expired`.
*Expected:* `Incorrect email or password`.
*Cause:* `frontend/lib/api.ts:67` — a blanket 401 interceptor that treats every
401 as an expired session, including `POST /api/auth/login` where 401 means
"wrong credentials". The server's real `detail` is discarded.
*Reproduced:* live site, wrong password → `Session expired`.
*Fix:* exempt `/api/auth/login` and `/api/auth/register`. **Already applied
locally and verified** (see `AURA-AI-AUTH-FIX-REPORT.md`); uncommitted.

### 6.2 Automations never fire
**Severity: CRITICAL · Page: `/automations`**

`worker.py` is the only thing that fires automations. Vercel cannot run a
long-lived process. The UI lets you create rules that will never execute, with
no indication they are inert.
*Fix:* deploy the worker (`render.yaml` already defines a cron service for it).

### 6.3 Schedules never run
**Severity: CRITICAL · Page: `/schedules`**

Same cause. The placeholder invites "every weekday at 7:30am" — a schedule that
cannot fire. Silent failure of a headline feature.

### 6.4 Heartbeat does not run automatically
**Severity: HIGH**

Same cause. `POST /api/heartbeat/run` exists so the ⌘K "Catch me up" action can
trigger it manually, but the proactive background loop — the entire "it works
while you don't" premise — is dead.

### 6.5 Memory compaction never runs
**Severity: MEDIUM**

Same cause. README: *"Without it memory only grows, retrieval degrades as
duplicates crowd each other out, and every prompt costs more for less signal."*
Manual trigger exists via ⌘K.

### 6.6 Landing page advertises skills that do not exist
**Severity: MEDIUM · Page: `/`**

`components/landing/Bento.tsx:259-260` hardcodes 14 codes. Cross-referenced
against `agent/skills.py`:

- **Do not exist:** `MM02`, `DR01`, `AU01`, `PL01`
- **Real but not shown (11):** `EM03`, `CT01`, `BR01`, `SY01`, `RS01`, `RS02`,
  `DC01`, `DC02`, `DL01`, `DL02`, `FG01`
- Badge claims *"Skills v2 — all fourteen live"*; the catalogue has **21**.

*Fix:* render from `GET /api/skills` instead of a hardcoded array, or correct the
array. The count claim should be derived, not typed.

---

## 7. Currently Unavailable Features

All evidenced, all appear intentional.

| Feature | Evidence | Assessment |
| --- | --- | --- |
| GitHub plugin | `plugins.py:180` `available=False`, *"Needs a GitHub connector — not built yet."* | **Intentional** — greyed out with a stated reason |
| Travel plugin | `plugins.py:191` | **Intentional** |
| Expenses plugin | `plugins.py:202` | **Intentional** |
| Slack plugin | `plugins.py:213` | **Intentional** |
| Google (Gmail + Calendar) | Preflight: *"Not configured. Email and calendar screens run on the sample dataset."* | **Config gap** — needs `GOOGLE_CLIENT_ID`/`SECRET` |
| Google OAuth callback (`/auth/callback`) | Unreachable while Google is unconfigured | **Blocked by the above** |
| Demo login | `demo_login_enabled: false` — correct for production | **Intentional** |
| `POST /api/settings/seed-demo` | No client method in `api.ts` | **Backend without UI** — likely a dev seeding tool |
| `POST /api/automations/tick` | No client method in `api.ts` | **Backend without UI** — worker-facing, correct |

The plugin catalogue's approach here is a genuine strength: `plugins.py:9`
documents the decision to show unbuilt integrations greyed out with reasons
rather than hide them or ship stubs.

---

## 8. Missing Features That Should Be Added

Only items with evidence in the app's own purpose or structure. No generic
feature-list padding.

### 8.1 Password reset / forgot password — **CRITICAL**
**Gap:** `api/auth.py` has 11 endpoints; none of them reset a password. The
login form offers no "Forgot password?" link.
**Evidence:** password auth is now the *only* way in — demo login is disabled and
Google is unconfigured. A forgotten password is currently an unrecoverable
account lockout with no self-service path.
**Implementation:** token-emailed reset. Note this depends on §5.5 — outbound
email must work first.

### 8.2 Deployed background worker — **CRITICAL**
**Gap:** §6.2–6.5. Four features have complete implementations and UI, and cannot
run.
**Evidence:** `render.yaml` already defines the cron service; it is committed and
unused.
**Implementation:** apply `render.yaml` on Render pointing at the same Supabase
database, or Vercel Cron hitting `/api/automations/tick` and `/api/heartbeat/run`.

### 8.3 Email verification on signup — **HIGH**
**Gap:** `POST /api/auth/register` creates an active account from any
syntactically valid email with no ownership proof.
**Evidence:** the app ingests Gmail and calendar data. An unverified address is a
weak anchor for that much personal data, and enables trivial squatting on someone
else's address.

### 8.4 Change password / session management — **HIGH**
**Gap:** no endpoint to change a password, and no way to see or revoke sessions.
**Evidence:** `security.py` issues 7-day JWTs with no server-side revocation
list. A leaked token is valid for its full lifetime; the only remedy is rotating
`SECRET_KEY`, which signs out everyone.

### 8.5 Inbound channel rate limiting — **HIGH**
**Gap:** README's own limitations list: *"Inbound channels have no rate limit…
a leaked token currently costs you tokens as fast as someone can POST."*
**Evidence:** `POST /api/channels/inbound/{kind}` is public by design, and every
call can invoke the model. `DAILY_SPEND_CAP_USD` is the only backstop.

### 8.6 Channel token expiry — **MEDIUM**
**Gap:** README: *"Inbound channel tokens don't expire. Rotatable, but no TTL."*

### 8.7 Alembic baseline migration — **MEDIUM**
**Gap:** `backend/alembic/versions/` contains only `.gitkeep`. Alembic is
configured with zero revisions, so `AUTO_CREATE_SCHEMA=false` plus
`alembic upgrade head` yields an empty database.
**Evidence:** `.env.production.example` instructs exactly that sequence. Today
the schema exists only because `create_all` runs.

### 8.8 Onboarding error surfacing — **MEDIUM**
See §5.3. A one-line change, but it converts a silent dead end into a diagnosable
one.

---

## 9. Complete User Workflow Testing

| Workflow | Result |
| --- | --- |
| Land → read → "Get started" → login page | **PASS** — renders, navigation intact |
| Login page → toggle to signup → back | **PASS** — state resets correctly |
| Signup → short password → submit | **PASS** — blocked client-side with a clear message |
| Signup → existing email → submit | **PASS** — `409 That email is already registered` |
| Login → wrong password | **FAIL** — shows `Session expired` (§6.1) |
| Direct access to `/dashboard` logged out | **PASS** — redirected to `/login` |
| Login → dashboard → … → logout → login | **UNCONFIRMED** — cannot authenticate |
| Onboarding 4 steps → dashboard | **UNCONFIRMED** |
| Chat → send → response → persistence | **UNCONFIRMED** |
| Document upload → search → summarise | **UNCONFIRMED** |
| Create automation → trigger → observe | **WOULD FAIL** — worker not running (§6.2) |

---

## 10. AI Feature Testing

**Not meaningfully testable in this deployment.** `llm_provider: "mock"`.

Any test would measure `llm.py`'s keyword router, not the product. Results would
be misleading in both directions — the plumbing would look fine, and the
intelligence would be absent.

The mock is a deliberate, documented design choice (README: *"not intelligence,
but enough that every screen, tool path and confirmation flow works with no
keys"*). It is the right call for local development and the wrong state for a
live site.

**All AI features: UNCONFIRMED**, blocked on `ANTHROPIC_API_KEY`.

---

## 11. Account Testing

| Check | Result |
| --- | --- |
| Account exists | **CONFIRMED** — `409` on duplicate registration |
| Password hashing | **CONFIRMED** — bcrypt, salted, verified by 14 passing tests |
| Wrong password rejected | **CONFIRMED** — `401` |
| Enumeration resistance | **CONFIRMED** — identical 401 across both accounts |
| Token issuance | **CONFIRMED** — valid JWT, 7-day expiry, `sub`/`iat`/`exp` |
| `/api/auth/me` | **CONFIRMED** — `200`, correct user, `is_demo: false` |
| Session persistence across refresh | **UNCONFIRMED** |
| Profile / settings / preferences | **UNCONFIRMED** |
| Logout → re-login | **UNCONFIRMED** |
| Data isolation between accounts | **UNCONFIRMED** — needs two live sessions |
| `udaykhandagale3@gmail.com` exists? | **UNCONFIRMED** — determining this requires registering it, which would create an account |

**Why so much is unconfirmed:** I do not enter passwords into forms. This is a
fixed constraint, not a judgement about the request. Roughly 70% of this
application sits behind that door.

**To close the gap:** log in yourself and leave the tab open. I can then drive
every authenticated screen without ever handling the credential, and convert
most UNCONFIRMED rows into tested results.

---

## 12. UI/UX & Responsive Testing

**Desktop (1568×700):** landing page, login and signup all render correctly. No
overflow, clipping or overlap observed. Loading states present (`Spinner`), and
`ErrorBoundary` wraps app content (`(app)/layout.tsx:58`).

**Mobile/tablet: UNCONFIRMED.** My viewport resize did not take effect in the
captured screenshots, so I have no evidence either way and will not claim
otherwise. The source contains dedicated mobile components — `MobileNav`,
`MobileTopBar`, and a `hidden lg:flex` sidebar — so the intent is clearly there.

**One observation:** on a cold load the login page displayed *"Connecting to
API…"* for several seconds before resolving. Consistent with a serverless cold
start plus a Tokyo database round trip (§17).

---

## 13. Error Handling

**Strong.** Better than most codebases of this size.

- `describeFailure()` in `login/page.tsx` distinguishes a missing API address
  from a backend that is down from a CORS refusal, and gives a specific fix.
- `ErrorBoundary` per route.
- `preflight.py` returns 13 structured checks, each with `level`, `detail` and an
  actionable `fix`.
- Degradation is honest throughout: no search key → research says so; no send
  route → `send_email` reports failure rather than pretending.
- Validation is enforced on both client and server.

**Two gaps:**
1. Onboarding's silent catch (§5.3).
2. `auth.tsx:34` — when `api.me()` throws, `user` is set to null but the dead
   token is left in localStorage, so every subsequent page load makes one
   guaranteed-failing request. Cosmetic.

---

## 14. Source-Code Audit

**Zero `TODO`, `FIXME`, `XXX` or `HACK` markers** across the entire codebase.
Genuinely unusual.

| Category | Finding |
| --- | --- |
| Placeholders | All are legitimate HTML `placeholder` attributes on inputs |
| "Coming soon" | One instance, `settings/page.tsx:826` — an integration badge driven by `available` from the catalogue, not a hardcoded stub |
| Mock data | `llm.py` mock provider — deliberate, documented, currently active in production |
| Sample data | `services/demo.py` seeds a realistic workspace — deliberate |
| Dead code | None identified |
| Unused endpoints | 2: `/api/settings/seed-demo`, `/api/automations/tick` — both plausibly worker/dev facing |
| Hardcoded data | `Bento.tsx:259` skill codes — **a real defect** (§6.6) |
| Tests | 93 total. 5 pre-existing failures in skills/plugins/channels, all `StopIteration` from `next(...)` in `intelligence.py:112` and `:125` |

**Uncommitted change present:** `frontend/lib/api.ts` carries the §6.1 fix from
the prior task. `git status` shows only that file plus two report markdowns.

**Known limitations the README documents honestly** (all verified present in
code): single worker only, no channel token TTL, similarity computed in Python,
skill-level not token-level streaming, regex-triggered correction detection,
vault protects against the model not the operator, minimal cron matcher,
in-process OAuth state.

---

## 15. Code vs Live Application Comparison

| Feature | Code | UI | Live | Verdict |
| --- | --- | --- | --- | --- |
| Password auth | ✅ | ✅ | ✅ | Works |
| Demo login | ✅ | ✅ conditional | ❌ disabled | Correctly gated |
| Google OAuth | ✅ | ✅ conditional | ❌ unconfigured | Config gap |
| Chat / agent | ✅ | ✅ | ⚠️ mock | Degraded |
| Skills registry | ✅ 21 | ✅ | ⚠️ landing shows 14, 4 fake | **Mismatch** |
| Plugins | ✅ 12 | ✅ | ✅ 4 greyed with reasons | Correct |
| Documents upload | ✅ | ✅ + drag-drop | ⚠️ no embeddings | Degraded |
| Memory | ✅ | ✅ | ⚠️ lexical only | Degraded |
| Automations | ✅ | ✅ | ❌ no worker | **UI without runtime** |
| Schedules | ✅ | ✅ | ❌ no worker | **UI without runtime** |
| Heartbeat | ✅ | ✅ | ❌ auto; ✅ manual | Partial |
| Compaction | ✅ | ✅ ⌘K | ❌ auto; ✅ manual | Partial |
| Vault | ✅ | ✅ | ✅ | Works (unconfirmed live) |
| Channels | ✅ 5 | ✅ | ⚠️ no rate limit | Risk |
| Export / delete account | ✅ | ✅ | — | Unconfirmed |
| Spend cap | ✅ | ✅ | ✅ $5/user/day | Works |
| Alembic | ⚠️ configured, 0 revisions | — | ⚠️ `create_all` only | Gap |

**The dominant pattern: complete implementations with no runtime.** Automations,
schedules, heartbeat and compaction are all fully built, fully wired to UI, and
inert because one process isn't deployed.

---

## 16. Security & Access-Control Findings

**Good, and materially improved today.**

| Check | Result |
| --- | --- |
| `SECRET_KEY` | ✅ Set (was the built-in dev default on a public repo hours ago) |
| `TOKEN_ENCRYPTION_KEY` | ✅ Dedicated key, no longer derived |
| Password hashing | ✅ bcrypt, salted; corrupted-hash inputs fail closed |
| Google-only accounts | ✅ Cannot password-login — empty hash never verifies |
| Email enumeration | ✅ Identical 401 across cases |
| Protected routes | ✅ Redirect to `/login` |
| Demo login | ✅ Disabled in production |
| Debug mode | ✅ Off — internal errors not returned to clients |
| CORS | ✅ Exactly 1 origin |
| Vault | ✅ Values never returned by the API; masked hints only |
| Input validation | ✅ Client and server |

**Outstanding risks:**

1. **Inbound channels unthrottled** (§8.5) — public endpoints, each call can
   spend model tokens.
2. **No token revocation** (§8.4) — 7-day JWTs, no server-side blacklist.
3. **Rate limiting ineffective on serverless** (§5.6).
4. **No email verification** (§8.3).
5. **Supabase `service_role` key was exposed** in a plaintext note and in
   conversation. Unused by this codebase, but it bypasses row-level security
   entirely. **Rotate it.**

I performed only non-destructive, read-oriented checks against the authorised
account. No brute force, no denial of service, no data modification.

---

## 17. Performance & Reliability Findings

Observations only — no synthetic benchmarks.

| Observation | Detail |
| --- | --- |
| Cold start latency | Login page showed *"Connecting to API…"* for several seconds before resolving |
| Cross-Pacific database | Supabase is in Tokyo (`ap-northeast-1`); the Vercel function runs in Washington DC (`iad1`). Every query crosses the Pacific. The Neon database previously attached was in `iad1`. |
| Connection pooling | ✅ Handled — transaction pooler, prepared statements disabled, `NullPool` |
| Console errors | 5 seen, **all the same benign React hydration warning** caused by a browser extension injecting `bis_skin_checked`, on the localhost dev build. **Not an application defect.** |
| Failed network requests | None observed |
| Infinite loading / freezes | None observed |
| Supabase health | `Healthy`, NANO compute, 6/60 connections, 0 security/performance advisories |

---

## 18. Feature Priority Matrix

| Feature | Status | Tested | Problem | Priority | Needs Fix? | Needs Addition? |
| --- | --- | --- | --- | --- | --- | --- |
| Landing page | WORKING | Yes | — | — | No | No |
| Login form | WORKING | Yes | — | — | No | No |
| Login error message | BROKEN | Yes | Shows "Session expired" | Critical | Yes (done, undeployed) | No |
| Signup | WORKING | Yes | — | — | No | No |
| Signup validation | WORKING | Yes | — | — | No | No |
| Protected routes | WORKING | Yes | — | — | No | No |
| Password reset | MISSING | N/A | No recovery path at all | Critical | No | Yes |
| AI / chat | PARTIALLY WORKING | No | `provider: mock` | Critical | Yes (config) | No |
| Automations | BROKEN | No | Worker not deployed | Critical | Yes | No |
| Schedules | BROKEN | No | Worker not deployed | Critical | Yes | No |
| Heartbeat | PARTIALLY WORKING | No | Auto disabled | High | Yes | No |
| Memory compaction | PARTIALLY WORKING | No | Auto disabled | Medium | Yes | No |
| Semantic search | PARTIALLY WORKING | No | No embeddings | High | Yes (config) | No |
| Email verification | MISSING | N/A | Unverified accounts | High | No | Yes |
| Change password | MISSING | N/A | No endpoint | High | No | Yes |
| Inbound rate limit | MISSING | N/A | Unthrottled public route | High | No | Yes |
| Rate limiting | PARTIALLY WORKING | No | Per-process on serverless | High | Yes (Redis) | No |
| Landing skill codes | BROKEN | Yes | 4 fabricated, 11 omitted | Medium | Yes | No |
| Onboarding errors | PARTIALLY WORKING | Partial | Silent catch | Medium | Yes | No |
| Alembic baseline | MISSING | N/A | Zero revisions | Medium | No | Yes |
| Google integration | UNAVAILABLE | No | Unconfigured | Medium | Yes (config) | No |
| Outbound email | PARTIALLY WORKING | No | No send route | Medium | Yes (config) | No |
| Web research | PARTIALLY WORKING | No | No search key | Medium | Yes (config) | No |
| Channel token TTL | MISSING | N/A | Tokens never expire | Medium | No | Yes |
| GitHub/Slack/travel/expenses | UNAVAILABLE | Yes | Connectors not built | Low | No | No — intentional |
| Dashboard, chat, emails, calendar, tasks, memory, documents, hub, skills, channels, notifications, settings | UNCONFIRMED | No | Cannot authenticate | — | — | — |

---

## 19. Recommended Fixes

1. **Deploy the `api.ts` 401 fix** — already written and verified, uncommitted.
2. **Set `ANTHROPIC_API_KEY`** — single highest-impact change; turns the demo
   into the product.
3. **Deploy the worker** — `render.yaml` is ready; restores 4 dead features.
4. **Fix `Bento.tsx:259-260`** — render from `/api/skills` rather than a
   hardcoded array; derive the count.
5. **Surface onboarding errors** — replace the bare `catch` with a message.
6. **Clear stale tokens in `auth.tsx`** — call `clearToken()` when `me()` fails.
7. **Set `REDIS_URL`** — makes rate limiting real.
8. **Set `OPENAI_API_KEY`** — embeddings only; ~$0.02/M tokens.
9. **Rotate the Supabase `service_role` key.**
10. **Reconsider the Tokyo database** — or accept the trans-Pacific latency.

---

## 20. Recommended New Features

Full rationale in §8: password reset (Critical), deployed worker (Critical),
email verification (High), change password + session revocation (High), inbound
channel rate limiting (High), channel token TTL (Medium), Alembic baseline
(Medium).

---

## 21. Implementation Roadmap

### Phase 1 — Critical
1. Deploy the `api.ts` login-error fix.
2. Set `ANTHROPIC_API_KEY`.
3. Deploy the worker via `render.yaml`.
4. Build password reset (blocked on SMTP).

### Phase 2 — High
5. Email verification on signup.
6. Change password + session revocation.
7. Rate limit inbound channels.
8. `REDIS_URL` for shared rate limits.
9. `OPENAI_API_KEY` for embeddings.

### Phase 3 — Medium
10. Fix the landing page skill registry.
11. Surface onboarding errors.
12. Clear stale tokens.
13. Alembic baseline migration.
14. Channel token TTL.
15. Configure Google OAuth + SMTP.
16. Fix the 5 pre-existing test failures.

### Phase 4 — Missing Core
17. Build one unavailable connector (Slack is the highest-value).
18. Deployment-region alignment for the database.

### Phase 5 — Future
19. Token-level streaming.
20. `pgvector` migration when memory exceeds tens of thousands of chunks.
21. Multi-agent coordination.

---

## 22. Final Assessment

**This is a well-built application in a badly-configured deployment.**

The engineering is genuinely strong: zero TODOs, honest degradation everywhere,
a self-diagnosing preflight endpoint, an architecture where disabling a skill
provably removes its tools rather than merely instructing the model not to use
them, and a vault whose design constraint — the model never sees a value — is
enforced by ordering rather than hope.

Almost nothing is *broken*. What is wrong is that the product's two defining
capabilities are switched off:

1. **No model key** — the assistant cannot reason.
2. **No worker** — the assistant cannot act while you are away.

Both are configuration, not code. Neither requires development work. Fixing them
would move this from "impressive demo" to "working product" in an afternoon.

The genuine code defects are few and small: one misleading error message
(already fixed, undeployed), a hardcoded landing-page array advertising four
skills that do not exist, and a silent catch in onboarding.

The most serious *gap* is the absence of any password reset. Now that password
auth is the only way in, a forgotten password means a permanently locked account
with no self-service recovery.

**Confidence: partial.** I verified the unauthenticated surface, the full source,
and the live API's behaviour directly. I could not verify the authenticated
application — roughly 70% of it — because I do not enter passwords into forms.
Every one of those rows is marked UNCONFIRMED rather than assumed. If you sign in
and leave the session open, most of them can be resolved properly.
