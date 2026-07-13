"""add production integrity constraints

Revision ID: 0007_production_integrity
Revises: 0006_phone_number_provisioning
Create Date: 2026-07-13 12:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0007_production_integrity"
down_revision: str | None = "0006_phone_number_provisioning"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


_PREFLIGHT_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "uq_webhook_events_provider_external_event_id",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM webhook_events
        GROUP BY provider, external_event_id
        HAVING COUNT(*) > 1
        ORDER BY identity
        LIMIT 10
        """,
    ),
    (
        "uq_usage_ledgers_call_event_type",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM usage_ledgers
        WHERE call_id IS NOT NULL
        GROUP BY call_id, event_type
        HAVING COUNT(*) > 1
        ORDER BY identity
        LIMIT 10
        """,
    ),
    (
        "uq_call_messages_call_sequence",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM call_messages
        GROUP BY call_id, sequence_number
        HAVING COUNT(*) > 1
        ORDER BY identity
        LIMIT 10
        """,
    ),
    (
        "uq_subscriptions_user_id",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM subscriptions
        GROUP BY user_id
        HAVING COUNT(*) > 1
        ORDER BY identity
        LIMIT 10
        """,
    ),
    (
        "uq_calls_user_active",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM calls
        WHERE status IN ('pending', 'connected', 'ending', 'finalizing')
        GROUP BY user_id
        HAVING COUNT(*) > 1
        ORDER BY identity
        LIMIT 10
        """,
    ),
    (
        "ck_subscriptions_allocated_minutes_nonnegative",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM subscriptions
        WHERE allocated_minutes < 0
        HAVING COUNT(*) > 0
        """,
    ),
    (
        "ck_calls_duration_seconds_nonnegative",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM calls
        WHERE duration_seconds < 0
        HAVING COUNT(*) > 0
        """,
    ),
    (
        "ck_calls_minutes_charged_nonnegative",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM calls
        WHERE minutes_charged < 0
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
            "Production integrity preflight failed: " + "; ".join(violations)
        )


def upgrade() -> None:
    # source_id is introduced below as nullable, so it cannot contain legacy
    # duplicates. Every condition possible in the pre-migration schema is
    # checked before the first DDL statement.
    _run_preflight(op.get_bind())

    op.add_column(
        "usage_ledgers",
        sa.Column("source_id", sa.String(length=255), nullable=True),
    )
    op.create_unique_constraint(
        "uq_webhook_events_provider_external_event_id",
        "webhook_events",
        ["provider", "external_event_id"],
    )
    op.create_index(
        "uq_usage_ledgers_call_event_type",
        "usage_ledgers",
        ["call_id", "event_type"],
        unique=True,
        postgresql_where=sa.text("call_id IS NOT NULL"),
    )
    op.create_index(
        "uq_usage_ledgers_event_source",
        "usage_ledgers",
        ["event_type", "source_id"],
        unique=True,
        postgresql_where=sa.text("source_id IS NOT NULL"),
    )
    op.create_unique_constraint(
        "uq_call_messages_call_sequence",
        "call_messages",
        ["call_id", "sequence_number"],
    )
    op.create_unique_constraint(
        "uq_subscriptions_user_id",
        "subscriptions",
        ["user_id"],
    )
    op.create_index(
        "uq_calls_user_active",
        "calls",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending', 'connected', 'ending', 'finalizing')"
        ),
    )
    op.create_check_constraint(
        op.f("ck_subscriptions_allocated_minutes_nonnegative"),
        "subscriptions",
        "allocated_minutes >= 0",
    )
    op.create_check_constraint(
        op.f("ck_calls_duration_seconds_nonnegative"),
        "calls",
        "duration_seconds IS NULL OR duration_seconds >= 0",
    )
    op.create_check_constraint(
        op.f("ck_calls_minutes_charged_nonnegative"),
        "calls",
        "minutes_charged IS NULL OR minutes_charged >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_calls_minutes_charged_nonnegative"),
        "calls",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_calls_duration_seconds_nonnegative"),
        "calls",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_subscriptions_allocated_minutes_nonnegative"),
        "subscriptions",
        type_="check",
    )
    op.drop_index("uq_calls_user_active", table_name="calls")
    op.drop_constraint(
        "uq_subscriptions_user_id",
        "subscriptions",
        type_="unique",
    )
    op.drop_constraint(
        "uq_call_messages_call_sequence",
        "call_messages",
        type_="unique",
    )
    op.drop_index("uq_usage_ledgers_event_source", table_name="usage_ledgers")
    op.drop_index("uq_usage_ledgers_call_event_type", table_name="usage_ledgers")
    op.drop_constraint(
        "uq_webhook_events_provider_external_event_id",
        "webhook_events",
        type_="unique",
    )
    op.drop_column("usage_ledgers", "source_id")
