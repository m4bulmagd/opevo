"""add outbox foundation and subscription lifecycle constraints

Revision ID: 0008_outbox_call_lifecycle
Revises: 0007_production_integrity
Create Date: 2026-07-13 15:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0008_outbox_call_lifecycle"
down_revision: str | None = "0007_production_integrity"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


_PREFLIGHT_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "ck_subscriptions_status_allowed",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM subscriptions
        WHERE status NOT IN (
            'trialing', 'active', 'past_due', 'unpaid', 'canceled',
            'incomplete', 'incomplete_expired', 'paused'
        )
        HAVING COUNT(*) > 0
        """,
    ),
    (
        "ck_subscriptions_plan_tier_allowed",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM subscriptions
        WHERE plan_tier <> 'starter'
        HAVING COUNT(*) > 0
        """,
    ),
)


def _run_preflight(connection) -> None:
    violations: list[str] = []
    for condition_name, query in _PREFLIGHT_QUERIES:
        rows = connection.execute(sa.text(query)).mappings().all()
        if not rows:
            continue
        identities = ", ".join(
            f"identity={row['identity']}, count={row['duplicate_count']}"
            for row in rows
        )
        violations.append(f"{condition_name}: {identities}")

    if violations:
        raise RuntimeError(
            "Subscription lifecycle preflight failed: " + "; ".join(violations)
        )


def upgrade() -> None:
    _run_preflight(op.get_bind())

    op.add_column(
        "subscriptions",
        sa.Column(
            "stripe_subscription_created_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "subscriptions",
        sa.Column(
            "last_stripe_event_created_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_table(
        "outbox_events",
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("topic", sa.String(length=100), nullable=False),
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_outbox_events_idempotency_key",
        ),
    )
    op.create_index(
        "ix_outbox_events_topic",
        "outbox_events",
        ["topic"],
        unique=False,
    )
    op.create_index(
        "ix_outbox_events_aggregate_id",
        "outbox_events",
        ["aggregate_id"],
        unique=False,
    )
    op.create_index(
        "ix_outbox_events_status",
        "outbox_events",
        ["status"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_subscriptions_status_allowed",
        "subscriptions",
        "status IN ('trialing', 'active', 'past_due', 'unpaid', "
        "'canceled', 'incomplete', 'incomplete_expired', 'paused')",
    )
    op.create_check_constraint(
        "ck_subscriptions_plan_tier_allowed",
        "subscriptions",
        "plan_tier = 'starter'",
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "last_stripe_event_created_at")
    op.drop_column("subscriptions", "stripe_subscription_created_at")
    op.drop_constraint(
        "ck_subscriptions_plan_tier_allowed",
        "subscriptions",
        type_="check",
    )
    op.drop_constraint(
        "ck_subscriptions_status_allowed",
        "subscriptions",
        type_="check",
    )
    op.drop_index("ix_outbox_events_status", table_name="outbox_events")
    op.drop_index("ix_outbox_events_aggregate_id", table_name="outbox_events")
    op.drop_index("ix_outbox_events_topic", table_name="outbox_events")
    op.drop_table("outbox_events")
