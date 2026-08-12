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
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    # strict | conservative | relaxed | full — see agent/autonomy.py
    autonomy_level: Mapped[str] = mapped_column(String(20), default="conservative")
    tone: Mapped[str] = mapped_column(String(50), default="concise-professional")
    theme: Mapped[str] = mapped_column(String(20), default="warm")
    heartbeat_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    heartbeat_interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    quiet_hours: Mapped[str] = mapped_column(String(20), default="22:00-07:00")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)

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


class PendingAction(Base, TimestampMixin):
    """A destructive tool call parked awaiting explicit user approval."""

    __tablename__ = "pending_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    tool_name: Mapped[str] = mapped_column(String(120))
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    preview: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    skill_code: Mapped[str] = mapped_column(String(12), default="")


# ---------------------------------------------------------------------------
# Identity, skills, trust
# ---------------------------------------------------------------------------


class Assistant(Base, TimestampMixin):
    """The user's assistant: name, personality, and how far it has grown.

    One per user. `stage` is derived, never set by hand — see services/identity.py.
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
    hatched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Growth signals. Stage is computed from these.
    interactions: Mapped[int] = mapped_column(Integer, default=0)
    actions_taken: Mapped[int] = mapped_column(Integer, default=0)
    corrections: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(30), default="stranger")
    # stranger | acquaintance | colleague | chief_of_staff

    goals: Mapped[list] = mapped_column(JSON, default=list)
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)


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
