"""Pydantic request/response models. These define the public API contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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


class RegisterIn(BaseModel):
    email: EmailStr
    # 72 is bcrypt's hard ceiling — see security.MAX_PASSWORD_BYTES. Enforced
    # here too so the error is a 422 about the field rather than a 500.
    password: str = Field(min_length=8, max_length=72)
    name: str = Field(default="", max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class SettingsOut(ORMModel):
    briefing_time: str
    briefing_enabled: bool
    autonomy_level: str
    tone: str
    theme: str
    heartbeat_enabled: bool
    heartbeat_interval_minutes: int
    quiet_hours: str


class SettingsIn(BaseModel):
    briefing_time: str | None = None
    briefing_enabled: bool | None = None
    autonomy_level: Literal["strict", "conservative", "relaxed", "full"] | None = None
    tone: str | None = None
    theme: Literal["warm", "dark", "light"] | None = None
    heartbeat_enabled: bool | None = None
    heartbeat_interval_minutes: int | None = Field(default=None, ge=5, le=720)
    quiet_hours: str | None = None


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


class ChatOut(BaseModel):
    conversation_id: str
    message: MessageOut
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    skill_runs: list[dict[str, Any]] = Field(default_factory=list)
    memories_used: list[str] = Field(default_factory=list)
    pending_actions: list[PendingActionOut] = Field(default_factory=list)
    learned: str = ""


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


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    kind: Literal[
        "preference", "contact", "project", "decision", "style", "habit", "fact"
    ] = "fact"
    pinned: bool = False


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
    run_count: int
    last_run_at: datetime | None
    created_at: datetime


class AutomationIn(BaseModel):
    """Users describe rules in plain language; the engine compiles them."""

    natural_language: str = Field(min_length=5, max_length=1000)
    enabled: bool = True


class AutomationPatch(BaseModel):
    enabled: bool | None = None
    name: str | None = None
    requires_confirmation: bool | None = None


# ---------------------------------------------------------------------------
# Pending (confirmation-gated) actions
# ---------------------------------------------------------------------------


class PendingActionOut(ORMModel):
    id: str
    tool_name: str
    skill_code: str
    arguments: dict[str, Any]
    preview: str
    status: str
    created_at: datetime


class PendingDecisionIn(BaseModel):
    """decision: reject | once | always | window"""

    decision: Literal["reject", "once", "always", "window"] = "once"
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
    onboarded: bool
    hatched_at: datetime
    stage: str
    stage_label: str
    stage_blurb: str
    signals: dict[str, int]
    progress: dict[str, Any] | None = None
    days_together: int


class AssistantPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    personality: Literal["concise", "warm", "dry", "formal", "encouraging"] | None = None
    avatar: Literal["teal", "amber", "rose", "violet", "sage"] | None = None
    pronoun: Literal["it", "she", "he", "they"] | None = None
    goals: list[str] | None = None


class HatchIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    personality: Literal["concise", "warm", "dry", "formal", "encouraging"] = "concise"
    avatar: Literal["teal", "amber", "rose", "violet", "sage"] = "teal"
    pronoun: Literal["it", "she", "he", "they"] = "it"
    goals: list[str] = Field(default_factory=list)
    role: str = ""
    about: str = ""
    autonomy_level: Literal["strict", "conservative", "relaxed", "full"] = "conservative"


# ---------------------------------------------------------------------------
# Heartbeat + trust
# ---------------------------------------------------------------------------


class HeartbeatOut(ORMModel):
    id: str
    headline: str
    lines: list[str]
    skills_run: list[str]
    needs_attention: int
    acknowledged: bool
    created_at: datetime


class GrantOut(ORMModel):
    id: str
    tool_name: str
    scope: str
    expires_at: datetime | None
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
    # Returned only immediately after connect/rotate, never on a plain list.
    token: str | None = None


class ChannelConnectIn(BaseModel):
    kind: str
    identifier: str = ""


class InboundIn(BaseModel):
    token: str
    text: str = Field(min_length=1, max_length=8000)
    thread_key: str = ""


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


class SchedulePatch(BaseModel):
    name: str | None = None
    prompt: str | None = None
    cron: str | None = None
    enabled: bool | None = None
    deliver_to: Literal["notification", "email", "chat"] | None = None


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


class PreflightOut(BaseModel):
    environment: str
    ready: bool
    failures: int
    warnings: int
    checks: list[PreflightCheckOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------


class AccountDeleteIn(BaseModel):
    """Deleting is irreversible, so it requires the account's own email typed out."""

    confirm_email: str
    understand: bool = False


ChatOut.model_rebuild()
