# Frontend contract notes for the permissions backend (lead, 12 Nov)

The web client for the permissions API is already written and committed:
frontend/lib/api.ts, frontend/lib/permissions.ts, frontend/components/ApprovalCard.tsx,
ModePicker.tsx, DeviceAccess.tsx, PresencePing.tsx, app/(app)/settings/permissions/page.tsx.
It was built from 10-permissions.md section 11. Where section 11 gives no shape, the client's
TypeScript types are now the concrete contract: READ THEM and return exactly those shapes
(field names and nesting). If a client type contradicts the spec's data model, follow the spec
and list the difference in your report so the client can be fixed.

Gaps the client had to guess; the backend must settle them as follows:
1. Rules: add `PATCH /api/permissions/rules/{id}` (presence required) accepting `{effect?, pattern?, note?, minutes?}`.
   The client currently changes a rule by POST new + DELETE old; both ways must work.
2. `GET /api/device/apps` returns `{grants: [...], blocks: [...], running: [{app_id, display_name}]}`;
   `GET /api/device/folders` returns a list of folder objects, each with `id, path, can_delete,
   within_install_limit (bool), created_at`.
3. Pending actions (list, detail and the decision response) include `card`, `card_payload`,
   `always_user`, `options`, `expires_at`, `origin`, `reason`, `work_item_id`, and inside
   `card_payload` a human-readable `rule_text` saying exactly what "always" would allow
   (for example: "Always allow writing files in D:\Work\Finance"), plus `path`, `app_id`,
   `display_name`, `tier` where the card kind uses them.
4. `RuleOut`, `EventOut` and catalogue entries: use the client's types; install-level entries
   carry `source: "install"` and `id: null`.
5. `GET /api/device` adds `presence_accepted (bool)` and `screen_control_user (bool)`; add
   `PUT /api/device/screen-control` (presence required) `{enabled}` so the user can switch mouse and
   keyboard use off without editing the env file (the env setting remains the install-level ceiling).
6. Pending actions carry `conversation_id` so the sidebar can mark the conversation that is waiting.
