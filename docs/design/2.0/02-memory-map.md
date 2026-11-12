# Audit map: memory, work records, identity, persistence as of v1.0.0 (paths under backend/app unless noted)

## 1. Memory today
- Model: models.py:266-292 `Memory` (kind, content, source, confidence, embedding JSON list, last_used_at, use_count, pinned).
- Kinds (closed set in 3 places): preference|contact|project|decision|style|habit|fact — tools.py:920 enum, schemas.py:306-311 MemoryIn Literal, frontend app/(app)/memory/page.tsx:17 KINDS.
- Write path:
  - services/memory.py:74 remember(db,user_id,content,kind,source,confidence,pinned): dedup only within the same kind at lexical Jaccard > 0.85 (:85-93; duplicate bumps confidence, does NOT touch content), else insert with embedding=embed_text(content) (:102).
  - Tool save_memory: agent/tools.py:912-936 (source="chat", conf .85), also writes ActivityLog. search_memory: tools.py:939.
  - Corrections: agent/coordinator.py:262-306 maybe_learn -> skill_registry.teach (UserSkill.learned_notes, last 10) AND remember(kind="preference", source="correction", conf .95) AND identity.record_correction.
  - Manual: api/assistant.py:123 POST /api/memories (source="manual", conf 1.0).
  - Onboarding: api/intelligence.py:236-253 writes pinned conf-1.0 memories ("Their role: ...", about, "Goal: ..." max 5), source="onboarding".
  - Compaction: services/memory.py:139 compact: merge at Jaccard > .75, promote to pinned if use_count>=5 and conf>=.8, decay x0.9 if use_count==0 and unpinned and source != "manual", delete when conf < .25. Called from worker.py:46 compact_all and POST /api/memories/compact (intelligence.py:323-327).
- Read path:
  - memory.py:110 search: loads ALL the user's rows (no kind filter); cosine if both vectors exist (:121) else Jaccard (:123); pinned +0.25; min_score .05; limit 6; increments use_count/last_used_at on hits (:132-135).
  - memory.py:198 build_context_block -> "What you know about this user..." lines `- [kind] content` + ids; injected at coordinator.py:230 (_prelude) -> build_system_prompt (agent/prompts.py:36,134); ids stored in Message.meta.memories_used.
  - Other readers: services/briefing.py:207 (draft_reply), services/documents.py:22,164,180 (reuses _cosine/_lexical/embed_text).
- Embeddings: memory.py:64 embed_text -> get_provider().embed([text]); [] on any exception. Only OpenAIProvider implements embed (llm.py:204, text-embedding-3-small). Anthropic and mock -> lexical Jaccard. Rows saved without a vector are never re-embedded; mixed rows are ranked by different metrics.
- API (api/assistant.py): GET /api/memories?kind= (:110), POST /api/memories (:123), GET /api/memories/search?q= (:132), DELETE /api/memories/{id} (:140), PATCH /api/memories/{id}/pin (:150), POST /api/memories/compact (intelligence.py:323). NO edit-content endpoint.
- Frontend: app/(app)/memory/page.tsx; lib/api.ts:945-950, :1024, interface MemoryItem :328.

## 2. Records of work today
- Message (models.py:141): role, content, meta JSON. Written by coordinator.py:527/529 persist_exchange (callers: api/chat.py:152, services/schedules.py:240, services/channels.py:237, stream path coordinator.py:604). Assistant meta: tool_calls (name, skill_code, arguments), skill_runs, memories_used, steps, latency_ms, learned, tokens, cost_usd, capped, pending_action_ids. No outcome flag; user row and assistant row linked only by order.
- SkillRun (:411): code, trigger, summary (500 chars), status success|error, detail={"tool": name}, duration_ms. Written only in agent/skills.py:859 record_run from execute_tool (coordinator.py:~118). No conversation/message id, no arguments, no result.
- ActivityLog (:323): actor, action, target(300), status, detail JSON. Written by tools.py:242 _log, coordinator.py:111/:640/:663, automation/engine.py:238, api/device.py:112/167. No conversation link.
- PendingAction (:337): conversation_id (plain String), tool_name, arguments, preview, status, resolved_at, skill_code. Written at coordinator.py:132 _queue_action and automation/engine.py:215.
- HeartbeatReport (:448), UsageRecord (:568; written by usage.py:179 record from coordinator.py:391 and api/local.py:181), Notification (:311).
- models.Task (:161) is the USER'S to-do list (create_task/delete_task tools, /tasks page). Not an assignment record.
- Missing for "what was I given, how did I do it, what was the outcome": no entity tying one request to the work it triggered; no FK from SkillRun/ActivityLog/UsageRecord to a run; tool results not kept; no outcome judgement for a whole turn; schedule/automation/heartbeat work has no request text; no summary/embedding of how a task was done.

## 3. Where preferences live today
- Memory kinds preference/style/habit: reach the prompt only if retrieved for the current message (top 6).
- UserSkill.learned_notes (models.py:404, last 10 per skill) -> skills.py:809 learned_notes_block -> coordinator.py:233, always injected ("Corrections this user has given you (follow them):", capped 20 lines).
- Assistant.personality/name -> identity.py:207 persona_prompt (which also embeds the STAGE text). Assistant.goals reaches the prompt only via seeded "Goal:" memories. pronoun/avatar UI-only.
- UserSettings.tone (models.py:110; schemas.py:79/90; lib/api.ts:418) is NEVER read by prompt code: dead config.
- Nothing is keyed by task type.

## 4. Identity and onboarding
- services/identity.py: get_or_create :80 (auth.py:138 on every login, auth.py:623 Google callback, intelligence.py:213/223); signals :91; compute_stage :105; progress_to_next :117; refresh_stage :151 (coordinator.py:231 every turn); record_interaction :162 (coordinator.py:495); record_correction :170 (coordinator.py:305, intelligence.py:185); profile :178 (intelligence.py:206/217/255); persona_prompt :207 (coordinator.py:243).
- Endpoints: POST /api/assistant/hatch (intelligence.py:220-255, schema HatchIn schemas.py:480-488); GET/PATCH /api/assistant (intelligence.py:204/209; AssistantOut schemas.py:455-469 incl. stage, stage_label, stage_blurb, signals, progress, days_together; AssistantPatch :472, blind setattr).
- Frontend gate: app/(app)/layout.tsx:38-41 redirects to /onboarding when assistant.onboarded is false; app/onboarding/page.tsx:61,73,86; context lib/assistant.tsx.
- Consumers of stage/interactions/actions_taken/corrections: models.py:358-387; identity.py; coordinator.py:231/243/305/495; intelligence.py:185/206/217/255; schemas.py:455-469; alembic/versions/0001_initial_schema.py:90-93; tests/test_smoke.py:339-347, :373-386; tests/test_site_fixes.py:322-340; frontend lib/api.ts:426, :437-447; components/Mascot.tsx:14-45 (stage prop with literals on login, reset-password, setup, onboarding, landing, HeroPreview); components/Sidebar.tsx:143-180,294; dashboard/page.tsx:175-185,438-459; settings/page.tsx:408-445; chat/page.tsx:469-477; components/ui.tsx (progressHint/Progress); app/page.tsx:43-63,170-216; components/landing/Faq.tsx:9; README.md:356.

## 5. Persistence and migrations
- db.py:152 init_db: if AUTO_CREATE_SCHEMA false -> log and return; else Base.metadata.create_all then apply_additive_migrations (:105-150). Errors swallowed and logged (:170+): the app boots even if schema init fails.
- apply_additive_migrations only ADDS model columns missing in the DB (ALTER TABLE ADD COLUMN with DEFAULT from _default_literal db.py:83). Never drops/renames/retypes.
- main.py:47 lifespan calls init_db(); worker.py:124/185 too. Nothing runs `alembic upgrade` automatically: vercel.json has no build/migrate command; aura_desktop.py sets DATABASE_URL to sqlite in the data folder (:211) and leaves AUTO_CREATE_SCHEMA default True. Only docker-compose.yml:39-42 runs `alembic upgrade head || echo ...`. config.py:72 auto_create_schema: bool = True.
- Alembic: alembic/env.py reads DATABASE_URL from settings, render_as_batch for SQLite (:49), compare_type=True. versions/ has only 0001_initial (every table as of 1.0.0). DEPLOY.md:106-120 documents `alembic stamp head` + AUTO_CREATE_SCHEMA=false for Vercel; whether that was done on the live DB is UNKNOWN (the documented Vercel env list does not include AUTO_CREATE_SCHEMA, so the live DB is probably unstamped and auto-created). Handle both.
- CI (.github/workflows/ci.yml:49-56, ubuntu/device only): AUTO_CREATE_SCHEMA=false DATABASE_URL=sqlite:///./migration-check.db alembic upgrade head, then alembic check. Postgres is never exercised in CI.
- Removing a model attribute leaves the column; Assistant.interactions/actions_taken/corrections/stage are NOT NULL with Python-side defaults only (no server_default), so INSERTs of new Assistant rows would fail (get_or_create runs on every login).
- Desktop SQLite DBs have no alembic_version table. SQLite DROP COLUMN needs batch mode for indexed/constrained columns.
- Live DB: Supabase Postgres through the transaction pooler (port 6543, pgbouncer transaction mode) via DATABASE_URL_OVERRIDE. psycopg 3.

## 6. Version and release
- backend/app/version.py:7 __version__ = "1.0.0". Consumers: app/__init__.py, main.py:36/96/198/288, api/auth.py:19/174, aura_desktop.py:590 (+ update check :347-363), desktop/aura.spec:30,111, Windows installer via /DAppVersion. desktop/windows/aura.iss:12 has a hardcoded fallback version. frontend/package.json is "0.1.0" (unchecked). desktop/RELEASE_NOTES.md.
- .github/workflows/desktop-release.yml: triggers on v* tags, release/v* branches, PRs touching desktop/build files, manual. Checks version.py equals the tag/branch; builds Windows, macOS ARM, macOS Intel, Linux; each runs `--smoke-test`; publishes release with checksums. Asset names are a contract with frontend/lib/downloads.ts.

## Facts most likely to cause a bug
- compact() merges/decays/deletes non-manual memories; search() ranks every kind together and bumps use_count.
- Tests assert the stage API shape; frontend `Assistant` type uses stage fields, so `tsc --noEmit` fails if only the backend changes.
- The onboarding guard depends on Assistant.onboarded and the hatch endpoint.
- Per-skill corrections (learned_notes) are a second preference store, always injected.
- The release workflow fails if version.py does not equal the tag; aura.iss:12 can go stale.
