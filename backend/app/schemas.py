"""Pydantic request/response models. These define the public API contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Auth / user
# ---------------------------------------------------------------------------


class UserOut(ORMModel):
    id: str
    email: str
    name: str
    avatar_url: str
    timezone: str
    is_demo: bool


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class DemoLoginIn(BaseModel):
    email: EmailStr = "demo@aura.ai"
    name: str = "Demo User"
    # The browser's IANA zone, e.g. "Asia/Kolkata". Optional; unknown -> UTC.
    timezone: str = Field(default="", max_length=64)


class RegisterIn(BaseModel):
    email: EmailStr
    # 72 is bcrypt's hard ceiling — see security.MAX_PASSWORD_BYTES. Enforced
    # here too so the error is a 422 about the field rather than a 500.
    password: str = Field(min_length=8, max_length=72)
    name: str = Field(default="", max_length=200)
    timezone: str = Field(default="", max_length=64)


class ProfilePatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    timezone: str | None = Field(default=None, max_length=64)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class LocalLoginIn(BaseModel):
    # The desktop app's sign-in key, from the link its window opened.
    key: str = Field(default="", max_length=200)


class PasswordForgotIn(BaseModel):
    email: EmailStr


class PasswordResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=2000)
    password: str = Field(min_length=8, max_length=72)


class SettingsOut(ORMModel):
    briefing_time: str
    briefing_enabled: bool
    theme: str
    heartbeat_enabled: bool
    heartbeat_interval_minutes: int
    quiet_hours: str


class SettingsIn(BaseModel):
    briefing_time: str | None = None
    briefing_enabled: bool | None = None
    theme: Literal["warm", "dark", "light"] | None = None
    heartbeat_enabled: bool | None = None
    heartbeat_interval_minutes: int | None = Field(default=None, ge=5, le=720)
    quiet_hours: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _no_trust_level(cls, data):
        """The 1.0 per-user setting is gone: the permission mode is set only on /api/permissions/mode."""
        if isinstance(data, dict) and "autonomy_level" in data:
            raise ValueError("autonomy_level was replaced by the permission mode: use /api/permissions/mode")
        return data


class Capability(BaseModel):
    key: str
    label: str
    granted: bool


class IntegrationStatus(BaseModel):
    provider: str
    connected: bool
    email: str = ""
    scopes: list[str] = Field(default_factory=list)
    available: bool = False
    capabilities: list[Capability] = Field(default_factory=list)
    needs_reconnect: bool = False
    last_sync_at: datetime | None = None
    last_sync_emails: int = 0
    last_sync_events: int = 0
    last_sync_error: str = ""
    connected_at: datetime | None = None


class SyncResultOut(BaseModel):
    connected: bool
    emails: int = 0
    events: int = 0
    errors: list[str] = Field(default_factory=list)
    message: str = ""


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class ConversationOut(ORMModel):
    id: str
    title: str
    pinned: bool
    memory_off: bool = False
    created_at: datetime
    updated_at: datetime


class MessageOut(ORMModel):
    id: str
    role: str
    content: str
    meta: dict[str, Any]
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut] = Field(default_factory=list)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    conversation_id: str | None = None
    # "Don't use memory in this chat", chosen before the first message is sent. Sets the
    # conversation's flag before the run starts; omitted leaves the flag as it is.
    memory_off: bool | None = None


class ChatContinueIn(BaseModel):
    work_item_id: str


class ChatOut(BaseModel):
    conversation_id: str
    message: MessageOut
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    skill_runs: list[dict[str, Any]] = Field(default_factory=list)
    memories_used: list[str] = Field(default_factory=list)
    pending_actions: list[PendingActionOut] = Field(default_factory=list)
    work_item_id: str | None = None
    work_status: str = ""
    preferences_noted: list[dict[str, str]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


class TaskOut(ORMModel):
    id: str
    parent_id: str | None
    title: str
    notes: str
    status: str
    priority: str
    due_at: datetime | None
    completed_at: datetime | None
    recurrence: str
    tags: list[Any]
    source: str
    ai_score: float
    created_at: datetime


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    notes: str = ""
    status: Literal["todo", "doing", "done"] = "todo"
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    due_at: datetime | None = None
    recurrence: Literal["", "daily", "weekly", "monthly"] = ""
    tags: list[str] = Field(default_factory=list)
    parent_id: str | None = None


class TaskPatch(BaseModel):
    title: str | None = None
    notes: str | None = None
    status: Literal["todo", "doing", "done"] | None = None
    priority: Literal["low", "medium", "high", "urgent"] | None = None
    due_at: datetime | None = None
    recurrence: Literal["", "daily", "weekly", "monthly"] | None = None
    tags: list[str] | None = None


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


class EmailOut(ORMModel):
    id: str
    external_id: str
    thread_id: str
    sender: str
    sender_name: str
    subject: str
    snippet: str
    body: str
    received_at: datetime
    is_read: bool
    is_archived: bool
    labels: list[Any]
    category: str
    importance: str
    ai_summary: str
    action_items: list[Any]
    needs_reply: bool


class InboxSummaryOut(BaseModel):
    total: int
    unread: int
    urgent: int
    needs_reply: int
    summary: str
    highlights: list[dict[str, Any]] = Field(default_factory=list)


class DraftReplyIn(BaseModel):
    email_id: str
    instruction: str = "Write a concise, professional reply."


class DraftReplyOut(BaseModel):
    subject: str
    body: str


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


class EventOut(ORMModel):
    id: str
    external_id: str
    title: str
    description: str
    location: str
    start_at: datetime
    end_at: datetime
    attendees: list[Any]
    status: str
    source: str


class EventIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = ""
    location: str = ""
    start_at: datetime
    end_at: datetime
    attendees: list[str] = Field(default_factory=list)


class FreeSlot(BaseModel):
    start_at: datetime
    end_at: datetime


class ConflictOut(BaseModel):
    event_a: EventOut
    event_b: EventOut
    overlap_minutes: int


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


class MemoryOut(ORMModel):
    id: str
    kind: str
    content: str
    source: str
    confidence: float
    use_count: int
    pinned: bool
    created_at: datetime


MEMORY_KINDS = ("fact", "contact", "project", "decision")
LEGACY_MEMORY_KINDS = ("preference", "style", "habit")


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    kind: Literal["fact", "contact", "project", "decision"] = "fact"
    pinned: bool = False

    @field_validator("kind", mode="before")
    @classmethod
    def _legacy_kind(cls, raw: Any) -> Any:
        # Older clients still send the 1.0 kinds; those lines are how-to preferences now and
        # this store holds facts, so they are filed as facts (as memory.remember does).
        return "fact" if raw in LEGACY_MEMORY_KINDS else raw


class MemoryPatch(BaseModel):
    """PATCH /api/memories/{id}: the owner's edit. A bad kind or empty content is a 422."""

    content: str | None = Field(default=None, min_length=1, max_length=2000)
    kind: Literal["fact", "contact", "project", "decision"] | None = None
    pinned: bool | None = None


# ---------------------------------------------------------------------------
# Task memory and preference memory (spec 20-taskloop-memory.md sections 11, 13;
# shapes follow the web client's types in frontend/lib/api.ts)
# ---------------------------------------------------------------------------


class WorkStepOut(ORMModel):
    """A work_steps row. `result_summary`, `started_at`, `finished_at` and `pending_action_id`
    are derived, for the contract note that lists them; `summary` and `detail` are what the
    client's WorkStepOut type reads."""

    id: str
    seq: int
    step_no: int = 0
    kind: str
    tool_name: str = ""
    tool_call_id: str = ""
    system: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: str
    summary: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    result_summary: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    pending_action_id: str | None = None
    created_at: datetime | None = None

    @model_validator(mode="after")
    def _derive(self) -> "WorkStepOut":
        self.result_summary = self.result_summary or self.summary
        self.started_at = self.started_at or self.created_at
        if self.pending_action_id is None:
            value = (self.detail or {}).get("pending_action_id")
            self.pending_action_id = str(value) if value else None
        return self


class VerificationOut(BaseModel):
    status: Literal["passed", "failed", "partial", "none"] = "none"
    checks: list[dict[str, Any]] = Field(default_factory=list)


class WorkItemOut(ORMModel):
    id: str
    title: str
    task_type: str
    status: str
    trigger: str
    conversation_id: str | None = None
    outcome: str = ""
    # Always an object {status, note?, checks}, never a bare string (contract note 3).
    verification: dict[str, Any] = Field(default_factory=lambda: {"status": "none", "checks": []})
    steps_total: int = 0
    cost_usd: float = 0.0
    recallable: bool = True
    created_at: datetime
    finished_at: datetime | None = None

    @field_validator("verification", mode="before")
    @classmethod
    def _verification_object(cls, raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict) and raw.get("status") in ("passed", "failed", "partial", "none"):
            return {"checks": [], **raw}
        return {"status": "none", "checks": []}


class RecallPastWorkOut(BaseModel):
    id: str
    title: str
    outcome: str = ""
    finished_at: datetime | None = None


class RecallPreferenceOut(BaseModel):
    id: str
    task_type: str
    text: str


class RecallUsedOut(BaseModel):
    """What the run recalled, kept so the panel can show it after a conversation is reopened."""

    past_work: list[RecallPastWorkOut] = Field(default_factory=list)
    preferences: list[RecallPreferenceOut] = Field(default_factory=list)


class WorkItemDetail(WorkItemOut):
    instruction: str = ""
    plan: list[dict[str, Any]] = Field(default_factory=list)
    systems_used: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    summary: str = ""
    report: str = ""
    user_feedback: dict[str, Any] = Field(default_factory=dict)
    pending_action_ids: list[str] = Field(default_factory=list)
    steps: list[WorkStepOut] = Field(default_factory=list)
    recall_used: RecallUsedOut = Field(default_factory=RecallUsedOut)

    @field_validator("recall_used", mode="before")
    @classmethod
    def _plain_recall(cls, raw: Any) -> Any:
        # The stored column holds ids ({"work_item_ids": ...}); the detail view carries the
        # resolved lists. A caller that has not resolved them yet gets empty lists, never ids.
        if isinstance(raw, dict) and ("past_work" in raw or "preferences" in raw):
            return raw
        return {}


class TaskTypeOut(BaseModel):
    value: str
    label: str


class PreferenceOut(ORMModel):
    id: str
    task_type: str
    system_scope: str = ""
    text: str
    source: str
    origin: str
    active: bool
    superseded_by: str | None = None
    use_count: int = 0
    last_used_at: datetime | None = None
    created_at: datetime


class MemorySettingsOut(BaseModel):
    paused: bool = False


class MemorySettingsIn(BaseModel):
    paused: bool


class ConversationMemoryIn(BaseModel):
    memory_off: bool


class MemoryResetIn(BaseModel):
    scope: Literal["preferences", "task_memory", "facts", "all"]
    # The caller must type the word: a reset cannot be undone.
    confirm: str = ""


class MemoryResetOut(BaseModel):
    deleted: dict[str, int]


class WorkItemPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    task_type: str | None = Field(default=None, max_length=40)
    summary: str | None = Field(default=None, max_length=1200)
    recallable: bool | None = None


class WorkItemFeedbackIn(BaseModel):
    rating: Literal["up", "down"]
    note: str = Field(default="", max_length=500)


class PreferenceIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    task_type: str = Field(default="general", max_length=40)
    system_scope: str = Field(default="", max_length=60)


class PreferencePatch(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=300)
    task_type: str | None = Field(default=None, max_length=40)
    system_scope: str | None = Field(default=None, max_length=60)
    active: bool | None = None


# ---------------------------------------------------------------------------
# Briefing / dashboard
# ---------------------------------------------------------------------------


class BriefingOut(BaseModel):
    generated_at: datetime
    greeting: str
    headline: str
    meetings: list[EventOut] = Field(default_factory=list)
    tasks_due: list[TaskOut] = Field(default_factory=list)
    urgent_emails: list[EmailOut] = Field(default_factory=list)
    suggested_priorities: list[str] = Field(default_factory=list)
    inbox: InboxSummaryOut | None = None


class ActivityOut(ORMModel):
    id: str
    actor: str
    action: str
    target: str
    status: str
    detail: dict[str, Any]
    created_at: datetime


class NotificationOut(ORMModel):
    id: str
    title: str
    body: str
    level: str
    read: bool
    link: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Automation
# ---------------------------------------------------------------------------


class AutomationOut(ORMModel):
    id: str
    name: str
    natural_language: str
    trigger_type: str
    trigger_config: dict[str, Any]
    actions: list[Any]
    enabled: bool
    requires_confirmation: bool
    # What an approval needed during a run does: ask parks it for you, auto sends it to the
    # safety reviewer. Never "skip": unattended work is always held to a check.
    approval_mode: Literal["ask", "auto"] = "ask"
    # Steps the compiler left out because they cannot run unattended (set when a rule is created).
    warnings: list[str] = Field(default_factory=list)
    run_count: int
    last_run_at: datetime | None
    created_at: datetime


class AutomationIn(BaseModel):
    """Users describe rules in plain language; the engine compiles them."""

    natural_language: str = Field(min_length=5, max_length=1000)
    enabled: bool = True
    approval_mode: Literal["ask", "auto"] = "ask"


class AutomationPatch(BaseModel):
    enabled: bool | None = None
    name: str | None = None
    requires_confirmation: bool | None = None
    approval_mode: Literal["ask", "auto"] | None = None


# ---------------------------------------------------------------------------
# Pending (confirmation-gated) actions
# ---------------------------------------------------------------------------


class PendingActionOut(ORMModel):
    """A parked approval, in the shape the web client's `PendingAction` type reads."""

    id: str
    tool_name: str
    skill_code: str
    arguments: dict[str, Any]
    preview: str
    status: str
    created_at: datetime
    card: Literal["tool", "folder_access", "app_grant", "delete_permission"] = "tool"
    # once | window | always | reject; the special cards offer approve | reject
    options: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    origin: str = "chat"
    origin_id: str = ""
    reason: str = ""
    reason_code: str = ""
    work_item_id: str | None = None
    conversation_id: str | None = None
    # folder path, app id / display name / tier, rule_text (what "always" would allow)
    card_payload: dict[str, Any] = Field(default_factory=dict)
    always_user: bool = False
    decision: str = ""


class PendingDecisionIn(BaseModel):
    """decision: once | window | always | reject, or approve on a folder / app card.

    `minutes` is accepted for old clients and ignored: "window" is always ten minutes.
    """

    decision: Literal["reject", "once", "always", "window", "approve"] = "once"
    minutes: int = Field(default=10, ge=1, le=1440)


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


class SkillOut(BaseModel):
    code: str
    name: str
    category: str
    description: str
    tools: list[str]
    proactive: bool
    autonomy_floor: str
    enabled: bool
    run_count: int
    success_count: int
    last_run_at: datetime | None
    learned_notes: list[str] = Field(default_factory=list)


class SkillPatch(BaseModel):
    enabled: bool | None = None


class SkillTeachIn(BaseModel):
    note: str = Field(min_length=3, max_length=200)


class SkillRunOut(ORMModel):
    id: str
    code: str
    trigger: str
    summary: str
    status: str
    duration_ms: int
    created_at: datetime


class SkillStatsOut(BaseModel):
    total: int
    enabled: int
    total_runs: int
    most_used_code: str = ""
    most_used_name: str = ""


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class AssistantOut(BaseModel):
    id: str
    name: str
    personality: str
    avatar: str
    pronoun: str
    goals: list[str] = Field(default_factory=list)


class AssistantPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=60)
    personality: Literal["concise", "warm", "dry", "formal", "encouraging"] | None = None
    avatar: Literal["teal", "amber", "rose", "violet", "sage"] | None = None
    pronoun: Literal["it", "she", "he", "they"] | None = None
    goals: list[str] | None = None


# ---------------------------------------------------------------------------
# Heartbeat + permissions
# ---------------------------------------------------------------------------


class HeartbeatOut(ORMModel):
    id: str
    headline: str
    lines: list[str]
    skills_run: list[str]
    needs_attention: int
    acknowledged: bool
    created_at: datetime


class CompactionOut(BaseModel):
    before: int
    after: int
    merged: int
    promoted: int
    dropped: int


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------


class PluginOut(BaseModel):
    id: str
    name: str
    category: str
    summary: str
    detail: str
    skills: list[str]
    skill_names: list[str] = Field(default_factory=list)
    core: bool
    available: bool
    unavailable_reason: str
    accent: str
    installed: bool


class PluginSummaryOut(BaseModel):
    installed: int
    available: int
    total: int


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


class ChannelOut(BaseModel):
    kind: str
    name: str
    blurb: str
    setup: str = ""
    inbound: bool
    available: bool
    unavailable_reason: str = ""
    connected: bool
    verified: bool
    identifier: str = ""
    message_count: int = 0
    last_seen_at: datetime | None = None
    # Who counts as the owner on this channel. Until one of these is set, messages that come in
    # are kept as notifications and AURA does not act on them.
    owner_sender: str = ""
    bridge_owner_only: bool = False
    # Returned only immediately after connect/rotate, never on a plain list.
    token: str | None = None


class ChannelOwnerIn(BaseModel):
    owner_sender: str = Field(default="", max_length=320)
    bridge_owner_only: bool = False


class ChannelConnectIn(BaseModel):
    kind: str
    identifier: str = ""


class InboundIn(BaseModel):
    token: str
    text: str = Field(min_length=1, max_length=8000)
    thread_key: str = ""
    # Who wrote it on that channel (an address, a handle, a phone number). Only the owner's own
    # messages are acted on; see Channel.config owner_sender / bridge_owner_only.
    sender: str = Field(default="", max_length=320)


class InboundOut(BaseModel):
    reply: str
    conversation_id: str | None = None
    skills: list[str] = Field(default_factory=list)
    pending: int = 0


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------


class SecretOut(ORMModel):
    key: str
    label: str
    kind: str
    hint: str
    use_count: int
    last_used_at: datetime | None
    created_at: datetime


class SecretIn(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    value: str = Field(min_length=1, max_length=8000)
    label: str = ""
    kind: Literal["secret", "api_key", "password", "token"] = "secret"


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


class DocumentOut(ORMModel):
    id: str
    title: str
    mime_type: str
    size_bytes: int
    summary: str
    source: str
    created_at: datetime


class DocumentDetail(DocumentOut):
    content: str = ""
    chunk_count: int = 0


class DocumentPassage(BaseModel):
    document_id: str
    title: str
    ordinal: int
    excerpt: str
    score: float


class DocumentTextIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=200_000)


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


class ScheduleOut(ORMModel):
    id: str
    name: str
    prompt: str
    natural_language: str
    cron: str
    cron_label: str = ""
    timezone: str
    enabled: bool
    deliver_to: str
    approval_mode: Literal["ask", "auto"] = "ask"
    last_run_at: datetime | None
    last_result: str
    run_count: int
    created_at: datetime


class ScheduleIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=2000)
    natural_language: str = Field(default="", max_length=300)
    name: str = ""
    cron: str = ""
    deliver_to: Literal["notification", "email", "chat"] = "notification"
    approval_mode: Literal["ask", "auto"] = "ask"


class SchedulePatch(BaseModel):
    name: str | None = None
    prompt: str | None = None
    cron: str | None = None
    enabled: bool | None = None
    deliver_to: Literal["notification", "email", "chat"] | None = None
    approval_mode: Literal["ask", "auto"] | None = None


# ---------------------------------------------------------------------------
# Delegations
# ---------------------------------------------------------------------------


class DelegationOut(ORMModel):
    id: str
    assignee_name: str
    assignee_email: str
    title: str
    context: str
    due_at: datetime | None
    status: str
    chased_at: datetime | None
    created_at: datetime


class DelegationIn(BaseModel):
    assignee: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    context: str = ""
    due_at: datetime | None = None


# ---------------------------------------------------------------------------
# Usage + spend
# ---------------------------------------------------------------------------


class SpendOut(BaseModel):
    spent_usd: float
    cap_usd: float
    remaining_usd: float
    percent: int
    calls: int
    tokens: int
    cap_enabled: bool
    provider: str
    model: str


class UsageDayOut(BaseModel):
    date: str
    cost_usd: float
    tokens: int
    calls: int


class UsageTriggerOut(BaseModel):
    trigger: str
    cost_usd: float
    calls: int


class UsageOut(BaseModel):
    today: SpendOut
    daily: list[UsageDayOut] = Field(default_factory=list)
    by_trigger: list[UsageTriggerOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Preflight (mirrors app/preflight.py for the Setup screen)
# ---------------------------------------------------------------------------


class PreflightCheckOut(BaseModel):
    key: str
    label: str
    level: Literal["ok", "warn", "fail"]
    detail: str
    fix: str = ""
    docs: str = ""


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------


class AccountDeleteIn(BaseModel):
    """Deleting is irreversible, so it requires the account's own email typed out."""

    confirm_email: str
    understand: bool = False


# ---------------------------------------------------------------------------
# Device control (local installs)
# ---------------------------------------------------------------------------


class DevicePolicyOut(BaseModel):
    allowed_roots: list[str] = Field(default_factory=list)
    protected_paths: list[str] = Field(default_factory=list)
    exec_mode: str = "ask"
    exec_allowlist: list[str] = Field(default_factory=list)
    denylist_active: bool = False
    exec_timeout_seconds: int = 300
    max_read_bytes: int = 200_000
    explanation: str = ""


class DeviceStatusOut(BaseModel):
    """What AURA can do on this machine, for the Device screen and /setup."""

    enabled: bool
    os: str = ""
    platform: str = ""
    arch: str = ""
    hostname: str = ""
    user: str = ""
    home: str = ""
    python: str = ""
    shell: dict[str, str] = Field(default_factory=dict)
    shell_control: bool = False
    file_control: bool = False
    process_control: bool = False
    screen_control: bool = False
    clipboard: bool = False
    has_display: bool = False
    packages: dict[str, bool] = Field(default_factory=dict)
    policy: DevicePolicyOut | None = None
    # Device skills the user currently has switched on, so the screen can say
    # "shell is off" without the user hunting through the skills page.
    active_skills: list[str] = Field(default_factory=list)
    # Presence pings are accepted from this session (the computer itself, not a paired device).
    presence_accepted: bool = False
    # AURA currently counts the person as being at the screen.
    present: bool = False
    # The user's own switch for mouse and keyboard use (the install setting is a ceiling above it).
    screen_control_user: bool = True


class DeviceFolderIn(BaseModel):
    path: str = Field(min_length=1, max_length=2000)
    can_delete: bool = False


class DeviceFolderPatch(BaseModel):
    can_delete: bool


class DeviceFolderOut(BaseModel):
    id: str
    path: str
    can_delete: bool
    source: str
    within_install_limit: bool = True
    created_at: datetime | None = None


class DeviceAppIn(BaseModel):
    tier: Literal["view", "click", "full"]


class DeviceAppGrantOut(BaseModel):
    id: str | None = None
    app_id: str
    display_name: str = ""
    tier: str


class DeviceAppBlockOut(BaseModel):
    id: str
    app_id: str
    match: str
    source: str


class DeviceRunningAppOut(BaseModel):
    app_id: str
    display_name: str = ""


class DeviceAppsOut(BaseModel):
    grants: list[DeviceAppGrantOut] = Field(default_factory=list)
    blocks: list[DeviceAppBlockOut] = Field(default_factory=list)
    running: list[DeviceRunningAppOut] = Field(default_factory=list)


class DeviceBlockIn(BaseModel):
    app_id: str = Field(min_length=1, max_length=120)
    match: Literal["app", "keyword"] = "app"


class ScreenControlIn(BaseModel):
    enabled: bool


class DeviceRunIn(BaseModel):
    """A command the *user* runs from the Device console, not the agent."""

    command: str = Field(min_length=1, max_length=4000)
    workdir: str = ""
    timeout: int | None = Field(default=None, ge=1, le=3600)


class DeviceRunOut(BaseModel):
    command: str = ""
    workdir: str = ""
    exit_code: int | None = None
    ok: bool = False
    output: str = ""
    truncated: bool = False
    duration_ms: int = 0
    timed_out: bool = False
    error: str = ""


class DeviceProcessOut(BaseModel):
    pid: int
    name: str
    cpu_percent: float = 0.0
    memory_percent: float = 0.0


class DeviceBackgroundOut(BaseModel):
    session_id: str
    command: str
    status: str
    exit_code: int | None = None
    running_seconds: int = 0


class DeviceFileEntryOut(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int = 0
    modified: float = 0.0


class DeviceListingOut(BaseModel):
    path: str
    count: int = 0
    entries: list[DeviceFileEntryOut] = Field(default_factory=list)


ChatOut.model_rebuild()
