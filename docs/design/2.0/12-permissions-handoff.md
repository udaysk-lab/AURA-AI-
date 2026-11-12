# Permissions: handoff notes between packages (newest last)

## After WP1-WP3 (committed)
New code: backend/app/permissions/{__init__,catalogue,match,shellparse}.py, backend/app/device/appnames.py,
revision pm01_permissions (EXPECTED_HEAD = "pm01_permissions"), models PermissionRule, DeviceFolder,
DeviceAppGrant, DeviceAppBlock, PermissionEvent and the new columns; plugins.plugin_for_tool and
plugins.assigned_systems; skills.enabled_tools drops tools with no catalogue entry.
Facts the next packages need:
- pending_actions.work_item_id is an indexed column WITHOUT a foreign key (work_items does not exist yet; the
  task-loop revision adds the FK). Every create/add in pm01 is guarded.
- Catalogue has 65 entries (60 tools + 5 loop bookkeeping tools). ToolPolicy has dyn_always_user (set on
  run_command and run_in_background). TargetSpec.default exists (search_files uses `root`, list_directory's
  path is optional; both default to ~).
- match.extract() raises BadArgs (fail closed); a non-email create_event attendee raises BadArgs.
- Shell: a program path is never stripped to its basename; wrappers/shells with an explicit directory are
  unwrapped for block/ask but marked opaque; $( ), backticks, $'..', ${, ( ) grouping, pwsh { }, eval, source
  and `.` make a segment opaque; an opaque segment containing a delete verb counts as a delete. Block/ask
  command rules match both the unwrapped text and the text as written. Numeric hosts match by equality only.
- Allow coverage ignores workdir and pid targets (floors check those); a rule with a target kind and no pattern
  covers every target of that kind.
- For WP4: reuse shellparse.Parsed.paths and Segment.deletes/opaque/dangerous, match.fold(..., always=True),
  match.normalize_path. Fix the fetch_url docstring. Call catalogue.log_missing() at startup (main.py).
  Tests deferred to WP4: self_paths_from_source_run, desktop_home_and_install, curl_localhost_denied,
  sqlite_db_denied, aura_env_denied.
- For WP5: use catalogue.default_disposition(kind, mode), match.first_match (block/ask), match.allow_cover
  (allow), match.rule_applies_to. extract() needs dialect=shellparse.dialect_for_shell_kind(capabilities()["shell"]["kind"])
  plus frontmost= and pid_app= from the device layer.
- For WP6/WP11: match.normalise_pattern(target_kind, pattern, effect, dialect) raises PatternError (API answers
  422); match.command_card_patterns(parsed) may return empty (card then offers only once/deny).

## After WP4-WP5 (committed)
New: app/device/net.py, app/permissions/{floors,gate,rules,events,triggers,interim,reviewer}.py, tests perm_helpers,
test_perm_fetch/decide/execute/events. execute_tool takes a required keyword `decision`; a grep-style test checks
nothing else calls a handler. Suite: 573 passed, 35 skipped.
- Call sites: iter_agent calls decide (chat, stream, schedule, inbound); resolve_pending calls decide_approved and
  enforces expires_at; automation engine calls decide per action; heartbeat uses run_gated; workspace buttons use
  ui_decision; the device console route uses console_decision.
- Use gate.build_context(...). Decision carries user_id and a MAC seal private to gate.py; execute_tool rejects
  another user's decision. Decision.proposed_rule is a RuleDraft whose `items` are (target_kind, pattern) pairs;
  an empty tuple means tool-wide. A failed permission-event write turns allow into deny AUDIT_FAILED.
- interim.py: until user_settings.extra["permissions_migrated"] is set, mode and rules are DERIVED at read time
  from the 1.0 level, ApprovalGrants, DEVICE_EXEC settings and automation actions; no rows are written. WP11
  writes the real rows (pm02) and deletes interim.py. run_agent passes legacy_tier to the gate meanwhile.
- Left in minimal form for WP6: coordinator._queue_action (fills the card columns) and resolve_pending keep the
  old grant flow plus the gate re-check. Missing: approvals.py (queue_action, expire_due), caller checks, the 409
  claim, card rule_text, PendingActionOut and API shapes.
- Left for WP7: schedules/automations already pass approval_mode and origin_id and the trigger-limit table is
  enforced in decide; the heartbeat's tier floor (_can_run / autonomy.heartbeat_allows) is still there; inbound
  messages are not owner-verified yet.
- Left for WP10: reviewer.py is an interface with available() False, so `auto` behaves as `ask`.
- Left for WP8: no connected-folder fence or cards, no app grants or screen floors, no access.bound;
  DEVICE_ALLOWED_ROOTS in device/files.py still bounds file tools. Add access.bound and the folder/app cards
  inside floors.check.
- agent/autonomy.py is still imported by coordinator (normalise_tier, grant, grant_target), heartbeat and
  api/intelligence.py; WP6/WP7/WP11 remove those uses, and WP13 deletes the module.

## After WP6-WP7 (committed). Suite: 750 passed, 35 skipped.
New: app/permissions/approvals.py (queue_action, resolve, expire_due, path_key), tests test_perm_approvals,
test_perm_inbound, test_perm_unattended. coordinator.resolve_pending is a thin wrapper over approvals.resolve.
- Answering: one conditional UPDATE claims the row (second answer = 409); gate re-check before anything runs; the
  approved tool runs inside the request; the hook app.agent.taskloop.on_pending_resolved is called only if it
  exists and only for rows with a work_item_id; the response carries `work_item`. PendingAction.result is written
  by the loop's hook, never by approvals.
- A paired device or a `local-open` token may answer once/deny only (403 on window, always, always-user and
  special cards). api/assistant.py `_caller()` already reads amr == "local-open".
- For WP9: /api/auth/local does not issue `amr` yet; presence and peer checks are NOT yet applied to the resolve
  route, the schedule/automation routes, or the channel owner route (PUT /api/channels/{kind}/owner).
- For WP8: the folder row key is approvals.path_key (sha256 of match.fold(realpath)); reuse it in access.py. The
  floors must return a folder_access / app_grant / delete_permission card as an ask, and decide_approved must
  pass once the folder or grant exists. For special cards, approvals applies the folder/app change first, then
  runs decide_approved, and undoes the change if that refuses (logged).
- queue_action(db, user, ctx, call, decision, tool_call_id) accepts tool_call_id but cannot store it (no column
  until the task-loop revision). The card's rule draft is in card_payload.rule_draft (hidden from the API).
- maybe_learn now runs only for trigger == "chat". usage.check gained background=; the WP10 reviewer should pass
  background=True for schedule, automation and inbound origins.
- events.py redactor no longer masks UUIDs.
- Channels: inbound runs only for the confirmed owner (owner_sender or bridge_owner_only); everything else is an
  info notification.
- agent/autonomy.py is still imported by coordinator (normalise_tier), interim.py and api/intelligence.py
  (grants endpoints, manual heartbeat call). iter_agent still passes autonomy_level / legacy_tier until WP11.

## After WP8-WP9 (committed). Suite: 798 passed, 35 skipped.
New: app/permissions/access.py, app/permissions/changes.py, app/device/windows.py; tests test_perm_screen,
test_perm_presence, test_perm_access, test_perm_devapi.
- Folders are read fresh from the database on every decide and tool run; env roots are only a ceiling. Until pm02
  runs, a user's folders are DERIVED at read time from the env roots (source=legacy, can_delete=true, ids
  `legacy:<key>`), like interim.py; the first PATCH/DELETE on one turns it into a real row. WP11 must write the
  same rows with the same access.path_key and set permissions_migrated.
- deps.require_presence / PresentUser / PresentOwner guard: the console, device folder/app/blocklist/screen
  routes, plugin install/uninstall, PATCH /skills, schedule and automation POST/PATCH, channel owner, local
  provider and remote PUT, pairing. Approval answers get the spawned-process and cool-down guard only.
- changes.record(...) writes the event and notifies the user (urgent when a change widens access). WP11: put
  PresentUser on the mode, rules and boundaries routes (they do not exist yet) and call changes.record; repeat
  the remote_cannot_change_mode / local_open_cannot_change_mode tests on the new routes.
- Local sign-in issues amr local-key or local-open; a token without the claim counts as local-open on a local
  install. The generated sign-in key is in <aura_home>/local-login.key (0600), never logged.
- A path outside the env ceiling is denied (FLOOR_OUTSIDE_FOLDERS), not offered as a card. Delete in an
  unconnected folder raises one delete_permission card with connect: true.
- Local-mode tests that change permissions need a keyed sign-in (see the test_device `auth` fixture).
- DEVICE.md still describes DEVICE_ALLOWED_ROOTS (docs package).

## After WP10-WP11 (committed). Suite: 928 passed, 37 skipped. EXPECTED_HEAD = "pm02_permissions_data".
New: alembic/versions/pm02_permissions_data.py (exposes map_legacy), app/permissions/legacy.py
(emit_pending_events), app/api/permissions.py, tests legacy_oracle, test_perm_reviewer, test_perm_legacy,
test_perm_api. Deleted: app/permissions/interim.py. Nothing derives folders or modes at read time any more.
- The gate reads user_settings.permission_mode and stored rules only. The loop no longer passes a 1.0 level.
  Tests that need a 1.0 level use apply_legacy(db, user, tier) from tests/perm_helpers.py.
- Reviewer: permissions/reviewer.py; `auto` behaves as `ask` when unavailable (mock provider). The fall-back
  counters (3 consecutive / 20 per run) are kept by the LOOP per work item and passed in the gate context:
  see spec 10 section 7 and spec 20 section 1.
- Rule PATCH adds a new row and revokes the old one.
- pm02 cannot sign events; it leaves extra["legacy_events_pending"] and the app emits legacy_mapped lazily.
- app/agent/autonomy.py is still on disk but nothing imports it; config.default_autonomy_level and comments
  mentioning autonomy remain for the cleanup package. The 1.0 columns remain for the contract revision.
