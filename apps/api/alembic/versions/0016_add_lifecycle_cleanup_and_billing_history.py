"""Add durable lifecycle cleanup and billing cycle history.

Revision ID: 0016_lifecycle_cleanup
Revises: 0015_account_deactivation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision = "0016_lifecycle_cleanup"
down_revision = "0015_account_deactivation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "billing_checkout_attempts",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("lifecycle_generation", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("stripe_checkout_session_id", sa.String(length=255)),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "lifecycle_generation >= 1",
            name="ck_billing_checkout_attempts_generation_positive",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed')",
            name="ck_billing_checkout_attempts_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_billing_checkout_attempts_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_billing_checkout_attempts"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_billing_checkout_attempts_idempotency_key",
        ),
        sa.UniqueConstraint(
            "stripe_checkout_session_id",
            name="uq_billing_checkout_attempts_stripe_checkout_session_id",
        ),
        sa.UniqueConstraint(
            "user_id",
            "lifecycle_generation",
            name="uq_billing_checkout_attempts_user_generation",
        ),
    )
    op.create_index(
        "ix_billing_checkout_attempts_user_id",
        "billing_checkout_attempts",
        ["user_id"],
    )

    op.create_table(
        "provider_cleanup_operations",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("lifecycle_generation", sa.Integer(), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("provider_resource_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("routing_disabled_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=64)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "resource_type IN ('phone_number', 'stripe_subscription')",
            name="ck_provider_cleanup_operations_resource_type_allowed",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'attention_required', 'completed')",
            name="ck_provider_cleanup_operations_status_allowed",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) OR "
            "(status <> 'completed' AND completed_at IS NULL)",
            name="ck_provider_cleanup_operations_completion_consistent",
        ),
        sa.CheckConstraint(
            "lifecycle_generation >= 1",
            name="ck_provider_cleanup_operations_generation_positive",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_provider_cleanup_operations_attempt_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_provider_cleanup_operations_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_provider_cleanup_operations"),
        sa.UniqueConstraint(
            "resource_type",
            "provider_resource_id",
            name="uq_provider_cleanup_operations_resource",
        ),
    )
    op.create_index(
        "ix_provider_cleanup_operations_user_id",
        "provider_cleanup_operations",
        ["user_id"],
    )

    op.create_table(
        "subscription_cycle_history",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("lifecycle_generation", sa.Integer(), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=False),
        sa.Column("plan_tier", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("allocated_minutes", sa.Integer(), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True)),
        sa.Column("current_period_end", sa.DateTime(timezone=True)),
        sa.Column("stripe_subscription_created_at", sa.DateTime(timezone=True)),
        sa.Column("last_stripe_event_created_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("cancellation_effective_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_subscription_cycle_history_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_subscription_cycle_history"),
        sa.UniqueConstraint(
            "stripe_subscription_id",
            name="uq_subscription_cycle_history_stripe_subscription_id",
        ),
        sa.UniqueConstraint(
            "user_id",
            "lifecycle_generation",
            name="uq_subscription_cycle_history_user_generation",
        ),
    )
    op.create_index(
        "ix_subscription_cycle_history_user_id",
        "subscription_cycle_history",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_subscription_cycle_history_user_id",
        table_name="subscription_cycle_history",
    )
    op.drop_table("subscription_cycle_history")
    op.drop_index(
        "ix_provider_cleanup_operations_user_id",
        table_name="provider_cleanup_operations",
    )
    op.drop_table("provider_cleanup_operations")
    op.drop_index(
        "ix_billing_checkout_attempts_user_id",
        table_name="billing_checkout_attempts",
    )
    op.drop_table("billing_checkout_attempts")
