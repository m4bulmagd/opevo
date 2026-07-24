"""add account deactivation lifecycle

Revision ID: 0015_account_deactivation
Revises: 0014_recording_egress_ops
Create Date: 2026-07-24 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa


revision: str = "0015_account_deactivation"
down_revision: str | None = "0014_recording_egress_ops"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


_USER_STATUS_CHECK = "status IN ('active','deactivating','inactive')"


def _is_sqlite() -> bool:
    return not context.is_offline_mode() and op.get_bind().dialect.name == "sqlite"


def _create_operation_table() -> None:
    op.create_table(
        "account_deactivation_operations",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("lifecycle_generation", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("phone_provider_id", sa.String(length=255), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("routing_disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subscription_canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_call_drained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("number_released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activation_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "trigger IN ('owner_request','subscription_ended')",
            name=op.f("ck_account_deactivation_operations_trigger_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','processing','attention_required','completed')",
            name=op.f("ck_account_deactivation_operations_status_allowed"),
        ),
        sa.CheckConstraint(
            "lifecycle_generation > 0",
            name=op.f("ck_account_deactivation_operations_generation_positive"),
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) OR "
            "(status <> 'completed' AND completed_at IS NULL)",
            name=op.f("ck_account_deactivation_operations_completion_consistent"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_account_deactivation_operations_attempt_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "(subscription_canceled_at IS NULL OR routing_disabled_at IS NOT NULL) "
            "AND (active_call_drained_at IS NULL OR subscription_canceled_at IS NOT NULL) "
            "AND (number_released_at IS NULL OR active_call_drained_at IS NOT NULL) "
            "AND (activation_reset_at IS NULL OR number_released_at IS NOT NULL) "
            "AND (completed_at IS NULL OR activation_reset_at IS NOT NULL)",
            name=op.f("ck_account_deactivation_operations_step_order"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_account_deactivation_operations_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_deactivation_operations")),
        sa.UniqueConstraint(
            "user_id",
            "lifecycle_generation",
            name=op.f("uq_account_deactivation_operations_user_generation"),
        ),
    )
    op.create_index(
        "uq_account_deactivation_operations_one_incomplete_user",
        "account_deactivation_operations",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("completed_at IS NULL"),
        sqlite_where=sa.text("completed_at IS NULL"),
    )


def _upgrade_sqlite() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("lifecycle_generation", sa.Integer(), nullable=True))
        batch.create_check_constraint("ck_users_status_allowed", _USER_STATUS_CHECK)
    op.execute(sa.text("UPDATE users SET lifecycle_generation = 1"))
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "lifecycle_generation",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        )

    with op.batch_alter_table("subscriptions") as batch:
        batch.add_column(sa.Column("lifecycle_generation", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cancel_at_period_end", sa.Boolean(), nullable=True))
        batch.add_column(
            sa.Column("cancellation_effective_at", sa.DateTime(timezone=True), nullable=True)
        )
    op.execute(
        sa.text(
            "UPDATE subscriptions SET lifecycle_generation = 1, "
            "cancel_at_period_end = false"
        )
    )
    with op.batch_alter_table("subscriptions") as batch:
        batch.alter_column(
            "lifecycle_generation",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        )
        batch.alter_column(
            "cancel_at_period_end",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        )


def _upgrade_non_sqlite() -> None:
    op.add_column("users", sa.Column("lifecycle_generation", sa.Integer(), nullable=True))
    op.execute(sa.text("UPDATE users SET lifecycle_generation = 1"))
    op.alter_column(
        "users",
        "lifecycle_generation",
        existing_type=sa.Integer(),
        existing_nullable=True,
        nullable=False,
        server_default=sa.text("1"),
    )
    op.create_check_constraint("ck_users_status_allowed", "users", _USER_STATUS_CHECK)

    op.add_column(
        "subscriptions",
        sa.Column("lifecycle_generation", sa.Integer(), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column(
            "cancellation_effective_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE subscriptions SET lifecycle_generation = 1, "
            "cancel_at_period_end = false"
        )
    )
    op.alter_column(
        "subscriptions",
        "lifecycle_generation",
        existing_type=sa.Integer(),
        existing_nullable=True,
        nullable=False,
        server_default=sa.text("1"),
    )
    op.alter_column(
        "subscriptions",
        "cancel_at_period_end",
        existing_type=sa.Boolean(),
        existing_nullable=True,
        nullable=False,
        server_default=sa.false(),
    )


def upgrade() -> None:
    if _is_sqlite():
        _upgrade_sqlite()
    else:
        _upgrade_non_sqlite()
    _create_operation_table()


def downgrade() -> None:
    op.drop_index(
        "uq_account_deactivation_operations_one_incomplete_user",
        table_name="account_deactivation_operations",
    )
    op.drop_table("account_deactivation_operations")

    if _is_sqlite():
        with op.batch_alter_table("subscriptions") as batch:
            batch.drop_column("cancellation_effective_at")
            batch.drop_column("cancel_at_period_end")
            batch.drop_column("lifecycle_generation")
        with op.batch_alter_table("users") as batch:
            batch.drop_constraint("ck_users_status_allowed", type_="check")
            batch.drop_column("lifecycle_generation")
        return

    op.drop_column("subscriptions", "cancellation_effective_at")
    op.drop_column("subscriptions", "cancel_at_period_end")
    op.drop_column("subscriptions", "lifecycle_generation")
    op.drop_constraint("ck_users_status_allowed", "users", type_="check")
    op.drop_column("users", "lifecycle_generation")
