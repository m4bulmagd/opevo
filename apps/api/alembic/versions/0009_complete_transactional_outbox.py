"""complete transactional outbox constraints and due-work index

Revision ID: 0009_transactional_outbox
Revises: 0008_outbox_call_lifecycle
Create Date: 2026-07-13 18:00:00
"""

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa


revision: str = "0009_transactional_outbox"
down_revision: str | None = "0008_outbox_call_lifecycle"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


_PREFLIGHT_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "legacy_phone_disable_user_reference",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM outbox_events
        WHERE topic = 'phone.disable'
          AND (
              payload ->> 'user_id' IS NULL
              OR payload ->> 'user_id' !~*
                 '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
          )
        HAVING COUNT(*) > 0
        """,
    ),
    (
        "ck_outbox_events_status_allowed",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM outbox_events
        WHERE status NOT IN ('pending', 'processing', 'delivered', 'failed')
        HAVING COUNT(*) > 0
        """,
    ),
    (
        "ck_outbox_events_attempt_count_nonnegative",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM outbox_events
        WHERE attempt_count < 0
        HAVING COUNT(*) > 0
        """,
    ),
    (
        "ck_outbox_events_delivery_consistent",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM outbox_events
        WHERE NOT (
            ((status = 'delivered' AND delivered_at IS NOT NULL
              AND last_error_code IS NULL)
             OR (status <> 'delivered' AND delivered_at IS NULL))
            AND (status <> 'failed' OR last_error_code IS NOT NULL)
        )
        HAVING COUNT(*) > 0
        """,
    ),
    (
        "ck_phone_number_provisionings_status_allowed",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM phone_number_provisionings
        WHERE status NOT IN ('queued', 'running', 'succeeded', 'failed')
        HAVING COUNT(*) > 0
        """,
    ),
    (
        "ck_phone_number_provisionings_attempt_count_nonnegative",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM phone_number_provisionings
        WHERE attempt_count < 0
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
            "Transactional outbox preflight failed: " + "; ".join(violations)
        )


def upgrade() -> None:
    if not context.is_offline_mode():
        _run_preflight(op.get_bind())
    op.execute(
        sa.text(
            """
            UPDATE outbox_events
            SET aggregate_type = 'user',
                aggregate_id = CAST(payload ->> 'user_id' AS UUID),
                payload = json_build_object('user_id', payload ->> 'user_id')
            WHERE topic = 'phone.disable'
            """
        )
    )
    op.create_check_constraint(
        op.f("ck_outbox_events_status_allowed"),
        "outbox_events",
        "status IN ('pending', 'processing', 'delivered', 'failed')",
    )
    op.create_check_constraint(
        op.f("ck_outbox_events_attempt_count_nonnegative"),
        "outbox_events",
        "attempt_count >= 0",
    )
    op.create_check_constraint(
        op.f("ck_outbox_events_delivery_consistent"),
        "outbox_events",
        "((status = 'delivered' AND delivered_at IS NOT NULL "
        "AND last_error_code IS NULL) OR "
        "(status <> 'delivered' AND delivered_at IS NULL)) "
        "AND (status <> 'failed' OR last_error_code IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_phone_number_provisionings_status_allowed"),
        "phone_number_provisionings",
        "status IN ('queued', 'running', 'succeeded', 'failed')",
    )
    op.create_check_constraint(
        op.f("ck_phone_number_provisionings_attempt_count_nonnegative"),
        "phone_number_provisionings",
        "attempt_count >= 0",
    )
    op.add_column(
        "phone_number_provisionings",
        sa.Column(
            "provider_operation_key",
            sa.String(length=255),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        op.f("uq_phone_number_provisionings_provider_operation_key"),
        "phone_number_provisionings",
        ["provider_operation_key"],
    )
    op.create_index(
        "ix_outbox_events_due_work",
        "outbox_events",
        ["status", "next_attempt_at", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_due_work", table_name="outbox_events")
    op.drop_constraint(
        op.f("uq_phone_number_provisionings_provider_operation_key"),
        "phone_number_provisionings",
        type_="unique",
    )
    op.drop_column("phone_number_provisionings", "provider_operation_key")
    op.drop_constraint(
        op.f("ck_phone_number_provisionings_attempt_count_nonnegative"),
        "phone_number_provisionings",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_phone_number_provisionings_status_allowed"),
        "phone_number_provisionings",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_outbox_events_delivery_consistent"),
        "outbox_events",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_outbox_events_attempt_count_nonnegative"),
        "outbox_events",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_outbox_events_status_allowed"),
        "outbox_events",
        type_="check",
    )
