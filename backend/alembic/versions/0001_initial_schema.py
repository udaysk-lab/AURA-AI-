"""Initial schema: every table as of AURA 1.0.0.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-14

Generated from the models and checked with `alembic check` against a database
built by it. Two ways to start using Alembic:

* A new, empty database:      alembic upgrade head
* One the app already built   alembic stamp head
  with create_all (the live    (records that it's at this revision without
  site, any local aura.db):    touching a table), then set AUTO_CREATE_SCHEMA=false

From then on a model change ships with `alembic revision --autogenerate`.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('avatar_url', sa.String(length=1000), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('password_reset_sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)

    op.create_table('activity_logs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('actor', sa.String(length=30), nullable=False),
    sa.Column('action', sa.String(length=120), nullable=False),
    sa.Column('target', sa.String(length=300), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('detail', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('activity_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_activity_logs_user_id'), ['user_id'], unique=False)

    op.create_table('approval_grants',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('tool_name', sa.String(length=120), nullable=False),
    sa.Column('scope', sa.String(length=20), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked', sa.Boolean(), nullable=False),
    sa.Column('target', sa.String(length=320), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('approval_grants', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_approval_grants_tool_name'), ['tool_name'], unique=False)
        batch_op.create_index(batch_op.f('ix_approval_grants_user_id'), ['user_id'], unique=False)

    op.create_table('assistants',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('personality', sa.String(length=40), nullable=False),
    sa.Column('avatar', sa.String(length=30), nullable=False),
    sa.Column('pronoun', sa.String(length=20), nullable=False),
    sa.Column('hatched_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('interactions', sa.Integer(), nullable=False),
    sa.Column('actions_taken', sa.Integer(), nullable=False),
    sa.Column('corrections', sa.Integer(), nullable=False),
    sa.Column('stage', sa.String(length=30), nullable=False),
    sa.Column('goals', sa.JSON(), nullable=False),
    sa.Column('onboarded', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('assistants', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_assistants_user_id'), ['user_id'], unique=True)

    op.create_table('automation_rules',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('natural_language', sa.Text(), nullable=False),
    sa.Column('trigger_type', sa.String(length=50), nullable=False),
    sa.Column('trigger_config', sa.JSON(), nullable=False),
    sa.Column('actions', sa.JSON(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('requires_confirmation', sa.Boolean(), nullable=False),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('run_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('automation_rules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_automation_rules_user_id'), ['user_id'], unique=False)

    op.create_table('calendar_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('location', sa.String(length=500), nullable=False),
    sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('attendees', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('source', sa.String(length=30), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('calendar_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_calendar_events_external_id'), ['external_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_calendar_events_start_at'), ['start_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_calendar_events_user_id'), ['user_id'], unique=False)

    op.create_table('channels',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('identifier', sa.String(length=320), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('verified', sa.Boolean(), nullable=False),
    sa.Column('token', sa.String(length=64), nullable=False),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('message_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'kind', name='uq_user_channel')
    )
    with op.batch_alter_table('channels', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_channels_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_channels_token'), ['token'], unique=False)
        batch_op.create_index(batch_op.f('ix_channels_user_id'), ['user_id'], unique=False)

    op.create_table('contacts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('company', sa.String(length=200), nullable=False),
    sa.Column('role', sa.String(length=200), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('relationship_tier', sa.String(length=30), nullable=False),
    sa.Column('last_interaction_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('contacts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_contacts_email'), ['email'], unique=False)
        batch_op.create_index(batch_op.f('ix_contacts_user_id'), ['user_id'], unique=False)

    op.create_table('conversations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('pinned', sa.Boolean(), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_conversations_user_id'), ['user_id'], unique=False)

    op.create_table('delegations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('assignee_name', sa.String(length=200), nullable=False),
    sa.Column('assignee_email', sa.String(length=320), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('context', sa.Text(), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('chased_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('delegations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_delegations_user_id'), ['user_id'], unique=False)

    op.create_table('documents',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('mime_type', sa.String(length=120), nullable=False),
    sa.Column('storage_path', sa.String(length=1000), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=50), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_documents_user_id'), ['user_id'], unique=False)

    op.create_table('email_messages',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=False),
    sa.Column('thread_id', sa.String(length=255), nullable=False),
    sa.Column('sender', sa.String(length=320), nullable=False),
    sa.Column('sender_name', sa.String(length=200), nullable=False),
    sa.Column('recipients', sa.JSON(), nullable=False),
    sa.Column('subject', sa.String(length=998), nullable=False),
    sa.Column('snippet', sa.Text(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('is_read', sa.Boolean(), nullable=False),
    sa.Column('is_archived', sa.Boolean(), nullable=False),
    sa.Column('labels', sa.JSON(), nullable=False),
    sa.Column('category', sa.String(length=50), nullable=False),
    sa.Column('importance', sa.String(length=20), nullable=False),
    sa.Column('ai_summary', sa.Text(), nullable=False),
    sa.Column('action_items', sa.JSON(), nullable=False),
    sa.Column('needs_reply', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('email_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_email_messages_external_id'), ['external_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_messages_received_at'), ['received_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_messages_user_id'), ['user_id'], unique=False)

    op.create_table('heartbeat_reports',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('headline', sa.String(length=300), nullable=False),
    sa.Column('lines', sa.JSON(), nullable=False),
    sa.Column('skills_run', sa.JSON(), nullable=False),
    sa.Column('needs_attention', sa.Integer(), nullable=False),
    sa.Column('acknowledged', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('heartbeat_reports', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_heartbeat_reports_user_id'), ['user_id'], unique=False)

    op.create_table('installed_plugins',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('plugin_id', sa.String(length=60), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'plugin_id', name='uq_user_plugin')
    )
    with op.batch_alter_table('installed_plugins', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_installed_plugins_plugin_id'), ['plugin_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_installed_plugins_user_id'), ['user_id'], unique=False)

    op.create_table('memories',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=50), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=50), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('embedding', sa.JSON(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('use_count', sa.Integer(), nullable=False),
    sa.Column('pinned', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('memories', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_memories_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_memories_user_id'), ['user_id'], unique=False)

    op.create_table('notifications',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('level', sa.String(length=20), nullable=False),
    sa.Column('read', sa.Boolean(), nullable=False),
    sa.Column('link', sa.String(length=500), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notifications_user_id'), ['user_id'], unique=False)

    op.create_table('oauth_accounts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('provider', sa.String(length=50), nullable=False),
    sa.Column('provider_account_id', sa.String(length=255), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('access_token_enc', sa.Text(), nullable=False),
    sa.Column('refresh_token_enc', sa.Text(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('scopes', sa.Text(), nullable=False),
    sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_sync_emails', sa.Integer(), nullable=False),
    sa.Column('last_sync_events', sa.Integer(), nullable=False),
    sa.Column('last_sync_error', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'provider', name='uq_oauth_user_provider')
    )
    with op.batch_alter_table('oauth_accounts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_oauth_accounts_user_id'), ['user_id'], unique=False)

    op.create_table('paired_devices',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('user_agent', sa.String(length=300), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_address', sa.String(length=64), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('paired_devices', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_paired_devices_user_id'), ['user_id'], unique=False)

    op.create_table('pairing_codes',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('pairing_codes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_pairing_codes_code_hash'), ['code_hash'], unique=False)
        batch_op.create_index(batch_op.f('ix_pairing_codes_user_id'), ['user_id'], unique=False)

    op.create_table('pending_actions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=True),
    sa.Column('tool_name', sa.String(length=120), nullable=False),
    sa.Column('arguments', sa.JSON(), nullable=False),
    sa.Column('preview', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('skill_code', sa.String(length=12), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('pending_actions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_pending_actions_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_pending_actions_user_id'), ['user_id'], unique=False)

    op.create_table('sample_rows',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('row_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'kind', 'row_id', name='uq_sample_row')
    )
    with op.batch_alter_table('sample_rows', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sample_rows_user_id'), ['user_id'], unique=False)

    op.create_table('schedules',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('prompt', sa.Text(), nullable=False),
    sa.Column('natural_language', sa.Text(), nullable=False),
    sa.Column('cron', sa.String(length=60), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('deliver_to', sa.String(length=30), nullable=False),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_result', sa.Text(), nullable=False),
    sa.Column('run_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('schedules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_schedules_user_id'), ['user_id'], unique=False)

    op.create_table('skill_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('code', sa.String(length=12), nullable=False),
    sa.Column('trigger', sa.String(length=20), nullable=False),
    sa.Column('summary', sa.String(length=500), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('detail', sa.JSON(), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('skill_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_skill_runs_code'), ['code'], unique=False)
        batch_op.create_index(batch_op.f('ix_skill_runs_user_id'), ['user_id'], unique=False)

    op.create_table('tasks',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('parent_id', sa.String(length=36), nullable=True),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('priority', sa.String(length=20), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('recurrence', sa.String(length=50), nullable=False),
    sa.Column('tags', sa.JSON(), nullable=False),
    sa.Column('source', sa.String(length=50), nullable=False),
    sa.Column('ai_score', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['parent_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tasks_parent_id'), ['parent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_tasks_user_id'), ['user_id'], unique=False)

    op.create_table('usage_records',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('model', sa.String(length=80), nullable=False),
    sa.Column('trigger', sa.String(length=30), nullable=False),
    sa.Column('skill_code', sa.String(length=12), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('usage_records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_usage_records_trigger'), ['trigger'], unique=False)
        batch_op.create_index(batch_op.f('ix_usage_records_user_id'), ['user_id'], unique=False)

    op.create_table('user_settings',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('briefing_time', sa.String(length=5), nullable=False),
    sa.Column('briefing_enabled', sa.Boolean(), nullable=False),
    sa.Column('autonomy_level', sa.String(length=20), nullable=False),
    sa.Column('tone', sa.String(length=50), nullable=False),
    sa.Column('theme', sa.String(length=20), nullable=False),
    sa.Column('heartbeat_enabled', sa.Boolean(), nullable=False),
    sa.Column('heartbeat_interval_minutes', sa.Integer(), nullable=False),
    sa.Column('quiet_hours', sa.String(length=20), nullable=False),
    sa.Column('extra', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('user_settings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_settings_user_id'), ['user_id'], unique=True)

    op.create_table('user_skills',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('code', sa.String(length=12), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('run_count', sa.Integer(), nullable=False),
    sa.Column('success_count', sa.Integer(), nullable=False),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('learned_notes', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'code', name='uq_user_skill')
    )
    with op.batch_alter_table('user_skills', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_skills_code'), ['code'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_skills_user_id'), ['user_id'], unique=False)

    op.create_table('vault_secrets',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('value_enc', sa.Text(), nullable=False),
    sa.Column('hint', sa.String(length=40), nullable=False),
    sa.Column('use_count', sa.Integer(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'key', name='uq_user_secret')
    )
    with op.batch_alter_table('vault_secrets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_vault_secrets_key'), ['key'], unique=False)
        batch_op.create_index(batch_op.f('ix_vault_secrets_user_id'), ['user_id'], unique=False)

    op.create_table('document_chunks',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('document_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('embedding', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('document_chunks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_document_chunks_document_id'), ['document_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_document_chunks_user_id'), ['user_id'], unique=False)

    op.create_table('messages',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('meta', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_messages_conversation_id'), ['conversation_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_messages_conversation_id'))

    op.drop_table('messages')
    with op.batch_alter_table('document_chunks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_document_chunks_user_id'))
        batch_op.drop_index(batch_op.f('ix_document_chunks_document_id'))

    op.drop_table('document_chunks')
    with op.batch_alter_table('vault_secrets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_vault_secrets_user_id'))
        batch_op.drop_index(batch_op.f('ix_vault_secrets_key'))

    op.drop_table('vault_secrets')
    with op.batch_alter_table('user_skills', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_skills_user_id'))
        batch_op.drop_index(batch_op.f('ix_user_skills_code'))

    op.drop_table('user_skills')
    with op.batch_alter_table('user_settings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_settings_user_id'))

    op.drop_table('user_settings')
    with op.batch_alter_table('usage_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_usage_records_user_id'))
        batch_op.drop_index(batch_op.f('ix_usage_records_trigger'))

    op.drop_table('usage_records')
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tasks_user_id'))
        batch_op.drop_index(batch_op.f('ix_tasks_parent_id'))

    op.drop_table('tasks')
    with op.batch_alter_table('skill_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_skill_runs_user_id'))
        batch_op.drop_index(batch_op.f('ix_skill_runs_code'))

    op.drop_table('skill_runs')
    with op.batch_alter_table('schedules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_schedules_user_id'))

    op.drop_table('schedules')
    with op.batch_alter_table('sample_rows', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sample_rows_user_id'))

    op.drop_table('sample_rows')
    with op.batch_alter_table('pending_actions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_pending_actions_user_id'))
        batch_op.drop_index(batch_op.f('ix_pending_actions_conversation_id'))

    op.drop_table('pending_actions')
    with op.batch_alter_table('pairing_codes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_pairing_codes_user_id'))
        batch_op.drop_index(batch_op.f('ix_pairing_codes_code_hash'))

    op.drop_table('pairing_codes')
    with op.batch_alter_table('paired_devices', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_paired_devices_user_id'))

    op.drop_table('paired_devices')
    with op.batch_alter_table('oauth_accounts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_oauth_accounts_user_id'))

    op.drop_table('oauth_accounts')
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notifications_user_id'))

    op.drop_table('notifications')
    with op.batch_alter_table('memories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_memories_user_id'))
        batch_op.drop_index(batch_op.f('ix_memories_kind'))

    op.drop_table('memories')
    with op.batch_alter_table('installed_plugins', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_installed_plugins_user_id'))
        batch_op.drop_index(batch_op.f('ix_installed_plugins_plugin_id'))

    op.drop_table('installed_plugins')
    with op.batch_alter_table('heartbeat_reports', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_heartbeat_reports_user_id'))

    op.drop_table('heartbeat_reports')
    with op.batch_alter_table('email_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_email_messages_user_id'))
        batch_op.drop_index(batch_op.f('ix_email_messages_received_at'))
        batch_op.drop_index(batch_op.f('ix_email_messages_external_id'))

    op.drop_table('email_messages')
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_documents_user_id'))

    op.drop_table('documents')
    with op.batch_alter_table('delegations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_delegations_user_id'))

    op.drop_table('delegations')
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_conversations_user_id'))

    op.drop_table('conversations')
    with op.batch_alter_table('contacts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_contacts_user_id'))
        batch_op.drop_index(batch_op.f('ix_contacts_email'))

    op.drop_table('contacts')
    with op.batch_alter_table('channels', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_channels_user_id'))
        batch_op.drop_index(batch_op.f('ix_channels_token'))
        batch_op.drop_index(batch_op.f('ix_channels_kind'))

    op.drop_table('channels')
    with op.batch_alter_table('calendar_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_calendar_events_user_id'))
        batch_op.drop_index(batch_op.f('ix_calendar_events_start_at'))
        batch_op.drop_index(batch_op.f('ix_calendar_events_external_id'))

    op.drop_table('calendar_events')
    with op.batch_alter_table('automation_rules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_automation_rules_user_id'))

    op.drop_table('automation_rules')
    with op.batch_alter_table('assistants', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_assistants_user_id'))

    op.drop_table('assistants')
    with op.batch_alter_table('approval_grants', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_approval_grants_user_id'))
        batch_op.drop_index(batch_op.f('ix_approval_grants_tool_name'))

    op.drop_table('approval_grants')
    with op.batch_alter_table('activity_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_activity_logs_user_id'))

    op.drop_table('activity_logs')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
