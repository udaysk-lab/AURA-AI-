# Frontend contract notes for the task-loop and memory backend (lead, 12 Nov)

The web client for the loop and memory API is already written and committed:
frontend/lib/api.ts (work items, steps, preferences, memory settings, StreamEvent union),
frontend/lib/taskrun.ts, components/TaskPanel.tsx, app/(app)/chat/page.tsx, app/(app)/memory/page.tsx.
It was built from 20-taskloop-memory.md sections 8 and 13. Where the spec gives no shape, the
client's TypeScript types are the concrete contract: READ THEM and return exactly those shapes.
If a client type contradicts the spec's data model, follow the spec and list the difference in
your report so the client can be fixed.

Gaps the client had to guess; settle them as follows:
1. WorkStepOut: return the work_steps columns as the client types them (id, seq, kind, tool_name,
   arguments (redacted), result_summary, status, started_at, finished_at, pending_action_id).
2. WorkItemDetail gains `recall_used`: `{past_work: [{id, title, outcome, finished_at}],
   preferences: [{id, task_type, text}]}` so the panel can show what was recalled, also after a
   conversation is reopened. The `recall` stream event keeps its counts and adds the same lists.
3. `verification` is an object `{status, note}` (never a bare string).
4. Message meta `preferences_noted` is a list of `{id, text}`.
5. ChatIn accepts `memory_off: bool` so the very first message of a new chat can opt out of memory;
   it sets the conversation's flag before the run starts.
6. Pending actions carry `conversation_id` (also settled in 11-frontend-contract.md).
7. A 409 from /api/chat/stream and /api/chat/continue returns `{code, work_item_id, status}`.
8. Files produced by a task are not tracked in 2.0 (no Files section); do not add it.
