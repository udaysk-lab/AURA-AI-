# Audit map: execution path and gate as of v1.0.0 (paths under backend/app unless noted)

## 1. Execution path
- api/chat.py:147 chat() and :168 chat_stream(): read tier = get_user_settings().autonomy_level (:150, :171), call run_agent / stream_agent. Stream opens its own DB session (:176-190), emits SSE, ends with [DONE].
- agent/coordinator.py:566 stream_agent wraps :314 iter_agent, chunks text, calls persist_exchange (:520). :500 run_agent drains iter_agent.
- iter_agent: normalise_tier (:336); maybe_learn (:339, defined :263; correction regex -> teach() onto a skill's learned_notes + memory; runs for EVERY trigger incl. inbound); _prelude (:227) builds system prompt; _history (HISTORY_LIMIT 20); _available_specs (:252) = tools of enabled skills only; loop `for step in range(settings.max_agent_steps)` (:357).
- Step limit: config.py:125 default 8; aura_desktop.py:213 sets 16. Out of steps -> canned message (:488).
- Each step: usage_service.check (:363; background triggers get 40% of daily cap, usage.py:82,217; "inbound" is NOT in that list); provider.complete (:382; background_model for non-chat triggers); per tool call: requires_approval (:407) -> _queue_action (:128, :408) OR execute_tool (:90, :433) -> REGISTRY[name].handler(ToolContext(db,user)) (tools.py:43-57; ToolContext is just db+user).
- Device tools register into the same REGISTRY via import at tools.py:1396; handlers wrapped by _safe (device_tools.py:27), call app/device/*.py.
- Tool results go back as role=tool messages truncated to 8000 chars (:~455); screenshots lifted into a user image message.
- System prompt (agent/prompts.py build_system_prompt :98; template SYSTEM :7): persona (identity_service.persona_prompt), "Trust level: {TIER_LABELS[tier]}" (:21), now/user/timezone, Google note, device_block (:77), memory_block, learned_block (skill learned_notes + vault key NAMES). The tier is a label only; enforcement is in code.

## 2. The gate today
- Tier reads: chat.py:150,171; services/schedules.py:236; services/channels.py:235; services/heartbeat.py:261; api/intelligence.py:284 (manual heartbeat). Written by PATCH /api/settings (api/assistant.py:259) and onboarding (intelligence.py:233). Prompt label prompts.py:127. Default "conservative" (config.py:154).
- agent/autonomy.py requires_approval (:228): (1) risk_class(tool) in _TIER_CEILING[tier] (:136) -> no ask; (2) _shell_policy_decides (:208) -> no ask; (3) else ask unless active_grant (:180).
- Risk classes (:144): read / safe / external / irreversible; unlisted tools default to safe. Ceilings: strict=read; conservative=+safe; relaxed=+external; full=+irreversible.
  - READ_ONLY (20): summarize_inbox, search_emails, get_email, list_events, find_free_time, detect_conflicts, prepare_meeting, list_tasks, search_memory, list_contacts, get_daily_briefing, sync_google, draft_reply, web_research, brief_subject, search_documents, summarize_document, compare_documents, list_delegations, check_focus_blocks
  - DEVICE_READ_ONLY (10): read_file, list_directory, search_files, list_applications, system_info, list_processes, list_background_commands, process_output, read_clipboard, take_screenshot
  - SAFE_WRITE (7): create_task, complete_task, save_memory, notify, delegate_task, chase_delegation (only drafts text; sends nothing), desktop_notify
  - EXTERNAL_WRITE (11 names incl. the literal "external"): create_event (pushes to Google with attendees), write_file, edit_file, move_file, make_directory, open_app, close_app, set_clipboard, scroll_screen, fetch_url
  - IRREVERSIBLE (13): send_email, archive_email, delete_event, delete_task, run_command, run_in_background, stop_process, kill_process, delete_file, mouse_click, type_text, press_keys, send_process_input
- _shell_policy_decides applies to run_command/run_in_background at ANY tier: skips the ask when device_policy.classify (device/policy.py:~55) says auto (DEVICE_EXEC_MODE=full, or allowlist prefix match with none of ;&|<>`$(){}[]^! and not "dangerous") OR the command is denylisted/empty (shell.run refuses it anyway: device/shell.py:89 _deny_floor, called :105 and :270).
- Grants: TARGETED = {send_email: "to"} (:165): an "always" grant for send_email is per recipient; every other tool is grant-by-tool-name only.
- When approval is needed: a PendingAction row (models.py:337; user, conversation_id (plain string), tool_name, arguments JSON, preview, status pending/approved/rejected, skill_code). No expiry, no run id. SSE event `pending_action`; the model receives {"status":"awaiting_approval"} and is told to stop.
- Resume: the agent loop is NOT resumed. POST /api/pending-actions/{id} (api/assistant.py:238) -> coordinator.resolve_pending (:623). reject -> rejected + ActivityLog. once -> approved, execute_tool(trigger="manual") with stored args. always/window -> autonomy.grant (autonomy.py:239; window default 10 minutes), then execute. Result goes to the HTTP response only, never back to the model or conversation. Grants: GET /api/grants, DELETE /api/grants/{tool} (intelligence.py:306-320).
- Independent deciders: automation/engine.py:41 is_destructive + run_rule (:194); device/files.py:96 resolve (roots + protected paths); device/screen.py:23 _gui; device/apps.py:53 _check_name; device/runtime.py require_enabled. api/device.py:83 POST /api/device/run (the user's own console) runs shell with NO approval, only the denylist floor; blocked for paired remote devices.

## 3. Triggers
- Chat: tier = user setting; pending -> approval card; caps: spend + steps.
- Schedule: services/schedules.py run_one (:212) -> run_agent(trigger="schedule"), full toolset, user's tier. Needs approval -> PendingAction parked forever + urgent Notification (:240-260). 40% spend cap.
- Automation: automation/engine.py run_rule (:194) via on_email_received (:250, from api/workspace.py:131), on_task_due (:275), run_due_schedules (:335), /api/cron tick. Does NOT read tier, grants or enabled skills. Only: risk_class == irreversible -> queued as PendingAction; everything else (incl. external-class) runs immediately via execute_tool(trigger="automation"). AutomationRule.requires_confirmation is stored but never read. compile_rule (:115) accepts whatever tool names the LLM emits.
- Heartbeat: services/heartbeat.py run (:91) / run_all_due (:232). Calls execute_tool directly with hard-coded tools (sync_google, detect_conflicts, create_task, prepare_meeting; triage/notify via services). No requires_approval call. Caps: _can_run (:84) needs skill.proactive AND enabled AND heartbeat_allows(tier, skill.autonomy_floor) (the ONLY use of autonomy_floor). Device skills are proactive=False. Quiet hours + interval in due() (:63).
- Inbound channel: api/hub.py:172 (per-channel token only) -> services/channels.py handle_inbound (:218) -> run_agent(trigger="inbound") with the user's tier and the FULL toolset (device tools included on a local install). Pending actions mentioned in reply text. Not budget-capped as background. Inbound text passes through maybe_learn.
- CLI: backend/aura_cli.py is an HTTP client of /api/chat/stream; handles pending_action inline (_approve :176; y/a/10/n). Desktop: aura_desktop.py sets LOCAL_MODE=true (:196), MAX_AGENT_STEPS=16, DEVICE_EXTRA_PROTECTED_PATHS (:216-220), runs uvicorn + embedded worker.

## 4. Local computer access today
- Master switch settings.device_control_enabled (config.py:399) = DEVICE_CONTROL or LOCAL_MODE. Consulted in plugins.availability (plugins.py:260), skills.enabled_tools (skills.py:787), device/runtime.require_enabled, prompts.device_block, api/device.py _require_device. Local install auto-installs the device-control plugin (plugins.py:~290). Startup refuses device control when ENVIRONMENT=production.
- Files: device/files.py:96 resolve(): realpath against DEVICE_ALLOWED_ROOTS (default "~"), then DEVICE_PROTECTED_PATHS (default ~/.ssh,~/.aws,~/.gnupg,~/.config/gcloud,~/.kube,~/.docker/config.json) + DEVICE_EXTRA_PROTECTED_PATHS + ALWAYS_PROTECTED (config.py:493: ~/.aura, shell rc files, startup/autostart, git config). _holds_protected blocks moving/deleting a parent of a protected path.
- Shell: DV01 = run_command, run_in_background, send_process_input, stop_process; process_output, list_background_commands read. DEVICE_EXEC_MODE ask|allowlist|full + DEVICE_EXEC_ALLOWLIST + DEVICE_COMMAND_DENYLIST (default empty) + timeout 300s. The shell is NOT fenced by roots or protected paths.
- Apps: open_app/close_app; _check_name refuses scripts/programs-by-path (apps.py:53).
- Screen: take_screenshot is read class (whole screen, every tier). mouse_click/type_text/press_keys irreversible; scroll_screen external. DEVICE_SCREEN_CONTROL default true; needs pyautogui + a display (screen.py:23; runtime.py _probe lru_cache). Unused helpers in device/screen.py: screen_size, cursor_position, move_mouse, double_click.
- fetch_url (device_tools.py:~509-520): GET only, httpx follow_redirects=True, NO host filter (can hit localhost). Its docstring says "runs without asking at every tier", contradicting autonomy.py.
- Changeable at runtime via API: tier (PATCH /api/settings), skill toggles (PATCH /api/skills/{code}, intelligence.py:161), plugin install/uninstall (hub.py:96,106), grants (revoke only), LLM provider/keys/model (PUT /api/local/provider, local.py:116), REMOTE_ACCESS (PUT /api/local/remote, needs restart). Device status read-only (GET /api/device).
- Env/file-only: LOCAL_MODE, DEVICE_CONTROL, DEVICE_ALLOWED_ROOTS, DEVICE_PROTECTED_PATHS/EXTRA, DEVICE_EXEC_MODE/ALLOWLIST/DENYLIST, DEVICE_SCREEN_CONTROL, MAX_AGENT_STEPS, DAILY_SPEND_CAP_USD; read once at import (get_settings lru_cache).

## 5. Self-modification paths today
- No agent tool touches UserSettings, ApprovalGrant, UserSkill/InstalledPlugin, Schedule or AutomationRule. Agent-writable behaviour state: save_memory, and learned_notes via maybe_learn on user messages (incl. inbound).
- File tools cannot reach ~/.aura or the desktop install dir. BUT on a from-source local run the defaults are ./aura.db (config.py:51) and .env / ../.env (config.py:56) inside the repo under the allowed root "~": unprotected, so write_file/edit_file can edit them.
- Shell (after approval, or under allowlist/full exec mode) is unfenced: can read/modify the DB and settings files, or call the local API. POST /api/auth/local (auth.py:~183) hands a token to any loopback caller when LOCAL_MODE is set and no AURA_LOCAL_LOGIN_KEY is set (the desktop app sets the key; it sits in ~/.aura/aura.env, readable by a shell). That token allows PATCH /api/settings, PATCH /api/skills, etc.
- fetch_url is GET-only: can read the local API, cannot change state.
- type_text/press_keys/mouse_click can drive the AURA UI itself (click "Always allow" on its own approval card) if the AURA window is on screen; take_screenshot lets it see the card.

## 6. Tool inventory (60 tools, 28 skills, 14 plugins)
- By class: read 30, safe 7, external 10, irreversible 13.
- Per plugin: core-inbox 6, core-calendar 6, core-tasks 4, core-memory 3, core-planning 3, research 2, documents 3, delegation 3, focus 1, device-control 29; developer/travel/expenses/comms 0 (placeholders). 31 tools in tools.py, 29 in device_tools.py.
- Device skills: DV01 6 (shell), DV02 8 (files), DV03 3, DV04 3, DV05 5, DV06 3, DV07 1.
- Target-bearing args: send_email.to; create_event.attendees; delegate_task.assignee; run_command/run_in_background .command + workdir; read_file/write_file/edit_file/list_directory/search_files/make_directory/delete_file .path; move_file src/dst; fetch_url.url; open_app/close_app.name; kill_process/stop_process pid/session_id; mouse_click x/y; type_text.text; press_keys.keys; set_clipboard text.

## Facts most likely to bite a redesign
- Four independent deciders today (requires_approval for chat/schedule/inbound; automations; heartbeat; console + shell denylist). autonomy_floor is enforced only by the heartbeat.
- execute_tool and iter_agent do not check that a tool belongs to an enabled skill: a model-emitted call to a disabled skill's tool still dispatches.
- Approval does not resume the agent; PendingActions never expire and stay executable with stored arguments.
- Reading is ungated everywhere, including take_screenshot and read_file anywhere under ~ except the protected list.
- A single decision function needs (tool, args, trigger, mode, run) because six call sites pass different subsets today.
