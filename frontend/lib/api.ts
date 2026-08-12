/**
 * Typed API client. One place that knows the backend contract.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * True when NEXT_PUBLIC_API_URL wasn't set and we fell back to localhost.
 *
 * Worth distinguishing: on a deployed site this means the browser is trying to
 * reach the *visitor's own* machine, which no amount of backend debugging will
 * fix. The variable is inlined at build time, so it's also the one class of
 * misconfiguration that needs a redeploy rather than a restart.
 */
export const API_BASE_IS_FALLBACK = !process.env.NEXT_PUBLIC_API_URL;

const TOKEN_KEY = "aura_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers || {}),
    },
  });

  if (res.status === 401) {
    clearToken();
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
    throw new ApiError(401, "Session expired");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const get = <T,>(p: string) => request<T>(p);
const post = <T,>(p: string, body?: unknown) =>
  request<T>(p, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const patch = <T,>(p: string, body?: unknown) =>
  request<T>(p, { method: "PATCH", body: body === undefined ? undefined : JSON.stringify(body) });
const del = <T,>(p: string) => request<T>(p, { method: "DELETE" });

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface User {
  id: string;
  email: string;
  name: string;
  avatar_url: string;
  timezone: string;
  is_demo: boolean;
}

export interface Message {
  id: string;
  role: string;
  content: string;
  meta: Record<string, any>;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  pinned: boolean;
  created_at: string;
  updated_at: string;
  messages?: Message[];
}

export interface Task {
  id: string;
  parent_id: string | null;
  title: string;
  notes: string;
  status: "todo" | "doing" | "done";
  priority: "low" | "medium" | "high" | "urgent";
  due_at: string | null;
  completed_at: string | null;
  recurrence: string;
  tags: string[];
  source: string;
  ai_score: number;
  created_at: string;
}

export interface Email {
  id: string;
  external_id: string;
  thread_id: string;
  sender: string;
  sender_name: string;
  subject: string;
  snippet: string;
  body: string;
  received_at: string;
  is_read: boolean;
  is_archived: boolean;
  labels: string[];
  category: string;
  importance: "urgent" | "high" | "normal" | "low";
  ai_summary: string;
  action_items: string[];
  needs_reply: boolean;
}

export interface CalendarEvent {
  id: string;
  external_id: string;
  title: string;
  description: string;
  location: string;
  start_at: string;
  end_at: string;
  attendees: string[];
  status: string;
  source: string;
}

export interface InboxSummary {
  total: number;
  unread: number;
  urgent: number;
  needs_reply: number;
  summary: string;
  highlights: Array<{
    id: string;
    sender: string;
    subject: string;
    importance: string;
    summary: string;
    needs_reply: boolean;
  }>;
}

export interface Briefing {
  generated_at: string;
  greeting: string;
  headline: string;
  meetings: CalendarEvent[];
  tasks_due: Task[];
  urgent_emails: Email[];
  suggested_priorities: string[];
  inbox: InboxSummary | null;
}

export interface MemoryItem {
  id: string;
  kind: string;
  content: string;
  source: string;
  confidence: number;
  use_count: number;
  pinned: boolean;
  created_at: string;
}

export interface Automation {
  id: string;
  name: string;
  natural_language: string;
  trigger_type: string;
  trigger_config: Record<string, any>;
  actions: Array<{ tool: string; arguments: Record<string, any> }>;
  enabled: boolean;
  requires_confirmation: boolean;
  run_count: number;
  last_run_at: string | null;
  created_at: string;
}

export interface PendingAction {
  id: string;
  tool_name: string;
  skill_code: string;
  arguments: Record<string, any>;
  preview: string;
  status: string;
  created_at: string;
}

export interface Activity {
  id: string;
  actor: string;
  action: string;
  target: string;
  status: string;
  detail: Record<string, any>;
  created_at: string;
}

export interface NotificationItem {
  id: string;
  title: string;
  body: string;
  level: "info" | "success" | "warning" | "urgent";
  read: boolean;
  link: string;
  created_at: string;
}

export interface Capability {
  key: string;
  label: string;
  granted: boolean;
}

export interface Integration {
  provider: string;
  connected: boolean;
  available: boolean;
  email: string;
  scopes: string[];
  capabilities: Capability[];
  needs_reconnect: boolean;
  last_sync_at: string | null;
  last_sync_emails: number;
  last_sync_events: number;
  last_sync_error: string;
  connected_at: string | null;
}

export interface SyncResult {
  connected: boolean;
  emails: number;
  events: number;
  errors: string[];
  message: string;
}

export type AutonomyTier = "strict" | "conservative" | "relaxed" | "full";

export interface AppSettings {
  briefing_time: string;
  briefing_enabled: boolean;
  autonomy_level: AutonomyTier;
  tone: string;
  theme: "warm" | "dark" | "light";
  heartbeat_enabled: boolean;
  heartbeat_interval_minutes: number;
  quiet_hours: string;
}

export type Colourway = "teal" | "amber" | "rose" | "violet" | "sage";
export type Stage = "stranger" | "acquaintance" | "colleague" | "chief_of_staff";

export interface Assistant {
  id: string;
  name: string;
  personality: "concise" | "warm" | "dry" | "formal" | "encouraging";
  avatar: Colourway;
  pronoun: string;
  goals: string[];
  onboarded: boolean;
  hatched_at: string;
  stage: Stage;
  stage_label: string;
  stage_blurb: string;
  signals: { interactions: number; memories: number; actions: number };
  progress: {
    next: Stage;
    next_label: string;
    percent: number;
    needs: { interactions: number; memories: number; actions: number };
  } | null;
  days_together: number;
}

export interface SkillItem {
  code: string;
  name: string;
  category: string;
  description: string;
  tools: string[];
  proactive: boolean;
  autonomy_floor: string;
  enabled: boolean;
  run_count: number;
  success_count: number;
  last_run_at: string | null;
  learned_notes: string[];
}

export interface SkillRun {
  id: string;
  code: string;
  trigger: string;
  summary: string;
  status: string;
  duration_ms: number;
  created_at: string;
}

export interface SkillStats {
  total: number;
  enabled: number;
  total_runs: number;
  most_used_code: string;
  most_used_name: string;
}

export interface Heartbeat {
  id: string;
  headline: string;
  lines: string[];
  skills_run: string[];
  needs_attention: number;
  acknowledged: boolean;
  created_at: string;
}

export interface Grant {
  id: string;
  tool_name: string;
  scope: string;
  expires_at: string | null;
  created_at: string;
}

export interface Compaction {
  before: number;
  after: number;
  merged: number;
  promoted: number;
  dropped: number;
}

export type Decision = "reject" | "once" | "always" | "window";

export interface Spend {
  spent_usd: number;
  cap_usd: number;
  remaining_usd: number;
  percent: number;
  calls: number;
  tokens: number;
  cap_enabled: boolean;
  provider: string;
  model: string;
}

export interface UsageReport {
  today: Spend;
  daily: Array<{ date: string; cost_usd: number; tokens: number; calls: number }>;
  by_trigger: Array<{ trigger: string; cost_usd: number; calls: number }>;
}

export interface PreflightCheck {
  key: string;
  label: string;
  level: "ok" | "warn" | "fail";
  detail: string;
  fix: string;
  docs: string;
}

export interface Preflight {
  environment: string;
  ready: boolean;
  failures: number;
  warnings: number;
  checks: PreflightCheck[];
}

export interface PluginItem {
  id: string;
  name: string;
  category: string;
  summary: string;
  detail: string;
  skills: string[];
  skill_names: string[];
  core: boolean;
  available: boolean;
  unavailable_reason: string;
  accent: Colourway;
  installed: boolean;
}

export interface PluginSummary {
  installed: number;
  available: number;
  total: number;
}

export interface ChannelItem {
  kind: string;
  name: string;
  blurb: string;
  setup: string;
  inbound: boolean;
  available: boolean;
  unavailable_reason: string;
  connected: boolean;
  verified: boolean;
  identifier: string;
  message_count: number;
  last_seen_at: string | null;
  token?: string | null;
}

export interface Secret {
  key: string;
  label: string;
  kind: string;
  hint: string;
  use_count: number;
  last_used_at: string | null;
  created_at: string;
}

export interface DocumentItem {
  id: string;
  title: string;
  mime_type: string;
  size_bytes: number;
  summary: string;
  source: string;
  created_at: string;
}

export interface DocumentDetail extends DocumentItem {
  content: string;
  chunk_count: number;
}

export interface DocumentPassage {
  document_id: string;
  title: string;
  ordinal: number;
  excerpt: string;
  score: number;
}

export interface ScheduleItem {
  id: string;
  name: string;
  prompt: string;
  natural_language: string;
  cron: string;
  cron_label: string;
  timezone: string;
  enabled: boolean;
  deliver_to: string;
  last_run_at: string | null;
  last_result: string;
  run_count: number;
  created_at: string;
}

export interface DelegationItem {
  id: string;
  assignee_name: string;
  assignee_email: string;
  title: string;
  context: string;
  due_at: string | null;
  status: string;
  chased_at: string | null;
  created_at: string;
}

export interface FreeSlot {
  start_at: string;
  end_at: string;
}

export interface Conflict {
  event_a: CalendarEvent;
  event_b: CalendarEvent;
  overlap_minutes: number;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export const api = {
  authConfig: () =>
    get<{ google_enabled: boolean; demo_login_enabled: boolean; llm_provider: string }>(
      "/api/auth/config"
    ),
  demoLogin: (email: string, name: string) =>
    post<{ access_token: string; user: User }>("/api/auth/demo", { email, name }),
  // Sends the bearer token when signed in, which is what tells the backend to
  // *connect* Google to this account rather than sign in as the Google identity.
  googleStart: () =>
    get<{ authorization_url: string; state: string; mode: "signin" | "connect" }>(
      "/api/auth/google/start"
    ),
  syncGoogle: () => post<SyncResult>("/api/auth/integrations/google/sync"),
  me: () => get<User>("/api/auth/me"),
  integrations: () => get<Integration[]>("/api/auth/integrations"),
  disconnectGoogle: (revoke = true) =>
    del<{ message: string; revoked: boolean }>(
      `/api/auth/integrations/google?revoke=${revoke}`
    ),

  briefing: () => get<Briefing>("/api/briefing"),
  activity: () => get<Activity[]>("/api/activity"),

  conversations: () => get<Conversation[]>("/api/chat/conversations"),
  conversation: (id: string) => get<Conversation>(`/api/chat/conversations/${id}`),
  newConversation: () => post<Conversation>("/api/chat/conversations"),
  pinConversation: (id: string) => patch<Conversation>(`/api/chat/conversations/${id}/pin`),
  deleteConversation: (id: string) => del<{ message: string }>(`/api/chat/conversations/${id}`),

  emails: (folder = "inbox", q = "") =>
    get<Email[]>(`/api/emails?folder=${folder}&q=${encodeURIComponent(q)}`),
  email: (id: string) => get<Email>(`/api/emails/${id}`),
  inboxSummary: () => get<InboxSummary>("/api/emails/summary"),
  archiveEmail: (id: string) => post<Email>(`/api/emails/${id}/archive`),
  draftReply: (email_id: string, instruction: string) =>
    post<{ subject: string; body: string }>("/api/emails/draft-reply", { email_id, instruction }),
  syncEmail: () => post<Record<string, any>>("/api/emails/sync"),

  events: (days = 14, dayOffset = 0) =>
    get<CalendarEvent[]>(`/api/events?days=${days}&day_offset=${dayOffset}`),
  createEvent: (body: Partial<CalendarEvent>) => post<CalendarEvent>("/api/events", body),
  deleteEvent: (id: string) => del<{ message: string }>(`/api/events/${id}`),
  freeSlots: (duration = 30, days = 5) =>
    get<FreeSlot[]>(`/api/events/free-slots?duration_minutes=${duration}&days=${days}`),
  conflicts: () => get<Conflict[]>("/api/events/conflicts"),
  meetingBrief: (id: string) => get<Record<string, any>>(`/api/events/${id}/brief`),

  tasks: (status = "all") => get<Task[]>(`/api/tasks?status=${status}`),
  createTask: (body: Partial<Task>) => post<Task>("/api/tasks", body),
  quickTask: (text: string) => post<Task>(`/api/tasks/quick?text=${encodeURIComponent(text)}`),
  updateTask: (id: string, body: Partial<Task>) => patch<Task>(`/api/tasks/${id}`, body),
  deleteTask: (id: string) => del<{ message: string }>(`/api/tasks/${id}`),

  memories: (kind = "") => get<MemoryItem[]>(`/api/memories${kind ? `?kind=${kind}` : ""}`),
  createMemory: (content: string, kind: string) =>
    post<MemoryItem>("/api/memories", { content, kind }),
  searchMemories: (q: string) => get<MemoryItem[]>(`/api/memories/search?q=${encodeURIComponent(q)}`),
  deleteMemory: (id: string) => del<{ message: string }>(`/api/memories/${id}`),
  pinMemory: (id: string) => patch<MemoryItem>(`/api/memories/${id}/pin`),

  automations: () => get<Automation[]>("/api/automations"),
  createAutomation: (natural_language: string) =>
    post<Automation>("/api/automations", { natural_language, enabled: true }),
  updateAutomation: (id: string, body: Partial<Automation>) =>
    patch<Automation>(`/api/automations/${id}`, body),
  deleteAutomation: (id: string) => del<{ message: string }>(`/api/automations/${id}`),
  runAutomation: (id: string) => post<Record<string, any>>(`/api/automations/${id}/run`),

  pendingActions: () => get<PendingAction[]>("/api/pending-actions"),
  decidePending: (id: string, decision: Decision, minutes = 10) =>
    post<Record<string, any>>(`/api/pending-actions/${id}`, { decision, minutes }),

  settings: () => get<AppSettings>("/api/settings"),
  updateSettings: (body: Partial<AppSettings>) => patch<AppSettings>("/api/settings", body),

  // --- Skills -------------------------------------------------------------
  skills: (category = "") =>
    get<SkillItem[]>(`/api/skills${category ? `?category=${encodeURIComponent(category)}` : ""}`),
  skillCategories: () => get<string[]>("/api/skills/categories"),
  skillStats: () => get<SkillStats>("/api/skills/stats"),
  skillActivity: (limit = 40) => get<SkillRun[]>(`/api/skills/activity?limit=${limit}`),
  toggleSkill: (code: string, enabled: boolean) =>
    patch<SkillItem>(`/api/skills/${code}`, { enabled }),
  teachSkill: (code: string, note: string) =>
    post<SkillItem>(`/api/skills/${code}/teach`, { note }),
  clearSkillNotes: (code: string) => del<{ message: string }>(`/api/skills/${code}/notes`),

  // --- Identity -----------------------------------------------------------
  assistant: () => get<Assistant>("/api/assistant"),
  updateAssistant: (body: Partial<Assistant>) => patch<Assistant>("/api/assistant", body),
  hatch: (body: {
    name: string;
    personality: string;
    avatar: string;
    pronoun: string;
    goals: string[];
    role: string;
    about: string;
    autonomy_level: AutonomyTier;
  }) => post<Assistant>("/api/assistant/hatch", body),

  // --- Heartbeat ----------------------------------------------------------
  heartbeats: (limit = 10) => get<Heartbeat[]>(`/api/heartbeat?limit=${limit}`),
  latestHeartbeat: () => get<Heartbeat | null>("/api/heartbeat/latest"),
  runHeartbeat: () => post<Heartbeat>("/api/heartbeat/run"),
  ackHeartbeat: (id: string) => post<Heartbeat>(`/api/heartbeat/${id}/ack`),

  // --- Trust + memory maintenance -----------------------------------------
  grants: () => get<Grant[]>("/api/grants"),
  revokeGrant: (tool: string) => del<{ revoked: number }>(`/api/grants/${tool}`),
  compactMemory: (dryRun = false) =>
    post<Compaction>(`/api/memories/compact?dry_run=${dryRun}`),

  // --- Plugins ------------------------------------------------------------
  plugins: (category = "") =>
    get<PluginItem[]>(
      `/api/plugins${category ? `?category=${encodeURIComponent(category)}` : ""}`
    ),
  pluginCategories: () => get<string[]>("/api/plugins/categories"),
  pluginSummary: () => get<PluginSummary>("/api/plugins/summary"),
  installPlugin: (id: string) => post<PluginItem>(`/api/plugins/${id}/install`),
  uninstallPlugin: (id: string) => post<PluginItem>(`/api/plugins/${id}/uninstall`),

  // --- Channels -----------------------------------------------------------
  channels: () => get<ChannelItem[]>("/api/channels"),
  connectChannel: (kind: string, identifier = "") =>
    post<ChannelItem>("/api/channels/connect", { kind, identifier }),
  rotateChannelToken: (kind: string) => post<ChannelItem>(`/api/channels/${kind}/rotate`),
  disconnectChannel: (kind: string) =>
    post<{ message: string }>(`/api/channels/${kind}/disconnect`),

  // --- Vault --------------------------------------------------------------
  secrets: () => get<Secret[]>("/api/vault"),
  putSecret: (body: { key: string; value: string; label?: string; kind?: string }) =>
    request<Secret>("/api/vault", { method: "PUT", body: JSON.stringify(body) }),
  deleteSecret: (key: string) => del<{ message: string }>(`/api/vault/${key}`),

  // --- Documents ----------------------------------------------------------
  documents: () => get<DocumentItem[]>("/api/documents"),
  document: (id: string) => get<DocumentDetail>(`/api/documents/${id}`),
  searchDocuments: (q: string) =>
    get<DocumentPassage[]>(`/api/documents/search?q=${encodeURIComponent(q)}`),
  addDocumentText: (title: string, content: string) =>
    post<DocumentItem>("/api/documents/text", { title, content }),
  summarizeDocument: (id: string) => post<DocumentItem>(`/api/documents/${id}/summarize`),
  deleteDocument: (id: string) => del<{ message: string }>(`/api/documents/${id}`),
  uploadDocument: async (file: File): Promise<DocumentItem> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${API_BASE}/api/documents/upload`, {
      method: "POST",
      headers: { Authorization: `Bearer ${getToken() ?? ""}` },
      body: form,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail ?? detail;
      } catch {
        /* non-JSON error body */
      }
      throw new ApiError(res.status, detail);
    }
    return (await res.json()) as DocumentItem;
  },

  // --- Schedules ----------------------------------------------------------
  schedules: () => get<ScheduleItem[]>("/api/schedules"),
  createSchedule: (body: {
    prompt: string;
    natural_language?: string;
    name?: string;
    cron?: string;
    deliver_to?: string;
  }) => post<ScheduleItem>("/api/schedules", body),
  updateSchedule: (id: string, body: Partial<ScheduleItem>) =>
    patch<ScheduleItem>(`/api/schedules/${id}`, body),
  runSchedule: (id: string) => post<Record<string, any>>(`/api/schedules/${id}/run`),
  deleteSchedule: (id: string) => del<{ message: string }>(`/api/schedules/${id}`),

  // --- Delegations --------------------------------------------------------
  delegations: (status = "all") => get<DelegationItem[]>(`/api/delegations?status=${status}`),
  createDelegation: (body: {
    assignee: string;
    title: string;
    context?: string;
    due_at?: string | null;
  }) => post<DelegationItem>("/api/delegations", body),
  updateDelegation: (id: string, status: string) =>
    patch<DelegationItem>(`/api/delegations/${id}?status=${status}`),
  deleteDelegation: (id: string) => del<{ message: string }>(`/api/delegations/${id}`),

  // --- Notifications ------------------------------------------------------
  notifications: () => get<NotificationItem[]>("/api/notifications"),
  markNotificationsRead: () => post<{ marked: number }>("/api/notifications/read-all"),

  // --- Spend, usage, account rights ---------------------------------------
  spend: () => get<Spend>("/api/account/spend"),
  usage: (days = 14) => get<UsageReport>(`/api/account/usage?days=${days}`),
  deleteAccount: (confirm_email: string) =>
    post<{ deleted: boolean; message: string }>("/api/account/delete", {
      confirm_email,
      understand: true,
    }),
  exportUrl: () => `${API_BASE}/api/account/export`,
  downloadExport: async () => {
    const res = await fetch(`${API_BASE}/api/account/export`, {
      headers: { Authorization: `Bearer ${getToken() ?? ""}` },
    });
    if (!res.ok) throw new ApiError(res.status, "Export failed");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "aura-export.json";
    link.click();
    URL.revokeObjectURL(url);
  },

  // --- Diagnostics (unauthenticated) --------------------------------------
  preflight: async (): Promise<Preflight> => {
    // Deliberately not using request(): this must work when nothing else does,
    // including before sign-in and when the config is broken.
    const res = await fetch(`${API_BASE}/api/health/preflight`);
    return (await res.json()) as Preflight;
  },
};

// ---------------------------------------------------------------------------
// Chat streaming (SSE over POST)
// ---------------------------------------------------------------------------

export type StreamEvent =
  | { type: "start"; conversation_id: string }
  | { type: "status"; value: string }
  | { type: "skill"; code: string; name: string; summary: string; ok: boolean }
  | { type: "learned"; value: string }
  | { type: "memory"; count: number }
  | {
      type: "pending_action";
      id: string;
      tool_name: string;
      skill_code: string;
      preview: string;
    }
  | { type: "delta"; value: string }
  | { type: "done"; conversation_id: string; message_id: string; latency_ms: number; steps: number }
  | { type: "error"; message: string };

export async function streamChat(
  message: string,
  conversationId: string | null,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${getToken() ?? ""}`,
    },
    body: JSON.stringify({ message, conversation_id: conversationId }),
    signal,
  });

  if (!res.ok || !res.body) {
    throw new ApiError(res.status, "Chat stream failed");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const line = chunk.trim();
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (payload === "[DONE]") return;
      try {
        onEvent(JSON.parse(payload) as StreamEvent);
      } catch {
        /* ignore malformed frame */
      }
    }
  }
}
