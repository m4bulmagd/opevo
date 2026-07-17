"""add customer activation domain

Revision ID: 0012_customer_activation
Revises: 0011_call_state_machine
Create Date: 2026-07-17 12:00:00
"""

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa


revision: str = "0012_customer_activation"
down_revision: str | None = "0011_call_state_machine"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _run_preflight(connection) -> None:
    duplicates = connection.execute(
        sa.text(
            "SELECT COUNT(*) AS duplicate_groups FROM ("
            "SELECT user_id FROM phone_numbers GROUP BY user_id HAVING COUNT(*) > 1"
            ") duplicate_users"
        )
    ).scalar_one()
    if duplicates:
        raise RuntimeError(
            "Cannot add uq_phone_numbers_user_id: "
            f"duplicate_user_groups={duplicates}"
        )


def upgrade() -> None:
    if not context.is_offline_mode():
        _run_preflight(op.get_bind())

    op.create_table(
        "business_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("owner_name", sa.String(length=100), nullable=True),
        sa.Column("business_name", sa.String(length=100), nullable=True),
        sa.Column("business_type", sa.String(length=100), nullable=True),
        sa.Column("public_description", sa.Text(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("business_hours", sa.JSON(), nullable=True),
        sa.Column("existing_phone_e164", sa.String(length=32), nullable=True),
        sa.Column("detected_carrier", sa.String(length=100), nullable=True),
        sa.Column("detected_number_type", sa.String(length=32), nullable=True),
        sa.Column("carrier_lookup_status", sa.String(length=32), nullable=True),
        sa.Column("carrier_looked_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_carrier", sa.String(length=32), nullable=True),
        sa.Column("receptionist_name", sa.String(length=100), nullable=True),
        sa.Column("faqs", sa.JSON(), nullable=False),
        sa.Column("special_instructions", sa.Text(), nullable=True),
        sa.Column("escalation_notes", sa.Text(), nullable=True),
        sa.Column("content_revision", sa.Integer(), nullable=False),
        sa.Column("routing_revision", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.CheckConstraint(
            "content_revision >= 1",
            name=op.f("ck_business_profiles_content_revision_positive"),
        ),
        sa.CheckConstraint(
            "routing_revision >= 1",
            name=op.f("ck_business_profiles_routing_revision_positive"),
        ),
        sa.CheckConstraint(
            "confirmed_carrier IS NULL OR confirmed_carrier IN "
            "('orange', 'sfr', 'bouygues', 'free', 'other')",
            name=op.f("ck_business_profiles_confirmed_carrier_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_business_profiles_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_profiles")),
        sa.UniqueConstraint(
            "user_id",
            name=op.f("uq_business_profiles_user_id"),
        ),
    )
    op.create_index(
        op.f("ix_business_profiles_user_id"),
        "business_profiles",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "customer_activations",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("profile_confirmed_revision", sa.Integer(), nullable=True),
        sa.Column("profile_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "provisioning_consented_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "provisioning_idempotency_key",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "verification_window_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "verification_window_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("verification_session_id", sa.String(length=255), nullable=True),
        sa.Column(
            "verification_claimed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("verification_dispatch_id", sa.String(length=255), nullable=True),
        sa.Column(
            "verification_routing_fingerprint",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column(
            "verified_routing_fingerprint",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "forwarding_verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("go_live_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("go_live_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_code", sa.String(length=100), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.CheckConstraint(
            "workflow_version >= 1",
            name=op.f("ck_customer_activations_workflow_version_positive"),
        ),
        sa.CheckConstraint(
            "verification_status IN "
            "('not_started', 'open', 'claimed', 'succeeded', 'failed', "
            "'expired', 'invalidated')",
            name=op.f("ck_customer_activations_verification_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_customer_activations_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_activations")),
        sa.UniqueConstraint(
            "provisioning_idempotency_key",
            name=op.f("uq_customer_activations_provisioning_idempotency_key"),
        ),
        sa.UniqueConstraint(
            "user_id",
            name=op.f("uq_customer_activations_user_id"),
        ),
        sa.UniqueConstraint(
            "verification_dispatch_id",
            name=op.f("uq_customer_activations_verification_dispatch_id"),
        ),
        sa.UniqueConstraint(
            "verification_session_id",
            name=op.f("uq_customer_activations_verification_session_id"),
        ),
    )
    op.create_index(
        op.f("ix_customer_activations_user_id"),
        "customer_activations",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "activation_events",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("activation_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["activation_id"],
            ["customer_activations.id"],
            name=op.f("fk_activation_events_activation_id_customer_activations"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_activation_events_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activation_events")),
        sa.UniqueConstraint(
            "idempotency_key",
            name=op.f("uq_activation_events_idempotency_key"),
        ),
    )
    op.create_index(
        op.f("ix_activation_events_activation_id"),
        "activation_events",
        ["activation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_activation_events_event_type"),
        "activation_events",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_activation_events_user_id"),
        "activation_events",
        ["user_id"],
        unique=False,
    )

    op.add_column(
        "agent_configs",
        sa.Column("business_display_name", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "agent_configs",
        sa.Column(
            "profile_projection_revision",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_phone_numbers_user_id",
        "phone_numbers",
        ["user_id"],
    )

    op.execute(
        sa.text(
            """
            INSERT INTO business_profiles
                (id, user_id, faqs, content_revision, routing_revision,
                 created_at, updated_at)
            SELECT id, id, '[]', 1, 1, now(), now()
            FROM users
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO customer_activations
                (id, user_id, workflow_version, verification_status,
                 created_at, updated_at)
            SELECT id, id, 1, 'not_started', now(), now()
            FROM users
            """
        )
    )
    op.alter_column(
        "agent_configs",
        "profile_projection_revision",
        existing_type=sa.Integer(),
        server_default=None,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_activation_events_user_id"),
        table_name="activation_events",
    )
    op.drop_index(
        op.f("ix_activation_events_event_type"),
        table_name="activation_events",
    )
    op.drop_index(
        op.f("ix_activation_events_activation_id"),
        table_name="activation_events",
    )
    op.drop_table("activation_events")
    op.drop_index(
        op.f("ix_customer_activations_user_id"),
        table_name="customer_activations",
    )
    op.drop_table("customer_activations")
    op.drop_index(
        op.f("ix_business_profiles_user_id"),
        table_name="business_profiles",
    )
    op.drop_table("business_profiles")
    op.drop_constraint(
        "uq_phone_numbers_user_id",
        "phone_numbers",
        type_="unique",
    )
    op.drop_column("agent_configs", "profile_projection_revision")
    op.drop_column("agent_configs", "business_display_name")
