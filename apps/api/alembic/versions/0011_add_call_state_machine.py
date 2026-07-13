"""add durable call state machine and reconciliation fields

Revision ID: 0011_call_state_machine
Revises: 0010_durable_livekit_dispatch
Create Date: 2026-07-13 22:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0011_call_state_machine"
down_revision: str | None = "0010_durable_livekit_dispatch"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


_PREFLIGHT_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "call_status_allowed",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM calls
        WHERE status NOT IN (
            'pending', 'connected', 'ending', 'finalizing', 'completed', 'failed'
        )
        HAVING COUNT(*) > 0
        """,
    ),
    (
        "notification_call_type_identity",
        """
        SELECT MIN(CAST(id AS VARCHAR)) AS identity,
               COUNT(*) AS duplicate_count
        FROM notifications
        WHERE call_id IS NOT NULL
        GROUP BY call_id, notification_type
        HAVING COUNT(*) > 1
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
            "Call state-machine preflight failed: " + "; ".join(violations)
        )


def upgrade() -> None:
    _run_preflight(op.get_bind())

    op.add_column(
        "calls",
        sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "calls",
        sa.Column(
            "finalization_attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "calls",
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "calls",
        sa.Column("summary_transcript_max_sequence", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE calls
            SET state_changed_at = CASE
                WHEN status IN ('completed', 'failed')
                    THEN COALESCE(ended_at, updated_at, created_at)
                WHEN status IN ('ending', 'finalizing')
                    THEN COALESCE(updated_at, started_at, created_at)
                WHEN status = 'connected'
                    THEN COALESCE(started_at, updated_at, created_at)
                ELSE COALESCE(created_at, updated_at)
            END,
            failure_code = CASE
                WHEN status = 'failed' THEN COALESCE(failure_code, 'legacy_failure')
                ELSE NULL
            END
            """
        )
    )
    op.alter_column(
        "calls",
        "state_changed_at",
        nullable=False,
        server_default=sa.func.now(),
    )
    op.create_check_constraint(
        op.f("ck_calls_status_allowed"),
        "calls",
        "status IN ('pending', 'connected', 'ending', 'finalizing', "
        "'completed', 'failed')",
    )
    op.create_check_constraint(
        op.f("ck_calls_finalization_attempt_count_nonnegative"),
        "calls",
        "finalization_attempt_count >= 0",
    )
    op.create_check_constraint(
        op.f("ck_calls_summary_transcript_max_sequence_nonnegative"),
        "calls",
        "summary_transcript_max_sequence IS NULL OR "
        "summary_transcript_max_sequence >= 0",
    )
    op.create_check_constraint(
        op.f("ck_calls_failure_status_consistent"),
        "calls",
        "(status = 'failed' AND failure_code IS NOT NULL) OR "
        "(status <> 'failed' AND failure_code IS NULL)",
    )
    op.create_index(
        "ix_calls_reconciliation_stale_work",
        "calls",
        ["status", "state_changed_at", "last_reconciled_at"],
        unique=False,
        postgresql_where=sa.text(
            "status IN ('pending', 'connected', 'ending', 'finalizing')"
        ),
    )
    op.create_unique_constraint(
        op.f("uq_notifications_call_notification_type"),
        "notifications",
        ["call_id", "notification_type"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_calls_summary_transcript_max_sequence_nonnegative"),
        "calls",
        type_="check",
    )
    op.drop_constraint(
        op.f("uq_notifications_call_notification_type"),
        "notifications",
        type_="unique",
    )
    op.drop_index("ix_calls_reconciliation_stale_work", table_name="calls")
    op.drop_constraint(
        op.f("ck_calls_failure_status_consistent"),
        "calls",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_calls_finalization_attempt_count_nonnegative"),
        "calls",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_calls_status_allowed"),
        "calls",
        type_="check",
    )
    op.drop_column("calls", "last_reconciled_at")
    op.drop_column("calls", "finalization_attempt_count")
    op.drop_column("calls", "summary_transcript_max_sequence")
    op.drop_column("calls", "state_changed_at")
