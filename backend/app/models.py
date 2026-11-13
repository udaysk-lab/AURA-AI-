"""SQLAlchemy ORM models.

IDs are string UUIDs so the same schema runs on SQLite (local dev) and
Postgres (everything else) without dialect-specific column types.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    false,
    true as sa_true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    avatar_url: Mapped[str] = mapped_column(String(1000), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    # bcrypt hash. Empty for accounts that only ever signed in through Google —
    # those have no password to verify, and must not be loginable with one.
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When a password-reset link was last emailed, so the form can't be used
    # to flood someone's inbox.
    password_reset_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    oauth_accounts: Mapped[list[OAuthAccount]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    settings: Mapped[UserSettings | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class OAuthAccount(Base, TimestampMixin):
    """Third-party account link. Refresh tokens are encrypted at rest."""

    __tablename__ = "oauth_accounts"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_oauth_user_provider"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(50))  # google | microsoft
    provider_account_id: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    access_token_enc: Mapped[str] = mapped_column(Text, default="")
    refresh_token_enc: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[str] = mapped_column(Text, default="")

    # Sync bookkeeping, so the UI can show something more useful than "connected".
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_emails: Mapped[int] = mapped_column(Integer, default=0)
    last_sync_events: Mapped[int] = mapped_column(Integer, default=0)
    last_sync_error: Mapped[str] = mapped_column(Text, default="")

    user: Mapped[User] = relationship(back_populates="oauth_accounts")


class UserSettings(Base, TimestampMixin):
    __tablename__ = "user_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    briefing_time: Mapped[str] = mapped_column(String(5), default="08:00")
    briefing_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Legacy 1.0 column, kept for migrations and not read by the permission gate.
    # The permission mode (app/permissions/) replaced it.
    autonomy_level: Mapped[str] = mapped_column(String(20), default="conservative")
    tone: Mapped[str] = mapped_column(String(50), default="concise-professional")
    theme: Mapped[str] = mapped_column(String(20), default="warm")
    heartbeat_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    heartbeat_interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    quiet_hours: Mapped[str] = mapped_column(String(20), default="22:00-07:00")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    # ask | auto | skip. Only the signed-in user sets it (spec 10-permissions 2.1).
    # Validated in code: no CHECK constraint, so SQLite batch mode stays simple.
    permission_mode: Mapped[str] = mapped_column(String(8), default="ask", server_default="ask")
    # The user's own limits for the auto reviewer, at most 2000 characters.
    reviewer_boundaries: Mapped[str] = mapped_column(Text, default="", server_default="")
    # No new preferences or Memory rows from chat, and new work items are not recallable.
    memory_paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    user: Mapped[User] = relationship(back_populates="settings")


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300), default="New conversation")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    # Memory off for this chat: no recall, no capture, items are not recallable.
    memory_off: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | tool | system
    content: Mapped[str] = mapped_column(Text, default="")
    # Tool calls, memory references, latency - anything the UI wants to render.
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# ---------------------------------------------------------------------------
# Work objects
# ---------------------------------------------------------------------------


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(500))
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="todo")  # todo|doing|done
    priority: Mapped[str] = mapped_column(String(20), default="medium")  # low|medium|high|urgent
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recurrence: Mapped[str] = mapped_column(String(50), default="")  # daily|weekly|monthly|""
    tags: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(50), default="manual")  # manual|agent|automation
    ai_score: Mapped[float] = mapped_column(Float, default=0.0)

    # Canonical adjacency-list pattern: remote_side pins the "one" end.
    parent: Mapped[Task | None] = relationship(
        back_populates="subtasks", remote_side="Task.id"
    )
    subtasks: Mapped[list[Task]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )


class CalendarEvent(Base, TimestampMixin):
    __tablename__ = "calendar_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(500), default="")
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attendees: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="confirmed")
    source: Mapped[str] = mapped_column(String(30), default="local")  # local|google


class EmailMessage(Base, TimestampMixin):
    """Local cache of a provider email, enriched with AI triage fields."""

    __tablename__ = "email_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    thread_id: Mapped[str] = mapped_column(String(255), default="")
    sender: Mapped[str] = mapped_column(String(320), default="")
    sender_name: Mapped[str] = mapped_column(String(200), default="")
    recipients: Mapped[list] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(String(998), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    labels: Mapped[list] = mapped_column(JSON, default=list)

    # AI triage
    category: Mapped[str] = mapped_column(String(50), default="")
    importance: Mapped[str] = mapped_column(String(20), default="normal")  # low|normal|high|urgent
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    action_items: Mapped[list] = mapped_column(JSON, default=list)
    needs_reply: Mapped[bool] = mapped_column(Boolean, default=False)


class Contact(Base, TimestampMixin):
    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(320), default="", index=True)
    company: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    relationship_tier: Mapped[str] = mapped_column(String(30), default="normal")
    last_interaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[str] = mapped_column(String(120), default="text/plain")
    storage_path: Mapped[str] = mapped_column(String(1000), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(50), default="upload")


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


class Memory(Base, TimestampMixin):
    """A durable fact about the user, retrieved by semantic similarity.

    `embedding` is a JSON array of floats. On SQLite we cosine-compare in
    Python; on Postgres run the pgvector migration in README to switch the
    column to `vector(1536)` and let the database do it. See services/memory.py.
    """

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(50), default="fact", index=True)
    # kind: preference | contact | project | decision | style | habit | fact
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(50), default="chat")
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    embedding: Mapped[list] = mapped_column(JSON, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------------------
# Task memory and preference memory (spec 20-taskloop-memory.md section 2)
# ---------------------------------------------------------------------------


def _verification_default() -> dict:
    return {"status": "none", "checks": []}


class WorkItem(Base, TimestampMixin):
    """The record of one assignment: instruction, type, plan, systems, how it went, outcome.

    Never compacted or decayed. (Named WorkItem: `Task` is the user's to-do list.)
    status: running | waiting | ready | done | partly_done | failed | stopped
    """

    __tablename__ = "work_items"
    __table_args__ = (
        Index("ix_work_items_user_status", "user_id", "status"),
        Index("ix_work_items_user_type", "user_id", "task_type"),
        Index("ix_work_items_status_ready", "status", "ready_at"),
        Index("ix_work_items_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    # chat | schedule | inbound | automation: the origin trigger, never changes.
    trigger: Mapped[str] = mapped_column(String(20), default="chat")
    origin_ref: Mapped[str] = mapped_column(String(64), default="", server_default="")
    instruction: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    task_type: Mapped[str] = mapped_column(String(40), default="other", index=True)
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    # approval | segment_budget | segment_time | interrupted
    wait_reason: Mapped[str] = mapped_column(String(20), default="", server_default="")
    plan: Mapped[list] = mapped_column(JSON, default=list)
    systems_used: Mapped[list] = mapped_column(JSON, default=list)
    tools_used: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    outcome: Mapped[str] = mapped_column(String(300), default="", server_default="")
    report: Mapped[str] = mapped_column(Text, default="", server_default="")
    # {"status": passed|failed|partial|none, "checks": [{method, passed, evidence}]}
    verification: Mapped[dict] = mapped_column(JSON, default=_verification_default)
    # {"rating": up|down, "note": "", "at": iso}
    user_feedback: Mapped[dict] = mapped_column(JSON, default=dict)
    recallable: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())
    steps_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    segments: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str] = mapped_column(String(36), default="", server_default="")
    # The resumable loop state (spec section 6.1); {} at a terminal status.
    loop_state: Mapped[dict] = mapped_column(JSON, default=dict)
    # {"work_item_ids": [], "preference_ids": [], "memory_ids": []}
    recall_used: Mapped[dict] = mapped_column(JSON, default=dict)
    embedding: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(Text, default="", server_default="")

    steps: Mapped[list[WorkStep]] = relationship(
        back_populates="work_item", cascade="all, delete-orphan", order_by="WorkStep.seq",
        passive_deletes=True,
    )


class WorkStep(Base, TimestampMixin):
    """One thing that happened inside a work item, in order."""

    __tablename__ = "work_steps"
    __table_args__ = (UniqueConstraint("work_item_id", "seq", name="uq_work_steps_item_seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    work_item_id: Mapped[str] = mapped_column(
        ForeignKey("work_items.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    # The model step (1-based) that produced it; 0 for resume notes.
    step_no: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # plan | tool | approval | reviewer_block | verification | note
    kind: Mapped[str] = mapped_column(String(20))
    tool_name: Mapped[str] = mapped_column(String(120), default="", server_default="")
    tool_call_id: Mapped[str] = mapped_column(String(64), default="", server_default="")
    system: Mapped[str] = mapped_column(String(60), default="", server_default="")
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="ok")
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    # pending_action_id, decision_source, reason, duration_ms
    detail: Mapped[dict] = mapped_column(JSON, default=dict)

    work_item: Mapped[WorkItem] = relationship(back_populates="steps")


class Preference(Base, TimestampMixin):
    """How the owner wants a kind of work done, keyed by task type (or `general`)."""

    __tablename__ = "preferences"
    __table_args__ = (Index("ix_preferences_lookup", "user_id", "active", "task_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_type: Mapped[str] = mapped_column(String(40), default="general")
    # A plugin id; "" means any system.
    system_scope: Mapped[str] = mapped_column(String(60), default="", server_default="")
    text: Mapped[str] = mapped_column(Text)
    # stated | correction
    source: Mapped[str] = mapped_column(String(20), default="stated")
    # chat | feedback | settings | skill_teach | migrated
    origin: Mapped[str] = mapped_column(String(20), default="chat")
    origin_message_id: Mapped[str | None] = mapped_column(String(36))
    origin_excerpt: Mapped[str] = mapped_column(String(300), default="", server_default="")
    origin_work_item_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("work_items.id", ondelete="SET NULL")
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())
    superseded_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("preferences.id", ondelete="SET NULL")
    )
    use_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Automation / ops
# ---------------------------------------------------------------------------


class AutomationRule(Base, TimestampMixin):
    __tablename__ = "automation_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    natural_language: Mapped[str] = mapped_column(Text, default="")
    trigger_type: Mapped[str] = mapped_column(String(50), default="email_received")
    # email_received | schedule | event_cancelled | task_due | manual
    trigger_config: Mapped[dict] = mapped_column(JSON, default=dict)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=True)
    # ask | auto: what an approval needed during an unattended run does (spec 10, 2.4)
    approval_mode: Mapped[str] = mapped_column(String(8), default="ask", server_default="ask")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_count: Mapped[int] = mapped_column(Integer, default=0)


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    level: Mapped[str] = mapped_column(String(20), default="info")  # info|success|warning|urgent
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    link: Mapped[str] = mapped_column(String(500), default="")


class ActivityLog(Base, TimestampMixin):
    """Audit trail. Every tool the agent runs lands here."""

    __tablename__ = "activity_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    actor: Mapped[str] = mapped_column(String(30), default="agent")  # agent|user|automation
    action: Mapped[str] = mapped_column(String(120))
    target: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(30), default="success")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)

    work_item_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("work_items.id", ondelete="SET NULL"), index=True
    )


class PendingAction(Base, TimestampMixin):
    """A tool call parked awaiting the user's answer (spec 10-permissions 2.3).

    Status: pending | approved | rejected | expired | refused | resolving.
    """

    __tablename__ = "pending_actions"
    __table_args__ = (Index("ix_pa_user_status", "user_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    tool_name: Mapped[str] = mapped_column(String(120))
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    preview: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    skill_code: Mapped[str] = mapped_column(String(12), default="")
    # The task-loop run this belongs to (tl01 added the foreign key).
    work_item_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("work_items.id", ondelete="SET NULL"), index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # chat | schedule | automation | inbound
    origin: Mapped[str] = mapped_column(String(20), default="chat", server_default="chat")
    origin_id: Mapped[str] = mapped_column(String(36), default="", server_default="")
    # tool | folder_access | app_grant | delete_permission
    card: Mapped[str] = mapped_column(String(20), default="tool", server_default="tool")
    card_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    # Subset of once, window, always, reject.
    options: Mapped[list] = mapped_column(JSON, default=list)
    always_user: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    reason_code: Mapped[str] = mapped_column(String(40), default="", server_default="")
    reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    # sha256 of the canonical JSON arguments (sorted keys, no spaces).
    args_digest: Mapped[str] = mapped_column(String(64), default="", server_default="")
    # once | window | always | reject, as answered.
    decision: Mapped[str] = mapped_column(String(10), default="", server_default="")
    # session | remote:<device id> | local-open | system
    resolved_by: Mapped[str] = mapped_column(String(40), default="", server_default="")
    rule_id: Mapped[str | None] = mapped_column(String(36))
    # The model's tool call this card answers, so a resumed run can pair the result with it.
    tool_call_id: Mapped[str] = mapped_column(String(64), default="", server_default="")
    # What the approved tool returned (redacted by the loop's hook); None until resolved.
    result: Mapped[dict | None] = mapped_column(JSON)


# ---------------------------------------------------------------------------
# Identity, skills, permissions
# ---------------------------------------------------------------------------


class Assistant(Base, TimestampMixin):
    """The user's assistant: name, personality, avatar.

    One per user. The table still has six 1.0.0 columns that are deliberately
    unmapped. Each has a server default (revision 0002) and is listed in
    migration_ops.LEGACY_PENDING_DROP until a contract revision drops it.
    """

    __tablename__ = "assistants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(60), default="Aura")
    personality: Mapped[str] = mapped_column(String(40), default="concise")
    # concise | warm | dry | formal | encouraging
    avatar: Mapped[str] = mapped_column(String(30), default="teal")
    # teal | amber | rose | violet | sage — maps to a mascot colourway
    pronoun: Mapped[str] = mapped_column(String(20), default="it")
    goals: Mapped[list] = mapped_column(JSON, default=list)


class UserSkill(Base, TimestampMixin):
    """Per-user state for a skill in the catalogue.

    The catalogue itself lives in code (agent/skills.py); this row only tracks
    what the user has turned on and how the skill has performed for them.
    """

    __tablename__ = "user_skills"
    __table_args__ = (UniqueConstraint("user_id", "code", name="uq_user_skill"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Free-text corrections the user has given. Injected into the prompt when
    # this skill is relevant — this is how a skill "learns how you like it done".
    learned_notes: Mapped[list] = mapped_column(JSON, default=list)


class SkillRun(Base, TimestampMixin):
    """One execution of a skill, with the compact one-line report the UI streams."""

    __tablename__ = "skill_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    trigger: Mapped[str] = mapped_column(String(20), default="chat")
    # chat | heartbeat | automation | manual
    summary: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="success")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    work_item_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("work_items.id", ondelete="SET NULL"), index=True
    )


class ApprovalGrant(Base, TimestampMixin):
    """A standing permission the user has given for a specific tool.

    Created when they pick 'Always allow' or 'Allow for 10 minutes' on a
    confirmation prompt. Checked before anything is queued.
    """

    __tablename__ = "approval_grants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tool_name: Mapped[str] = mapped_column(String(120), index=True)
    scope: Mapped[str] = mapped_column(String(20), default="always")  # always | window
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    # What the permission covers, for tools where "always" must not mean
    # "anything": a grant for send_email names one recipient. Empty means the
    # tool as a whole, which is how every other tool's grants work.
    target: Mapped[str] = mapped_column(String(320), default="")


class HeartbeatReport(Base, TimestampMixin):
    """What the assistant did on its own while the user was away."""

    __tablename__ = "heartbeat_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    headline: Mapped[str] = mapped_column(String(300), default="")
    lines: Mapped[list] = mapped_column(JSON, default=list)
    skills_run: Mapped[list] = mapped_column(JSON, default=list)
    needs_attention: Mapped[int] = mapped_column(Integer, default=0)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------------------
# Plugins, channels, vault, documents, schedules
# ---------------------------------------------------------------------------


class InstalledPlugin(Base, TimestampMixin):
    """A plugin the user has installed. The catalogue itself lives in plugins.py.

    Installing a plugin unlocks its skills; uninstalling hides them again
    without destroying the skill's learned notes or run history.
    """

    __tablename__ = "installed_plugins"
    __table_args__ = (UniqueConstraint("user_id", "plugin_id", name="uq_user_plugin"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plugin_id: Mapped[str] = mapped_column(String(60), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class Channel(Base, TimestampMixin):
    """A surface the assistant can be reached on."""

    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("user_id", "kind", name="uq_user_channel"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    # web | email | slack | telegram | cli | voice | sms
    identifier: Mapped[str] = mapped_column(String(320), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # Shared secret an inbound webhook must present. Never leaves the server
    # except once, at setup, to the authenticated owner.
    token: Mapped[str] = mapped_column(String(64), default="", index=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_count: Mapped[int] = mapped_column(Integer, default=0)


class VaultSecret(Base, TimestampMixin):
    """An encrypted credential.

    The value is never returned by the API and never enters a prompt. Tools
    reference it by key; a deterministic substitution step swaps the reference
    for the real value immediately before the outbound call.
    """

    __tablename__ = "vault_secrets"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_secret"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(80), index=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(30), default="secret")
    # secret | api_key | password | token
    value_enc: Mapped[str] = mapped_column(Text, default="")
    hint: Mapped[str] = mapped_column(String(40), default="")  # e.g. "sk-…9f2"
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentChunk(Base, TimestampMixin):
    """A retrievable slice of a document."""

    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[list] = mapped_column(JSON, default=list)


class Schedule(Base, TimestampMixin):
    """A standing instruction the agent runs on a timer.

    Different from an AutomationRule: an automation fires a fixed list of tool
    calls, a schedule runs a *prompt* through the full agent. That makes it far
    more capable and correspondingly more expensive, so they stay separate.
    """

    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    prompt: Mapped[str] = mapped_column(Text)
    natural_language: Mapped[str] = mapped_column(Text, default="")
    cron: Mapped[str] = mapped_column(String(60), default="0 8 * * *")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    deliver_to: Mapped[str] = mapped_column(String(30), default="notification")
    # notification | email | chat
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_result: Mapped[str] = mapped_column(Text, default="")
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    # ask | auto: what an approval needed during a run does (spec 10, 2.4)
    approval_mode: Mapped[str] = mapped_column(String(8), default="ask", server_default="ask")


class UsageRecord(Base, TimestampMixin):
    """One model call. The audit trail behind the spend cap.

    Deliberately append-only and never aggregated away — when someone asks why
    their bill looks like that, the answer needs to be recoverable per call.
    """

    __tablename__ = "usage_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    model: Mapped[str] = mapped_column(String(80), default="")
    trigger: Mapped[str] = mapped_column(String(30), default="chat", index=True)
    # chat | heartbeat | schedule | automation | inbound | triage | research
    skill_code: Mapped[str] = mapped_column(String(12), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    work_item_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("work_items.id", ondelete="SET NULL"), index=True
    )


class Delegation(Base, TimestampMixin):
    """Something handed to another person, with a chase-up date."""

    __tablename__ = "delegations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    assignee_name: Mapped[str] = mapped_column(String(200), default="")
    assignee_email: Mapped[str] = mapped_column(String(320), default="")
    title: Mapped[str] = mapped_column(String(500))
    context: Mapped[str] = mapped_column(Text, default="")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|chased|done
    chased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SampleRow(Base, TimestampMixin):
    """One row the sample-data seeder created, so it can be removed exactly.

    Removal used to find sample rows by their titles, which also caught the
    user's own "Standup" or "Draft Q3 board update" and deleted them. The seeder
    now writes down the id of everything it adds, and removal deletes only those.
    """

    __tablename__ = "sample_rows"
    __table_args__ = (UniqueConstraint("user_id", "kind", "row_id", name="uq_sample_row"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # emails | events | tasks | …
    row_id: Mapped[str] = mapped_column(String(36))


class PairedDevice(Base, TimestampMixin):
    """A phone or laptop allowed to use this computer's AURA remotely.

    Local installs only. A device is added by pairing — a short code shown on
    this computer and typed on the other device — and holds a token that names
    this row, so unpairing it here signs it out everywhere at once.
    """

    __tablename__ = "paired_devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_address: Mapped[str] = mapped_column(String(64), default="")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PairingCode(Base, TimestampMixin):
    """A one-time code for pairing a device. Only its hash is stored."""

    __tablename__ = "pairing_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Permissions (spec 10-permissions 2.2, 2.5, 2.6)
# ---------------------------------------------------------------------------


class PermissionRule(Base, TimestampMixin):
    """One block / ask / allow rule for a system or a single tool, optionally
    limited to a target (a recipient, path, command, domain or app)."""

    __tablename__ = "permission_rules"
    __table_args__ = (
        Index("ix_prules_user_tool", "user_id", "tool"),
        Index("ix_prules_user_system", "user_id", "system"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    effect: Mapped[str] = mapped_column(String(8))  # block | ask | allow
    system: Mapped[str] = mapped_column(String(60))  # plugin id or "*"
    tool: Mapped[str | None] = mapped_column(String(120))  # NULL = every tool of the system
    # "" | path | command | recipient | domain | app
    target_kind: Mapped[str] = mapped_column(String(12), default="", server_default="")
    pattern: Mapped[str] = mapped_column(String(1024), default="", server_default="")
    # "" everywhere | schedule:<id> | automation:<id>
    applies_to: Mapped[str] = mapped_column(String(80), default="", server_default="")
    source: Mapped[str] = mapped_column(String(12), default="user", server_default="user")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str] = mapped_column(String(300), default="", server_default="")


class DeviceFolder(Base, TimestampMixin):
    """A folder the user connected to AURA. Rows exist only on local installs."""

    __tablename__ = "device_folders"
    __table_args__ = (UniqueConstraint("user_id", "path_key", name="uq_device_folders_user_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(Text)  # realpath as stored
    path_key: Mapped[str] = mapped_column(String(64))  # sha256 of the folded realpath
    can_delete: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    source: Mapped[str] = mapped_column(String(12), default="user", server_default="user")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceAppGrant(Base, TimestampMixin):
    """Screen-control access the user granted to one application."""

    __tablename__ = "device_app_grants"
    __table_args__ = (UniqueConstraint("user_id", "app_id", name="uq_device_app_grants_user_app"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    app_id: Mapped[str] = mapped_column(String(120))  # canonical id (device/appnames.py)
    display_name: Mapped[str] = mapped_column(String(200), default="", server_default="")
    tier: Mapped[str] = mapped_column(String(8))  # view | click | full
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceAppBlock(Base, TimestampMixin):
    """An application AURA must never operate (banking, trading, password managers...)."""

    __tablename__ = "device_app_blocks"
    __table_args__ = (UniqueConstraint("user_id", "app_id", name="uq_device_app_blocks_user_app"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    app_id: Mapped[str] = mapped_column(String(120))
    match: Mapped[str] = mapped_column(String(8), default="app", server_default="app")  # app | keyword
    source: Mapped[str] = mapped_column(String(12), default="user", server_default="user")  # default | user


class PermissionEvent(Base, TimestampMixin):
    """Tamper-evident log of permission decisions and changes.

    Each row's `hash` covers the previous row's, and `mac` signs the hash with a
    key derived from the server secret. Rows are never updated or deleted by code.
    """

    __tablename__ = "permission_events"
    __table_args__ = (
        UniqueConstraint("user_id", "seq", name="uq_permission_events_user_seq"),
        Index("ix_permission_events_user_run", "user_id", "run_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)  # per user, starts at 1
    actor: Mapped[str] = mapped_column(String(12))  # user | aura | system | migration
    kind: Mapped[str] = mapped_column(String(30))
    tool: Mapped[str] = mapped_column(String(120), default="", server_default="")
    target: Mapped[str] = mapped_column(String(500), default="", server_default="")
    outcome: Mapped[str] = mapped_column(String(8), default="", server_default="")
    reason_code: Mapped[str] = mapped_column(String(40), default="", server_default="")
    run_key: Mapped[str] = mapped_column(String(36), default="", server_default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), default="", server_default="")
    hash: Mapped[str] = mapped_column(String(64), default="", server_default="")
    mac: Mapped[str] = mapped_column(String(64), default="", server_default="")
