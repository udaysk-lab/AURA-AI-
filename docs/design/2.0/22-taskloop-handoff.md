# Task loop and memory: handoff notes between packages (newest last)

## After WP1-WP2 (committed). Suite: 1076 passed, 40 skipped. EXPECTED_HEAD = "tl02_preferences".
New: revisions tl01_work_items, tl02_preferences; app/services/{tasktypes,redaction,preferences,workmemory}.py;
models WorkItem, WorkStep, Preference, the work_item_id links, memory_paused, memory_off, PendingAction
tool_call_id and result; tests test_tasktypes, test_redaction, test_preferences, test_recall, test_work_items,
test_memory_controls, work_helpers. Nothing about the chat turn changed yet; maybe_learn and skills.teach are
still in place for WP3 to replace.
- workmemory.py: create_item, set_plan, update_plan_step, add_step, finish_step, add_check, note_tool, finalise,
  is_lookup. build_recall returns a Recall with .block, .counts(), .used(); record_recall stores it;
  resolve_recall gives the lists the API shows. finalise takes status and final report text from the loop; the
  loop decides the outcome table (spec 5.5) and the report footer.
- preferences.py: capture and note_from_tool return Captured objects (.event() gives the preference_noted event
  body); preference_block, select_rows / format_block / mark_used serve the prompt. PREFERENCE_EXTRACTOR lives in
  preferences.py for now: WP3 moves it to prompts.py (fill it with .replace, its JSON braces break .format).
- Recall scoring: type, status and feedback adjustments apply only when text similarity is above 0.
- services/memory.py: KINDS narrowed; remember() files legacy kinds (preference, style, habit) as "fact" until the
  coordinator and save_memory stop writing them (WP3); compaction never touches manual rows or the new stores;
  edit() exists; public tokens / cosine / lexical.
- pending_actions.work_item_id is now a real foreign key: tests that park an approval must create the WorkItem.
- API-facing helpers ready for WP6: preferences.teach / skill_notes / clear_skill_notes / reset;
  workmemory.edit / set_feedback / delete_item (raises ItemBusy) / reset; memory.edit.
- WorkStepOut returns both the spec's fields (summary, detail) and the client's (result_summary, started_at,
  finished_at, pending_action_id).
- To fix in a later package: services/demo.py still seeds Memory rows of kind preference/style/habit (seed
  Preference rows instead); briefing.draft_reply builds its style context from memory search and no longer
  finds style memories (use the preference service for the email task type).
- From the permission system (finished): approvals.queue_action(db, user, ctx, call, decision, tool_call_id) and
  approvals.expire_due exist; approvals.resolve calls app.agent.taskloop.on_pending_resolved (only if it exists,
  only for rows with a work_item_id) and the hook writes PendingAction.result; the reviewer's fall-back counters
  (3 consecutive / 20 per run) are kept by the loop per work item and passed in the gate context; maybe_learn
  runs only for trigger == "chat"; usage.check has background=.

## After WP3-WP4 (committed). Suite: 1126 passed, 40 skipped. No new revision.
New: app/agent/taskloop.py (loop, bookkeeping tools, outcome table, resume, stop, expiry, on_pending_resolved),
app/agent/verifiers.py, tests scripted.py, test_taskloop, test_taskloop_resume, test_taskloop_memory.
api/chat.py has continue, stop and the 409 answers. skills.teach and learned_notes_block are deleted; the teach
and notes endpoints in api/intelligence.py call preferences.teach / skill_notes / clear_skill_notes (minimal
rewire; WP6 owns their final form). PREFERENCE_EXTRACTOR is in prompts.py. demo.py seeds Preference rows;
briefing.draft_reply takes its style from the preference service.
- LoopState.dump deep-copies through JSON (SQLAlchemy does not see in-place edits of nested dicts): keep that.
- The `recall` event carries its lists under `recalled: {past_work, preferences}` (preferences is the count key).
- A reviewer block on a write tool counts as a bad write in the outcome table.
- If the client disconnects or the loop raises, the item is parked ready / interrupted; resume marks open calls
  interrupted. AgentResult lives in taskloop.py (coordinator re-exports it).
- NOT written yet, for WP5: on_finished (schedule / inbound notifications), resume_due.
  claim_for_resume(background=True) and iter_resume(background=True) give the "resume" budget trigger;
  recover_stale is ready. schedules.run_one and channels.handle_inbound still use run_agent and work unchanged;
  a schedule item that parks is only continued once resume_due exists.
- NOT done yet, for WP6: ChatIn.memory_off (see 21-frontend-contract.md item 5); the final form of the work
  item, preference and memory routes.
- For WP7 (CLI): backend/aura_cli.py still reads the removed events (pending_action, skill, learned), so CLI
  approvals are not prompted until it is rewritten for the section 8 events.
- Lead decision: quick-answer turns that capture a preference get no "Noted:" footer; the preference_noted event
  and message meta carry it and the web app shows a chip.
