"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-03-13 01:55:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("clerk_user_id", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("clerk_user_id", name=op.f("uq_users_clerk_user_id")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_clerk_user_id"), "users", ["clerk_user_id"], unique=False)

    op.create_table(
        "agent_configs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_name", sa.String(length=255), nullable=False),
        sa.Column("owner_context", sa.Text(), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("knowledge_base", sa.Text(), nullable=False),
        sa.Column("pipeline_mode", sa.String(length=50), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_agent_configs_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_configs")),
        sa.UniqueConstraint("user_id", name=op.f("uq_agent_configs_user_id")),
    )
    op.create_index(op.f("ix_agent_configs_user_id"), "agent_configs", ["user_id"], unique=False)

    op.create_table(
        "phone_numbers",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("e164", sa.String(length=32), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_number_id", sa.String(length=255), nullable=True),
        sa.Column("provider_connection_name", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_phone_numbers_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_phone_numbers")),
        sa.UniqueConstraint("e164", name=op.f("uq_phone_numbers_e164")),
        sa.UniqueConstraint("provider_number_id", name=op.f("uq_phone_numbers_provider_number_id")),
    )
    op.create_index(op.f("ix_phone_numbers_e164"), "phone_numbers", ["e164"], unique=False)
    op.create_index(op.f("ix_phone_numbers_user_id"), "phone_numbers", ["user_id"], unique=False)

    op.create_table(
        "subscriptions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("plan_tier", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_subscriptions_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
        sa.UniqueConstraint("stripe_customer_id", name=op.f("uq_subscriptions_stripe_customer_id")),
        sa.UniqueConstraint("stripe_subscription_id", name=op.f("uq_subscriptions_stripe_subscription_id")),
    )
    op.create_index(op.f("ix_subscriptions_user_id"), "subscriptions", ["user_id"], unique=False)

    op.create_table(
        "calls",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("phone_number_id", sa.Uuid(), nullable=True),
        sa.Column("livekit_room_id", sa.String(length=255), nullable=True),
        sa.Column("caller_number", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("minutes_charged", sa.Integer(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["phone_number_id"], ["phone_numbers.id"], name=op.f("fk_calls_phone_number_id_phone_numbers")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_calls_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calls")),
        sa.UniqueConstraint("livekit_room_id", name=op.f("uq_calls_livekit_room_id")),
    )
    op.create_index(op.f("ix_calls_phone_number_id"), "calls", ["phone_number_id"], unique=False)
    op.create_index(op.f("ix_calls_user_id"), "calls", ["user_id"], unique=False)

    op.create_table(
        "call_messages",
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("speaker", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], name=op.f("fk_call_messages_call_id_calls")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_call_messages")),
    )
    op.create_index(op.f("ix_call_messages_call_id"), "call_messages", ["call_id"], unique=False)

    op.create_table(
        "notifications",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=True),
        sa.Column("notification_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], name=op.f("fk_notifications_call_id_calls")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_notifications_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index(op.f("ix_notifications_call_id"), "notifications", ["call_id"], unique=False)
    op.create_index(op.f("ix_notifications_user_id"), "notifications", ["user_id"], unique=False)

    op.create_table(
        "usage_ledgers",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("minutes_delta", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], name=op.f("fk_usage_ledgers_call_id_calls")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_usage_ledgers_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_ledgers")),
    )
    op.create_index(op.f("ix_usage_ledgers_call_id"), "usage_ledgers", ["call_id"], unique=False)
    op.create_index(op.f("ix_usage_ledgers_user_id"), "usage_ledgers", ["user_id"], unique=False)

    op.create_table(
        "webhook_events",
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_events")),
    )
    op.create_index(
        "ix_webhook_events_provider_external_event_id",
        "webhook_events",
        ["provider", "external_event_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_events_provider_external_event_id", table_name="webhook_events")
    op.drop_table("webhook_events")
    op.drop_index(op.f("ix_usage_ledgers_user_id"), table_name="usage_ledgers")
    op.drop_index(op.f("ix_usage_ledgers_call_id"), table_name="usage_ledgers")
    op.drop_table("usage_ledgers")
    op.drop_index(op.f("ix_notifications_user_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_call_id"), table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(op.f("ix_call_messages_call_id"), table_name="call_messages")
    op.drop_table("call_messages")
    op.drop_index(op.f("ix_calls_user_id"), table_name="calls")
    op.drop_index(op.f("ix_calls_phone_number_id"), table_name="calls")
    op.drop_table("calls")
    op.drop_index(op.f("ix_subscriptions_user_id"), table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index(op.f("ix_phone_numbers_user_id"), table_name="phone_numbers")
    op.drop_index(op.f("ix_phone_numbers_e164"), table_name="phone_numbers")
    op.drop_table("phone_numbers")
    op.drop_index(op.f("ix_agent_configs_user_id"), table_name="agent_configs")
    op.drop_table("agent_configs")
    op.drop_index(op.f("ix_users_clerk_user_id"), table_name="users")
    op.drop_table("users")
