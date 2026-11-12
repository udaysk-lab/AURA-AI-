# Audit: where stages and trust levels appear (v1.0.0), and dead-code candidates

Re-grep for exact lines; this lists files so nothing is missed.

## Stage concept — 27 files, 191 lines
Patterns: stage, STAGES, STAGE_BY_KEY, compute_stage, refresh_stage, progress_to_next, signals(, record_interaction, record_correction, stranger, acquaintance, colleague, chief_of_staff, "chief of staff", interactions, actions_taken, corrections (the counter), hatch, hatched, "you raise", days_together, copy about the assistant growing/evolving/being raised.
NOT the concept: SQLAlchemy relationship(), "staging", Contact.relationship_tier, the "Relationship Tracking" skill, "stranger's process" in device/shell.py, UserSkill corrections text (models.py:406).
- backend: app/services/identity.py, app/models.py (Assistant 358-387), app/agent/coordinator.py (231, 259, 305, 495), app/schemas.py (455-488), app/api/intelligence.py (185, 204-255), alembic/versions/0001_initial_schema.py (90-93; history, do not edit)
- frontend: components/Mascot.tsx, components/ui.tsx (65-72), components/Sidebar.tsx, components/landing/{Faq,HeroPreview,LandingNav,Pricing}.tsx, app/page.tsx, app/(app)/chat/page.tsx (469-477), app/(app)/dashboard/page.tsx, app/(app)/settings/page.tsx (408-445), app/(app)/layout.tsx (38), app/onboarding/page.tsx, app/login/page.tsx (295), app/setup/page.tsx (58), app/reset-password/page.tsx (71), lib/api.ts (426-447, 1000, 1009)
- tests: backend/tests/test_smoke.py (339-386), backend/tests/test_site_fixes.py (322-340)
- docs: README.md (356, 380), AURA-AI-COMPLETE-FEATURE-AUDIT.md, AURA-AI-AUTH-FIX-REPORT.md (both files are deleted in 2.0)

## Trust / autonomy levels — 46 files, 370 lines
Patterns: autonomy, autonomy_level, default_autonomy_level, tier/TIERS/TIER_RANK/TIER_LABELS/_TIER_CEILING, normalise_tier, risk_class, requires_approval, heartbeat_allows, autonomy_floor/floor=, ApprovalGrant/approval_grants, active_grant, list_grants, grant(, revoke(, "Trust"/"trust"/"Autonomy" in UI and docs, strict/conservative/relaxed/"full access" as level names, DEVICE_EXEC_MODE.
- backend/app: agent/autonomy.py, config.py, models.py (108-109, 427-444), schemas.py (78, 89, 416, 488-510), agent/coordinator.py, agent/skills.py (floors on every skill), agent/prompts.py, agent/tools.py (88-89), agent/device_tools.py, services/heartbeat.py, services/schedules.py, services/channels.py, plugins.py, automation/engine.py, api/intelligence.py, api/chat.py, api/remote.py, api/device.py, api/assistant.py (settings PATCH, pending-actions), device/__init__.py, device/policy.py, device/apps.py, device/screen.py; backend/aura_desktop.py (127)
- frontend: lib/api.ts, app/(app)/settings/page.tsx, app/onboarding/page.tsx, app/(app)/skills/page.tsx (219), app/(app)/device/page.tsx (246), app/page.tsx, components/landing/{Pricing,OnYourComputer,Bento,Faq,GetAura}.tsx
- tests: backend/tests/test_smoke.py, test_device.py, test_desktop.py
- docs/config: README.md, DEVICE.md, .env.example, .env.production.example, start-aura-local.bat, aura-local.sh, desktop/RELEASE_NOTES.md, desktop/README.md

## Dead-code and stale-doc candidates (confirm by grep + tests before deleting)
- Top level: run-aura.bat, _aura-backend.bat, _aura-frontend.bat (high); AURA-AI-AUTH-FIX-REPORT.md, AURA-AI-COMPLETE-FEATURE-AUDIT.md (high); start-aura.bat, cleanup.ps1 (medium: verify; README/DEPLOY may mention start-aura.bat and run-worker.bat as the hosted-style launchers)
- Backend: device/files.py:29 FileEntry; schemas.py:756 PreflightOut; services/mailer.py:27 available_routes; agent/skills.py:892 activity_line; agent/skills.py:222 _sum_file_read; automation/engine.py:301 due_schedules (medium); device/screen.py:132-166 screen_size, cursor_position, move_mouse, double_click (medium: may be wired up by the new computer-access work instead)
- Config never read: embedding_dimensions (config.py:124), heartbeat_default_interval_minutes (:152), heartbeat_default_quiet_hours (:153), default_autonomy_level (:154), destructive_tools (:147) + destructive_tool_set (:369)
- Shims: autonomy.normalise_tier legacy values + tests/test_smoke.py:216 test_legacy_tier_values_still_resolve; services/demo.py:470 _LEGACY_WINDOW (low)
- Other: frontend/Dockerfile unreferenced (medium); DEPLOY.md:46 Neon history note (low); "Legacy neutral ramp" frontend/tailwind.config.ts:30 + frontend/DESIGN.md:41 (low)
- Dead data fields: UserSettings.tone; AutomationRule.requires_confirmation (stored, never read)
