"""add durable recording egress operations

Revision ID: 0014_recording_egress_ops
Revises: 0013_outbox_routing_target
Create Date: 2026-07-19 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "0014_recording_egress_ops"
down_revision: str | None = "0013_outbox_routing_target"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


_ACTIVE_CALL_STATES = ("pending", "connected", "ending", "finalizing")
_TERMINAL_CALL_STATES = ("completed", "failed")


def _legacy_calls() -> sa.TableClause:
    return sa.table(
        "calls",
        sa.column("id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("livekit_room_id", sa.String(255)),
        sa.column("status", sa.String(50)),
        sa.column("ended_at", sa.DateTime(timezone=True)),
        sa.column("deleted_at", sa.DateTime(timezone=True)),
        sa.column("recording_object_key", sa.String(512)),
        sa.column("recording_egress_id", sa.String(255)),
        sa.column("recording_url", sa.String(512)),
    )


def _recording_operations() -> sa.TableClause:
    return sa.table(
        "recording_egress_operations",
        sa.column("id", sa.Uuid()),
        sa.column("call_id", sa.Uuid()),
        sa.column("room_name", sa.String(255)),
        sa.column("legacy_incomplete", sa.Boolean()),
        sa.column("expected_object_key", sa.String(512)),
        sa.column("provider_egress_id", sa.String(255)),
        sa.column("start_state", sa.String(32)),
        sa.column("start_attempted_at", sa.DateTime(timezone=True)),
        sa.column("stop_requested_at", sa.DateTime(timezone=True)),
        sa.column("delete_requested_at", sa.DateTime(timezone=True)),
        sa.column("provider_terminal_at", sa.DateTime(timezone=True)),
        sa.column("object_deleted_at", sa.DateTime(timezone=True)),
        sa.column("last_reconciled_at", sa.DateTime(timezone=True)),
        sa.column("last_error_code", sa.String(100)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _outbox_events() -> sa.TableClause:
    return sa.table(
        "outbox_events",
        sa.column("id", sa.Uuid()),
        sa.column("idempotency_key", sa.String(255)),
        sa.column("topic", sa.String(100)),
        sa.column("aggregate_type", sa.String(100)),
        sa.column("aggregate_id", sa.Uuid()),
        sa.column("payload", sa.JSON()),
        sa.column("status", sa.String(32)),
        sa.column("attempt_count", sa.Integer()),
        sa.column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.column("last_error_code", sa.String(100)),
        sa.column("routing_target_provider_number_id", sa.String(255)),
        sa.column("delivered_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _replacement_suffix(row: sa.RowMapping) -> str | None:
    if row["deleted_at"] is not None:
        return "delete"
    if row["status"] in _TERMINAL_CALL_STATES:
        return "stop"
    if row["status"] in _ACTIVE_CALL_STATES and row["recording_egress_id"] in (
        None,
        "",
    ):
        return "start"
    return None


def _backfill_sqlite(bind: Connection) -> None:
    calls = _legacy_calls()
    operations = _recording_operations()
    outbox = _outbox_events()
    rows = (
        bind.execute(
            sa.select(
                calls.c.id,
                calls.c.user_id,
                calls.c.livekit_room_id,
                calls.c.status,
                calls.c.ended_at,
                calls.c.deleted_at,
                calls.c.recording_object_key,
                calls.c.recording_egress_id,
                calls.c.recording_url,
            ).where(
                sa.or_(
                    sa.and_(
                        calls.c.recording_object_key.is_not(None),
                        calls.c.recording_object_key != "",
                    ),
                    sa.and_(
                        calls.c.recording_egress_id.is_not(None),
                        calls.c.recording_egress_id != "",
                    ),
                    sa.and_(
                        calls.c.recording_url.is_not(None),
                        calls.c.recording_url != "",
                    ),
                )
            )
        )
        .mappings()
        .all()
    )
    migrated_at = datetime.now(UTC)

    for row in rows:
        operation_id = row["id"]
        is_deleted = row["deleted_at"] is not None
        is_terminal = row["status"] in _TERMINAL_CALL_STATES
        room_name = row["livekit_room_id"] or None
        provider_egress_id = row["recording_egress_id"] or None
        expected_object_key = row["recording_object_key"] or (
            f"calls/{row['user_id']}/{row['id']}.ogg"
        )
        start_state = "started" if provider_egress_id is not None else "uncertain"
        stop_requested_at = None
        if is_terminal or is_deleted:
            stop_requested_at = row["ended_at"] or row["deleted_at"] or migrated_at
        bind.execute(
            operations.insert().values(
                id=operation_id,
                call_id=row["id"],
                room_name=room_name,
                legacy_incomplete=room_name is None,
                expected_object_key=expected_object_key,
                provider_egress_id=provider_egress_id,
                start_state=start_state,
                start_attempted_at=None,
                stop_requested_at=stop_requested_at,
                delete_requested_at=row["deleted_at"] if is_deleted else None,
                provider_terminal_at=None,
                object_deleted_at=None,
                last_reconciled_at=None,
                last_error_code=None,
                created_at=migrated_at,
                updated_at=migrated_at,
            )
        )

        suffix = _replacement_suffix(row)
        if suffix is None:
            continue
        bind.execute(
            outbox.insert().values(
                id=uuid4(),
                idempotency_key=f"recording.reconcile:{operation_id}:{suffix}",
                topic="recording.reconcile",
                aggregate_type="recording-egress-operation",
                aggregate_id=operation_id,
                payload={"operation_id": str(operation_id)},
                status="pending",
                attempt_count=0,
                next_attempt_at=migrated_at,
                last_error_code=None,
                routing_target_provider_number_id=None,
                delivered_at=None,
                created_at=migrated_at,
                updated_at=migrated_at,
            )
        )
        bind.execute(
            outbox.delete().where(
                outbox.c.topic == "recording.stop",
                outbox.c.aggregate_id == row["id"],
                outbox.c.status.in_(("pending", "processing")),
            )
        )


def _backfill_postgresql() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO recording_egress_operations (
                id,
                call_id,
                room_name,
                legacy_incomplete,
                expected_object_key,
                provider_egress_id,
                start_state,
                start_attempted_at,
                stop_requested_at,
                delete_requested_at,
                provider_terminal_at,
                object_deleted_at,
                last_reconciled_at,
                last_error_code,
                created_at,
                updated_at
            )
            SELECT
                calls.id,
                calls.id,
                NULLIF(calls.livekit_room_id, ''),
                NULLIF(calls.livekit_room_id, '') IS NULL,
                COALESCE(
                    NULLIF(calls.recording_object_key, ''),
                    'calls/' || calls.user_id::text || '/' || calls.id::text || '.ogg'
                ),
                NULLIF(calls.recording_egress_id, ''),
                CASE
                    WHEN NULLIF(calls.recording_egress_id, '') IS NOT NULL
                    THEN 'started'
                    ELSE 'uncertain'
                END,
                NULL,
                CASE
                    WHEN calls.deleted_at IS NOT NULL
                        OR calls.status IN ('completed', 'failed')
                    THEN COALESCE(calls.ended_at, calls.deleted_at, CURRENT_TIMESTAMP)
                    ELSE NULL
                END,
                CASE
                    WHEN calls.deleted_at IS NOT NULL THEN calls.deleted_at
                    ELSE NULL
                END,
                NULL,
                NULL,
                NULL,
                NULL,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM calls
            WHERE NULLIF(calls.recording_object_key, '') IS NOT NULL
                OR NULLIF(calls.recording_egress_id, '') IS NOT NULL
                OR NULLIF(calls.recording_url, '') IS NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO outbox_events (
                id,
                idempotency_key,
                topic,
                aggregate_type,
                aggregate_id,
                payload,
                status,
                attempt_count,
                next_attempt_at,
                last_error_code,
                routing_target_provider_number_id,
                delivered_at,
                created_at,
                updated_at
            )
            SELECT
                gen_random_uuid(),
                'recording.reconcile:' || operations.id::text || ':' ||
                    CASE
                        WHEN calls.deleted_at IS NOT NULL THEN 'delete'
                        WHEN calls.status IN ('completed', 'failed') THEN 'stop'
                        ELSE 'start'
                    END,
                'recording.reconcile',
                'recording-egress-operation',
                operations.id,
                json_build_object('operation_id', operations.id::text),
                'pending',
                0,
                CURRENT_TIMESTAMP,
                NULL,
                NULL,
                NULL,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM calls
            JOIN recording_egress_operations AS operations
                ON operations.call_id = calls.id
            WHERE calls.deleted_at IS NOT NULL
                OR calls.status IN ('completed', 'failed')
                OR (
                    calls.status IN ('pending', 'connected', 'ending', 'finalizing')
                    AND NULLIF(calls.recording_egress_id, '') IS NULL
                )
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM outbox_events AS legacy
            USING calls
            WHERE legacy.topic = 'recording.stop'
                AND legacy.aggregate_id = calls.id
                AND legacy.status IN ('pending', 'processing')
                AND (
                    calls.deleted_at IS NOT NULL
                    OR calls.status IN ('completed', 'failed')
                    OR (
                        calls.status IN ('pending', 'connected', 'ending', 'finalizing')
                        AND NULLIF(calls.recording_egress_id, '') IS NULL
                    )
                )
                AND (
                    NULLIF(calls.recording_object_key, '') IS NOT NULL
                    OR NULLIF(calls.recording_egress_id, '') IS NOT NULL
                    OR NULLIF(calls.recording_url, '') IS NOT NULL
                )
            """
        )
    )


def upgrade() -> None:
    op.create_table(
        "recording_egress_operations",
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("room_name", sa.String(length=255), nullable=True),
        sa.Column(
            "legacy_incomplete",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("expected_object_key", sa.String(length=512), nullable=False),
        sa.Column("provider_egress_id", sa.String(length=255), nullable=True),
        sa.Column("start_state", sa.String(length=32), nullable=False),
        sa.Column("start_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delete_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("object_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
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
            "start_state IN ('prepared','starting','started','not_started','uncertain')",
            name=op.f("ck_recording_egress_operations_start_state_allowed"),
        ),
        sa.CheckConstraint(
            "(start_state = 'started' AND provider_egress_id IS NOT NULL) OR "
            "(start_state <> 'started' AND provider_egress_id IS NULL)",
            name=op.f("ck_recording_egress_operations_provider_identity_consistent"),
        ),
        sa.CheckConstraint(
            "(legacy_incomplete = false AND room_name IS NOT NULL) OR "
            "(legacy_incomplete = true AND room_name IS NULL AND "
            "start_state IN ('started','uncertain'))",
            name=op.f("ck_recording_egress_operations_legacy_room_consistent"),
        ),
        sa.CheckConstraint(
            "start_state <> 'prepared' OR start_attempted_at IS NULL",
            name=op.f("ck_recording_egress_operations_prepared_attempt_consistent"),
        ),
        sa.CheckConstraint(
            "delete_requested_at IS NULL OR stop_requested_at IS NOT NULL",
            name=op.f("ck_recording_egress_operations_delete_implies_stop"),
        ),
        sa.CheckConstraint(
            "object_deleted_at IS NULL OR delete_requested_at IS NOT NULL",
            name=op.f("ck_recording_egress_operations_object_delete_implies_request"),
        ),
        sa.ForeignKeyConstraint(
            ["call_id"],
            ["calls.id"],
            name=op.f("fk_recording_egress_operations_call_id_calls"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_recording_egress_operations"),
        ),
        sa.UniqueConstraint(
            "call_id",
            name=op.f("uq_recording_egress_operations_call_id"),
        ),
        sa.UniqueConstraint(
            "provider_egress_id",
            name=op.f("uq_recording_egress_operations_provider_egress_id"),
        ),
    )
    op.create_index(
        "ix_recording_egress_operations_due_work",
        "recording_egress_operations",
        ["start_state", "stop_requested_at", "delete_requested_at", "updated_at"],
        unique=False,
    )

    if context.is_offline_mode():
        _backfill_postgresql()
        return
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _backfill_sqlite(bind)
        return
    if bind.dialect.name == "postgresql":
        _backfill_postgresql()
        return
    raise RuntimeError(
        f"Unsupported recording egress migration dialect: {bind.dialect.name}"
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM outbox_events "
            "WHERE topic = 'recording.reconcile' "
            "AND aggregate_type = 'recording-egress-operation'"
        )
    )
    op.drop_index(
        "ix_recording_egress_operations_due_work",
        table_name="recording_egress_operations",
    )
    op.drop_table("recording_egress_operations")
