from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from uuid import UUID, uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from sqlalchemy import event as sa_event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.call import Call
from app.models.recording_egress_operation import (
    RECORDING_START_STATES,
    RecordingEgressOperation,
)
from app.models.user import User
from app.repositories.recording_egress_operation_repository import (
    RecordingEgressOperationRepository,
    RecordingOperationObservabilitySnapshot,
)


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0014_add_recording_egress_operations.py"
)
SNAPSHOT_NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)


def _load_migration() -> ModuleType:
    assert MIGRATION_PATH.exists(), "Recording egress operation migration must exist"
    spec = spec_from_file_location(
        "recording_egress_operation_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.context = SimpleNamespace(is_offline_mode=lambda: False)
    return module


def _normalized_sql(constraint: sa.CheckConstraint) -> str:
    return " ".join(str(constraint.sqltext).split())


def test_recording_operation_model_has_private_coordination_shape() -> None:
    columns = RecordingEgressOperation.__table__.c

    assert set(columns.keys()) == {
        "id",
        "call_id",
        "room_name",
        "legacy_incomplete",
        "expected_object_key",
        "provider_egress_id",
        "start_state",
        "start_attempted_at",
        "stop_requested_at",
        "delete_requested_at",
        "provider_terminal_at",
        "object_deleted_at",
        "last_reconciled_at",
        "last_error_code",
        "created_at",
        "updated_at",
    }
    assert RECORDING_START_STATES == frozenset(
        {"prepared", "starting", "started", "not_started", "uncertain"}
    )
    assert {
        name: columns[name].nullable
        for name in (
            "call_id",
            "room_name",
            "legacy_incomplete",
            "expected_object_key",
            "provider_egress_id",
            "start_state",
            "start_attempted_at",
            "stop_requested_at",
            "delete_requested_at",
            "provider_terminal_at",
            "object_deleted_at",
            "last_reconciled_at",
            "last_error_code",
        )
    } == {
        "call_id": False,
        "room_name": True,
        "legacy_incomplete": False,
        "expected_object_key": False,
        "provider_egress_id": True,
        "start_state": False,
        "start_attempted_at": True,
        "stop_requested_at": True,
        "delete_requested_at": True,
        "provider_terminal_at": True,
        "object_deleted_at": True,
        "last_reconciled_at": True,
        "last_error_code": True,
    }
    assert columns.room_name.type.length == 255
    assert columns.expected_object_key.type.length == 512
    assert columns.provider_egress_id.type.length == 255
    assert columns.start_state.type.length == 32
    assert columns.last_error_code.type.length == 100
    assert columns.legacy_incomplete.default.arg is False
    assert str(columns.legacy_incomplete.server_default.arg) == "false"

    unique_constraints = {
        constraint.name
        for constraint in RecordingEgressOperation.__table__.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert unique_constraints == {
        "uq_recording_egress_operations_call_id",
        "uq_recording_egress_operations_provider_egress_id",
    }
    checks = {
        constraint.name: _normalized_sql(constraint)
        for constraint in RecordingEgressOperation.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert checks == {
        "ck_recording_egress_operations_start_state_allowed": (
            "start_state IN ('prepared','starting','started','not_started','uncertain')"
        ),
        "ck_recording_egress_operations_provider_identity_consistent": (
            "(start_state = 'started' AND provider_egress_id IS NOT NULL) OR "
            "(start_state <> 'started' AND provider_egress_id IS NULL)"
        ),
        "ck_recording_egress_operations_legacy_room_consistent": (
            "(legacy_incomplete = false AND room_name IS NOT NULL) OR "
            "(legacy_incomplete = true AND room_name IS NULL AND "
            "start_state IN ('started','uncertain'))"
        ),
        "ck_recording_egress_operations_prepared_attempt_consistent": (
            "start_state <> 'prepared' OR start_attempted_at IS NULL"
        ),
        "ck_recording_egress_operations_delete_implies_stop": (
            "delete_requested_at IS NULL OR stop_requested_at IS NOT NULL"
        ),
        "ck_recording_egress_operations_object_delete_implies_request": (
            "object_deleted_at IS NULL OR delete_requested_at IS NOT NULL"
        ),
    }

    call_foreign_keys = list(columns.call_id.foreign_keys)
    assert len(call_foreign_keys) == 1
    assert call_foreign_keys[0].target_fullname == "calls.id"
    assert call_foreign_keys[0].ondelete == "RESTRICT"
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in RecordingEgressOperation.__table__.indexes
    }
    assert indexes == {
        "ix_recording_egress_operations_due_work": (
            "start_state",
            "stop_requested_at",
            "delete_requested_at",
            "updated_at",
        )
    }


async def _create_call(db_session: AsyncSession, *, suffix: str) -> Call:
    user = User(
        external_user_id=f"recording_operation_{suffix}_{uuid4().hex}",
        email=f"recording_operation_{suffix}_{uuid4().hex}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    call = Call(user_id=user.id, status="completed")
    db_session.add(call)
    await db_session.flush()
    return call


def _operation(
    call: Call,
    *,
    room_name: str,
    start_state: str = "prepared",
    provider_egress_id: str | None = None,
) -> RecordingEgressOperation:
    return RecordingEgressOperation(
        call_id=call.id,
        room_name=room_name,
        expected_object_key=f"calls/{call.user_id}/{call.id}.ogg",
        provider_egress_id=provider_egress_id,
        start_state=start_state,
    )


@pytest.mark.anyio
async def test_recording_operation_repository_round_trips_and_counts_states(
    db_session: AsyncSession,
) -> None:
    call = await _create_call(db_session, suffix="round_trip")
    operation = _operation(call, room_name="room-round-trip")
    repository = RecordingEgressOperationRepository(db_session)

    assert await repository.add(operation) is operation
    assert await repository.get_by_id(operation.id) is operation
    assert await repository.get_by_id_for_update(operation.id) is operation
    assert await repository.get_by_call_id_for_update(call.id) is operation
    assert await repository.get_by_room_name("room-round-trip") is operation
    assert (await repository.observability_snapshot(SNAPSHOT_NOW)).counts == {
        "prepared": 1,
        "starting": 0,
        "started": 0,
        "not_started": 0,
        "uncertain": 0,
    }

    await repository.delete(operation)

    assert await repository.get_by_id(operation.id) is None
    assert (await repository.observability_snapshot(SNAPSHOT_NOW)).counts == {
        state: 0 for state in RECORDING_START_STATES
    }


@pytest.mark.anyio
async def test_recording_operation_observability_snapshot_counts_all_start_states(
    db_session: AsyncSession,
) -> None:
    repository = RecordingEgressOperationRepository(db_session)
    state_inputs = {
        "prepared": None,
        "starting": None,
        "started": "EG_snapshot",
        "not_started": None,
        "uncertain": None,
    }
    for state, provider_egress_id in state_inputs.items():
        call = await _create_call(db_session, suffix=f"snapshot_{state}")
        await repository.add(
            _operation(
                call,
                room_name=f"room-snapshot-{state}",
                start_state=state,
                provider_egress_id=provider_egress_id,
            )
        )

    assert (await repository.observability_snapshot(SNAPSHOT_NOW)).counts == {
        state: 1 for state in state_inputs
    }


async def _add_snapshot_operation(
    db_session: AsyncSession,
    repository: RecordingEgressOperationRepository,
    *,
    suffix: str,
    start_state: str,
    created_at: datetime,
    stop_requested_at: datetime | None = None,
    delete_requested_at: datetime | None = None,
    provider_terminal_at: datetime | None = None,
    object_deleted_at: datetime | None = None,
    last_error_code: str | None = None,
) -> RecordingEgressOperation:
    call = await _create_call(db_session, suffix=f"snapshot_contract_{suffix}")
    operation = _operation(
        call,
        room_name=f"room-snapshot-contract-{suffix}",
        start_state=start_state,
        provider_egress_id=(
            f"EG_snapshot_contract_{suffix}" if start_state == "started" else None
        ),
    )
    operation.created_at = created_at
    operation.updated_at = created_at
    operation.stop_requested_at = stop_requested_at
    operation.delete_requested_at = delete_requested_at
    operation.provider_terminal_at = provider_terminal_at
    operation.object_deleted_at = object_deleted_at
    operation.last_error_code = last_error_code
    await repository.add(operation)
    return operation


@pytest.mark.anyio
async def test_recording_operation_observability_snapshot_has_exact_empty_shape(
    db_session: AsyncSession,
) -> None:
    snapshot = await RecordingEgressOperationRepository(
        db_session
    ).observability_snapshot(SNAPSHOT_NOW)

    assert tuple(
        field.name for field in fields(RecordingOperationObservabilitySnapshot)
    ) == (
        "counts",
        "oldest_unresolved_age_seconds",
        "pending_stop_count",
        "oldest_pending_stop_age_seconds",
        "pending_deletion_count",
        "oldest_pending_deletion_age_seconds",
    )
    assert tuple(snapshot.counts) == (
        "prepared",
        "starting",
        "started",
        "not_started",
        "uncertain",
    )
    assert snapshot.counts == {
        "prepared": 0,
        "starting": 0,
        "started": 0,
        "not_started": 0,
        "uncertain": 0,
    }
    assert snapshot.oldest_unresolved_age_seconds == 0.0
    assert snapshot.pending_stop_count == 0
    assert snapshot.oldest_pending_stop_age_seconds == 0.0
    assert snapshot.pending_deletion_count == 0
    assert snapshot.oldest_pending_deletion_age_seconds == 0.0


@pytest.mark.anyio
async def test_recording_operation_observability_snapshot_uses_exact_predicates_and_clocks(
    db_session: AsyncSession,
) -> None:
    repository = RecordingEgressOperationRepository(db_session)

    def seconds_ago(seconds: int) -> datetime:
        return SNAPSHOT_NOW - timedelta(seconds=seconds)

    await _add_snapshot_operation(
        db_session,
        repository,
        suffix="healthy_active",
        start_state="started",
        created_at=seconds_ago(2_000),
    )
    await _add_snapshot_operation(
        db_session,
        repository,
        suffix="clean_non_start",
        start_state="not_started",
        created_at=seconds_ago(1_900),
    )
    await _add_snapshot_operation(
        db_session,
        repository,
        suffix="unresolved_prepared",
        start_state="prepared",
        created_at=seconds_ago(100),
    )
    await _add_snapshot_operation(
        db_session,
        repository,
        suffix="unresolved_starting",
        start_state="starting",
        created_at=seconds_ago(200),
    )
    await _add_snapshot_operation(
        db_session,
        repository,
        suffix="unresolved_uncertain",
        start_state="uncertain",
        created_at=seconds_ago(300),
    )
    await _add_snapshot_operation(
        db_session,
        repository,
        suffix="unresolved_error",
        start_state="started",
        created_at=seconds_ago(400),
        last_error_code="recording_unresolved",
    )
    await _add_snapshot_operation(
        db_session,
        repository,
        suffix="pending_stop",
        start_state="started",
        created_at=seconds_ago(500),
        stop_requested_at=seconds_ago(50),
    )
    await _add_snapshot_operation(
        db_session,
        repository,
        suffix="definitive_non_start",
        start_state="not_started",
        created_at=seconds_ago(1_800),
        stop_requested_at=seconds_ago(900),
    )
    await _add_snapshot_operation(
        db_session,
        repository,
        suffix="terminal_identity_conflict",
        start_state="started",
        created_at=seconds_ago(600),
        stop_requested_at=seconds_ago(80),
        provider_terminal_at=seconds_ago(20),
        last_error_code="recording_identity_conflict",
    )
    await _add_snapshot_operation(
        db_session,
        repository,
        suffix="terminal_stop_satisfied",
        start_state="started",
        created_at=seconds_ago(1_700),
        stop_requested_at=seconds_ago(1_000),
        provider_terminal_at=seconds_ago(900),
    )
    await _add_snapshot_operation(
        db_session,
        repository,
        suffix="pending_deletion",
        start_state="not_started",
        created_at=seconds_ago(700),
        stop_requested_at=seconds_ago(80),
        delete_requested_at=seconds_ago(70),
    )
    await _add_snapshot_operation(
        db_session,
        repository,
        suffix="deleted_object_row_retained",
        start_state="not_started",
        created_at=seconds_ago(800),
        stop_requested_at=seconds_ago(100),
        delete_requested_at=seconds_ago(90),
        object_deleted_at=seconds_ago(30),
    )

    snapshot = await repository.observability_snapshot(SNAPSHOT_NOW)

    assert snapshot.counts == {
        "prepared": 1,
        "starting": 1,
        "started": 5,
        "not_started": 4,
        "uncertain": 1,
    }
    assert snapshot.oldest_unresolved_age_seconds == 800.0
    assert snapshot.pending_stop_count == 2
    assert snapshot.oldest_pending_stop_age_seconds == 80.0
    assert snapshot.pending_deletion_count == 1
    assert snapshot.oldest_pending_deletion_age_seconds == 70.0


@pytest.mark.anyio
async def test_recording_operation_observability_snapshot_returns_zero_for_no_matches(
    db_session: AsyncSession,
) -> None:
    repository = RecordingEgressOperationRepository(db_session)
    await _add_snapshot_operation(
        db_session,
        repository,
        suffix="resolved_started",
        start_state="started",
        created_at=SNAPSHOT_NOW - timedelta(days=30),
    )
    await _add_snapshot_operation(
        db_session,
        repository,
        suffix="resolved_not_started",
        start_state="not_started",
        created_at=SNAPSHOT_NOW - timedelta(days=30),
        stop_requested_at=SNAPSHOT_NOW - timedelta(days=29),
    )

    snapshot = await repository.observability_snapshot(SNAPSHOT_NOW)

    assert snapshot.oldest_unresolved_age_seconds == 0.0
    assert snapshot.pending_stop_count == 0
    assert snapshot.oldest_pending_stop_age_seconds == 0.0
    assert snapshot.pending_deletion_count == 0
    assert snapshot.oldest_pending_deletion_age_seconds == 0.0


@pytest.mark.anyio
async def test_recording_operation_observability_snapshot_normalizes_naive_utc_and_clamps_future_ages(
    db_session: AsyncSession,
) -> None:
    repository = RecordingEgressOperationRepository(db_session)
    future = SNAPSHOT_NOW + timedelta(seconds=30)
    await _add_snapshot_operation(
        db_session,
        repository,
        suffix="future_pending_cleanup",
        start_state="started",
        created_at=future,
        stop_requested_at=future,
        delete_requested_at=future,
    )

    snapshot = await repository.observability_snapshot(
        SNAPSHOT_NOW.replace(tzinfo=None)
    )

    assert snapshot.oldest_unresolved_age_seconds == 0.0
    assert snapshot.pending_stop_count == 1
    assert snapshot.oldest_pending_stop_age_seconds == 0.0
    assert snapshot.pending_deletion_count == 1
    assert snapshot.oldest_pending_deletion_age_seconds == 0.0


@pytest.mark.anyio
async def test_recording_operation_observability_snapshot_does_not_select_identities(
    db_session: AsyncSession,
) -> None:
    repository = RecordingEgressOperationRepository(db_session)
    await _add_snapshot_operation(
        db_session,
        repository,
        suffix="privacy_sentinel",
        start_state="prepared",
        created_at=SNAPSHOT_NOW - timedelta(seconds=10),
    )
    statements: list[str] = []
    engine = db_session.bind
    assert engine is not None

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement.lower())

    sa_event.listen(engine.sync_engine, "before_cursor_execute", capture_statement)
    try:
        await repository.observability_snapshot(SNAPSHOT_NOW)
    finally:
        sa_event.remove(
            engine.sync_engine,
            "before_cursor_execute",
            capture_statement,
        )

    assert statements
    rendered_sql = "\n".join(statements)
    for forbidden_column in (
        "recording_egress_operations.id",
        "recording_egress_operations.call_id",
        "recording_egress_operations.room_name",
        "recording_egress_operations.expected_object_key",
        "recording_egress_operations.provider_egress_id",
    ):
        assert forbidden_column not in rendered_sql


@pytest.mark.anyio
async def test_recording_operation_database_allows_only_one_row_per_call(
    db_session: AsyncSession,
) -> None:
    call = await _create_call(db_session, suffix="call_identity")
    repository = RecordingEgressOperationRepository(db_session)
    await repository.add(_operation(call, room_name="room-call-identity"))

    with pytest.raises(IntegrityError):
        await repository.add(
            _operation(
                call,
                room_name="room-call-identity-duplicate",
                start_state="uncertain",
            )
        )


@pytest.mark.anyio
async def test_recording_operation_database_allows_only_one_row_per_egress_id(
    db_session: AsyncSession,
) -> None:
    first_call = await _create_call(db_session, suffix="egress_identity_first")
    second_call = await _create_call(db_session, suffix="egress_identity_second")
    repository = RecordingEgressOperationRepository(db_session)
    await repository.add(
        _operation(
            first_call,
            room_name="room-egress-identity-first",
            start_state="started",
            provider_egress_id="EG_shared",
        )
    )

    with pytest.raises(IntegrityError):
        await repository.add(
            _operation(
                second_call,
                room_name="room-egress-identity-second",
                start_state="started",
                provider_egress_id="EG_shared",
            )
        )


def _legacy_tables(metadata: sa.MetaData) -> tuple[sa.Table, sa.Table]:
    calls = sa.Table(
        "calls",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("livekit_room_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recording_object_key", sa.String(512), nullable=True),
        sa.Column("recording_egress_id", sa.String(255), nullable=True),
        sa.Column("recording_url", sa.String(512), nullable=True),
    )
    outbox_events = sa.Table(
        "outbox_events",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("aggregate_type", sa.String(100), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("routing_target_provider_number_id", sa.String(255), nullable=True),
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
    )
    return calls, outbox_events


def _insert_legacy_stop(
    connection: sa.Connection,
    outbox_events: sa.Table,
    *,
    call_id: UUID,
    status: str,
    suffix: str,
    event_id: UUID | None = None,
) -> None:
    now = datetime(2026, 7, 19, 8, tzinfo=UTC)
    connection.execute(
        outbox_events.insert().values(
            id=event_id or uuid4(),
            idempotency_key=f"recording.stop:{call_id}:{suffix}",
            topic="recording.stop",
            aggregate_type="call-recording",
            aggregate_id=call_id,
            payload={"call_id": str(call_id)},
            status=status,
            attempt_count=1,
            next_attempt_at=now,
            last_error_code="provider_terminal" if status == "failed" else None,
            delivered_at=now if status == "delivered" else None,
        )
    )


def _as_uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _as_payload(value: object) -> dict[str, str]:
    return value if isinstance(value, dict) else json.loads(str(value))


def _as_utc_datetime(value: object) -> datetime:
    parsed = (
        value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    )
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def test_sqlite_migration_backfills_operations_and_reference_only_work() -> None:
    migration = _load_migration()
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    calls, outbox_events = _legacy_tables(metadata)
    metadata.create_all(engine)
    terminal_ended_at = datetime(2026, 7, 18, 6, tzinfo=UTC)
    deleted_at = datetime(2026, 7, 18, 7, tzinfo=UTC)
    ids = {
        "known_terminal": uuid4(),
        "object_active": uuid4(),
        "empty_metadata_active": uuid4(),
        "url_active": uuid4(),
        "blank_provider_key": uuid4(),
        "blank_provider_url": uuid4(),
        "missing_room": uuid4(),
        "deleted": uuid4(),
        "known_active": uuid4(),
    }
    user_ids = {name: uuid4() for name in ids}
    seeded = [
        {
            "id": ids["known_terminal"],
            "user_id": user_ids["known_terminal"],
            "livekit_room_id": "room-known-terminal",
            "status": "completed",
            "ended_at": terminal_ended_at,
            "deleted_at": None,
            "recording_object_key": None,
            "recording_egress_id": "EG_test",
            "recording_url": "https://playback.example/known",
        },
        {
            "id": ids["object_active"],
            "user_id": user_ids["object_active"],
            "livekit_room_id": "room-object-active",
            "status": "connected",
            "ended_at": None,
            "deleted_at": None,
            "recording_object_key": "calls/existing/object.ogg",
            "recording_egress_id": None,
            "recording_url": None,
        },
        {
            "id": ids["empty_metadata_active"],
            "user_id": user_ids["empty_metadata_active"],
            "livekit_room_id": "room-empty-metadata-active",
            "status": "connected",
            "ended_at": None,
            "deleted_at": None,
            "recording_object_key": "",
            "recording_egress_id": "",
            "recording_url": "",
        },
        {
            "id": ids["url_active"],
            "user_id": user_ids["url_active"],
            "livekit_room_id": "room-url-active",
            "status": "pending",
            "ended_at": None,
            "deleted_at": None,
            "recording_object_key": None,
            "recording_egress_id": None,
            "recording_url": "https://playback.example/url-only",
        },
        {
            "id": ids["blank_provider_key"],
            "user_id": user_ids["blank_provider_key"],
            "livekit_room_id": "",
            "status": "connected",
            "ended_at": None,
            "deleted_at": None,
            "recording_object_key": "calls/legacy/blank-provider-key.ogg",
            "recording_egress_id": "",
            "recording_url": None,
        },
        {
            "id": ids["blank_provider_url"],
            "user_id": user_ids["blank_provider_url"],
            "livekit_room_id": "room-blank-provider-url",
            "status": "ending",
            "ended_at": None,
            "deleted_at": None,
            "recording_object_key": None,
            "recording_egress_id": "",
            "recording_url": "https://playback.example/blank-provider-url",
        },
        {
            "id": ids["missing_room"],
            "user_id": user_ids["missing_room"],
            "livekit_room_id": None,
            "status": "connected",
            "ended_at": None,
            "deleted_at": None,
            "recording_object_key": "calls/legacy/missing-room.ogg",
            "recording_egress_id": "EG_missing_room",
            "recording_url": None,
        },
        {
            "id": ids["deleted"],
            "user_id": user_ids["deleted"],
            "livekit_room_id": "room-deleted",
            "status": "failed",
            "ended_at": None,
            "deleted_at": deleted_at,
            "recording_object_key": "calls/legacy/deleted.ogg",
            "recording_egress_id": "EG_deleted",
            "recording_url": None,
        },
        {
            "id": ids["known_active"],
            "user_id": user_ids["known_active"],
            "livekit_room_id": "room-known-active",
            "status": "connected",
            "ended_at": None,
            "deleted_at": None,
            "recording_object_key": "calls/legacy/known-active.ogg",
            "recording_egress_id": "EG_known_active",
            "recording_url": None,
        },
    ]

    with engine.begin() as connection:
        connection.execute(calls.insert(), seeded)
        _insert_legacy_stop(
            connection,
            outbox_events,
            call_id=ids["known_terminal"],
            status="pending",
            suffix="pending",
        )
        _insert_legacy_stop(
            connection,
            outbox_events,
            call_id=ids["known_terminal"],
            status="processing",
            suffix="processing",
        )
        _insert_legacy_stop(
            connection,
            outbox_events,
            call_id=ids["known_terminal"],
            status="delivered",
            suffix="delivered",
        )
        _insert_legacy_stop(
            connection,
            outbox_events,
            call_id=ids["known_terminal"],
            status="failed",
            suffix="failed",
        )
        _insert_legacy_stop(
            connection,
            outbox_events,
            call_id=ids["known_active"],
            status="pending",
            suffix="no-replacement",
        )

        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        operation_rows = {
            _as_uuid(row["call_id"]): row
            for row in connection.execute(
                sa.text("SELECT * FROM recording_egress_operations")
            ).mappings()
        }
        event_rows = list(
            connection.execute(
                sa.select(outbox_events).order_by(outbox_events.c.idempotency_key)
            ).mappings()
        )
        call_rows = {
            _as_uuid(row["id"]): row
            for row in connection.execute(sa.select(calls)).mappings()
        }

    assert set(operation_rows) == set(ids.values()) - {ids["empty_metadata_active"]}
    known = operation_rows[ids["known_terminal"]]
    assert _as_uuid(known["id"]) == ids["known_terminal"]
    assert known["start_state"] == "started"
    assert known["provider_egress_id"] == "EG_test"
    assert known["expected_object_key"] == (
        f"calls/{user_ids['known_terminal']}/{ids['known_terminal']}.ogg"
    )
    assert _as_utc_datetime(known["stop_requested_at"]) == terminal_ended_at
    assert known["delete_requested_at"] is None

    object_only = operation_rows[ids["object_active"]]
    assert object_only["start_state"] == "uncertain"
    assert object_only["expected_object_key"] == "calls/existing/object.ogg"
    assert object_only["stop_requested_at"] is None
    assert object_only["delete_requested_at"] is None

    assert ids["empty_metadata_active"] not in operation_rows

    url_only = operation_rows[ids["url_active"]]
    assert url_only["start_state"] == "uncertain"
    assert url_only["expected_object_key"] == (
        f"calls/{user_ids['url_active']}/{ids['url_active']}.ogg"
    )

    blank_provider_key = operation_rows[ids["blank_provider_key"]]
    assert blank_provider_key["room_name"] is None
    assert bool(blank_provider_key["legacy_incomplete"]) is True
    assert blank_provider_key["provider_egress_id"] is None
    assert blank_provider_key["start_state"] == "uncertain"

    blank_provider_url = operation_rows[ids["blank_provider_url"]]
    assert blank_provider_url["room_name"] == "room-blank-provider-url"
    assert blank_provider_url["legacy_incomplete"] in (False, 0)
    assert blank_provider_url["provider_egress_id"] is None
    assert blank_provider_url["start_state"] == "uncertain"

    missing_room = operation_rows[ids["missing_room"]]
    assert missing_room["room_name"] is None
    assert bool(missing_room["legacy_incomplete"]) is True
    assert operation_rows[ids["object_active"]]["legacy_incomplete"] in (False, 0)

    deleted = operation_rows[ids["deleted"]]
    assert _as_utc_datetime(deleted["stop_requested_at"]) == deleted_at
    assert _as_utc_datetime(deleted["delete_requested_at"]) == deleted_at

    reconcile_events = [
        row for row in event_rows if row["topic"] == "recording.reconcile"
    ]
    assert {row["idempotency_key"].rsplit(":", 1)[-1] for row in reconcile_events} == {
        "delete",
        "start",
        "stop",
    }
    assert {_as_uuid(row["aggregate_id"]) for row in reconcile_events} == {
        ids["known_terminal"],
        ids["object_active"],
        ids["url_active"],
        ids["blank_provider_key"],
        ids["blank_provider_url"],
        ids["deleted"],
    }
    reconcile_keys = {row["idempotency_key"] for row in reconcile_events}
    assert f"recording.reconcile:{ids['blank_provider_key']}:start" in reconcile_keys
    assert f"recording.reconcile:{ids['blank_provider_url']}:start" in reconcile_keys
    assert all(
        _as_uuid(row["aggregate_id"]) != ids["empty_metadata_active"]
        for row in reconcile_events
    )
    for event in reconcile_events:
        operation_id = _as_uuid(event["aggregate_id"])
        assert event["aggregate_type"] == "recording-egress-operation"
        assert _as_payload(event["payload"]) == {"operation_id": str(operation_id)}

    legacy_events = [row for row in event_rows if row["topic"] == "recording.stop"]
    assert {row["idempotency_key"].rsplit(":", 1)[-1] for row in legacy_events} == {
        "delivered",
        "failed",
        "no-replacement",
    }
    assert (
        next(
            row
            for row in legacy_events
            if row["idempotency_key"].endswith(":delivered")
        )["status"]
        == "delivered"
    )

    for original in seeded:
        persisted = call_rows[original["id"]]
        assert persisted["recording_object_key"] == original["recording_object_key"]
        assert persisted["recording_egress_id"] == original["recording_egress_id"]
        assert persisted["recording_url"] == original["recording_url"]

    engine.dispose()


class _ExecuteOperations:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: object) -> None:
        self.statements.append(" ".join(str(statement).split()))


def test_postgresql_backfill_requires_non_empty_legacy_metadata() -> None:
    migration = _load_migration()
    operations = _ExecuteOperations()
    migration.op = operations

    migration._backfill_postgresql()

    operation_backfill = operations.statements[0]
    legacy_event_replacement = operations.statements[2]
    for column in (
        "recording_object_key",
        "recording_egress_id",
        "recording_url",
    ):
        predicate = f"NULLIF(calls.{column}, '') IS NOT NULL"
        assert predicate in operation_backfill
        assert predicate in legacy_event_replacement


def test_sqlite_downgrade_removes_revision_events_and_reupgrade_is_reversible() -> None:
    migration = _load_migration()
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    calls, outbox_events = _legacy_tables(metadata)
    metadata.create_all(engine)
    ended_at = datetime(2026, 7, 18, 6, tzinfo=UTC)
    call_ids = (uuid4(), uuid4())
    legacy_ids = (uuid4(), uuid4())

    with engine.begin() as connection:
        connection.execute(
            calls.insert(),
            [
                {
                    "id": call_id,
                    "user_id": uuid4(),
                    "livekit_room_id": f"room-reupgrade-{index}",
                    "status": "completed",
                    "ended_at": ended_at,
                    "deleted_at": None,
                    "recording_object_key": f"calls/reupgrade/{call_id}.ogg",
                    "recording_egress_id": f"EG_reupgrade_{index}",
                    "recording_url": None,
                }
                for index, call_id in enumerate(call_ids)
            ],
        )
        _insert_legacy_stop(
            connection,
            outbox_events,
            call_id=call_ids[0],
            status="delivered",
            suffix="reupgrade-delivered",
            event_id=legacy_ids[0],
        )
        _insert_legacy_stop(
            connection,
            outbox_events,
            call_id=call_ids[1],
            status="failed",
            suffix="reupgrade-failed",
            event_id=legacy_ids[1],
        )

        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        connection.execute(
            outbox_events.update()
            .where(
                outbox_events.c.topic == "recording.reconcile",
                outbox_events.c.aggregate_id == call_ids[0],
            )
            .values(status="delivered", delivered_at=ended_at)
        )
        connection.execute(
            outbox_events.update()
            .where(
                outbox_events.c.topic == "recording.reconcile",
                outbox_events.c.aggregate_id == call_ids[1],
            )
            .values(status="failed", last_error_code="provider_terminal")
        )
        first_revision_event_ids = set(
            connection.scalars(
                sa.select(outbox_events.c.id).where(
                    outbox_events.c.topic == "recording.reconcile"
                )
            )
        )
        legacy_before = list(
            connection.execute(
                sa.select(outbox_events)
                .where(outbox_events.c.topic == "recording.stop")
                .order_by(outbox_events.c.idempotency_key)
            ).mappings()
        )

        migration.downgrade()
        migration.upgrade()

        replacement_events = list(
            connection.execute(
                sa.select(outbox_events)
                .where(outbox_events.c.topic == "recording.reconcile")
                .order_by(outbox_events.c.idempotency_key)
            ).mappings()
        )
        legacy_after = list(
            connection.execute(
                sa.select(outbox_events)
                .where(outbox_events.c.topic == "recording.stop")
                .order_by(outbox_events.c.idempotency_key)
            ).mappings()
        )

    assert len(first_revision_event_ids) == 2
    assert len(replacement_events) == 2
    assert first_revision_event_ids.isdisjoint(
        {row["id"] for row in replacement_events}
    )
    assert {row["status"] for row in replacement_events} == {"pending"}
    assert legacy_after == legacy_before
    engine.dispose()


class _DowngradeOperations:
    def __init__(self) -> None:
        self.timeline: list[tuple[str, tuple, dict]] = []

    def execute(self, *args, **kwargs) -> None:
        self.timeline.append(("execute", args, kwargs))

    def drop_index(self, *args, **kwargs) -> None:
        self.timeline.append(("drop_index", args, kwargs))

    def drop_table(self, *args, **kwargs) -> None:
        self.timeline.append(("drop_table", args, kwargs))


def test_recording_operation_migration_revision_and_downgrade_order() -> None:
    migration = _load_migration()
    operations = _DowngradeOperations()
    migration.op = operations

    migration.downgrade()

    assert migration.revision == "0014_recording_egress_ops"
    assert migration.down_revision == "0013_outbox_routing_target"
    assert [name for name, _args, _kwargs in operations.timeline] == [
        "execute",
        "drop_index",
        "drop_table",
    ]
    assert "recording.reconcile" in str(operations.timeline[0][1][0])
    assert operations.timeline[1][1] == ("ix_recording_egress_operations_due_work",)
    assert operations.timeline[1][2] == {"table_name": "recording_egress_operations"}
    assert operations.timeline[2][1] == ("recording_egress_operations",)


@pytest.mark.anyio
async def test_postgresql_0013_to_0014_round_trip_preserves_private_coordination_contract() -> (
    None
):
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Recording operation migration proof requires TEST_DATABASE_URL")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )
    if not database_url.startswith("postgresql+asyncpg://"):
        pytest.skip("TEST_DATABASE_URL must identify PostgreSQL")

    source_url = make_url(database_url)
    database_name = f"task7_recording_migration_{uuid4().hex}"
    migration_url = source_url.set(database=database_name)
    admin_engine = create_async_engine(
        source_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    migration_engine = None

    def run_alembic(command: str, revision: str) -> None:
        env = {
            **os.environ,
            "DATABASE_URL": migration_url.render_as_string(hide_password=False),
        }
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", command, revision],
            cwd=MIGRATION_PATH.parents[2],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr

    call_names = (
        "active",
        "terminal",
        "terminal_no_end",
        "deleted",
        "empty_metadata",
        "incomplete_room",
        "blank_provider_key",
        "blank_provider_url",
    )
    call_ids = {name: uuid4() for name in call_names}
    user_ids = {name: uuid4() for name in call_names}
    created_at = datetime(2026, 7, 18, 6, tzinfo=UTC)
    active_started_at = datetime(2026, 7, 18, 7, tzinfo=UTC)
    terminal_ended_at = datetime(2026, 7, 18, 8, tzinfo=UTC)
    deleted_ended_at = datetime(2026, 7, 18, 9, tzinfo=UTC)
    deleted_at = datetime(2026, 7, 18, 10, tzinfo=UTC)
    active_object_key = "calls/synthetic-active/original.ogg"
    deleted_object_key = "calls/synthetic-deleted/original.ogg"
    incomplete_object_key = "calls/synthetic-incomplete/original.ogg"
    blank_provider_object_key = "calls/synthetic-blank-provider/original.ogg"
    terminal_egress_id = "EG_synthetic_terminal"
    terminal_no_end_egress_id = "EG_synthetic_terminal_no_end"
    deleted_egress_id = "EG_synthetic_deleted"

    try:
        async with admin_engine.connect() as connection:
            await connection.execute(sa.text(f'CREATE DATABASE "{database_name}"'))

        run_alembic("upgrade", "0013_outbox_routing_target")
        migration_engine = create_async_engine(migration_url)
        async with migration_engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO users "
                    "(id, clerk_user_id, email, status, created_at, updated_at) "
                    "VALUES (:id, :clerk_user_id, :email, 'active', "
                    ":created_at, :updated_at)"
                ),
                [
                    {
                        "id": user_ids[name],
                        "clerk_user_id": f"task7-migration-{name}-{user_ids[name]}",
                        "email": f"task7-migration-{name}-{user_ids[name]}@example.test",
                        "created_at": created_at,
                        "updated_at": created_at,
                    }
                    for name in call_names
                ],
            )
            await connection.execute(
                sa.text(
                    "INSERT INTO calls "
                    "(id, user_id, livekit_room_id, caller_number, status, "
                    "state_changed_at, started_at, ended_at, deleted_at, "
                    "summary_text, recording_object_key, recording_egress_id, "
                    "recording_url, failure_code, created_at, updated_at) "
                    "VALUES (:id, :user_id, :livekit_room_id, :caller_number, "
                    ":status, :state_changed_at, :started_at, :ended_at, "
                    ":deleted_at, :summary_text, :recording_object_key, "
                    ":recording_egress_id, :recording_url, NULL, "
                    ":created_at, :updated_at)"
                ),
                [
                    {
                        "id": call_ids["active"],
                        "user_id": user_ids["active"],
                        "livekit_room_id": "room-synthetic-active",
                        "caller_number": "+353000000001",
                        "status": "connected",
                        "state_changed_at": active_started_at,
                        "started_at": active_started_at,
                        "ended_at": None,
                        "deleted_at": None,
                        "summary_text": "synthetic active summary",
                        "recording_object_key": active_object_key,
                        "recording_egress_id": None,
                        "recording_url": None,
                        "created_at": created_at,
                        "updated_at": active_started_at,
                    },
                    {
                        "id": call_ids["terminal"],
                        "user_id": user_ids["terminal"],
                        "livekit_room_id": "room-synthetic-terminal",
                        "caller_number": "+353000000002",
                        "status": "completed",
                        "state_changed_at": terminal_ended_at,
                        "started_at": active_started_at,
                        "ended_at": terminal_ended_at,
                        "deleted_at": None,
                        "summary_text": "synthetic terminal summary",
                        "recording_object_key": None,
                        "recording_egress_id": terminal_egress_id,
                        "recording_url": "https://synthetic.invalid/terminal",
                        "created_at": created_at,
                        "updated_at": terminal_ended_at,
                    },
                    {
                        "id": call_ids["terminal_no_end"],
                        "user_id": user_ids["terminal_no_end"],
                        "livekit_room_id": "room-synthetic-terminal-no-end",
                        "caller_number": "+353000000006",
                        "status": "completed",
                        "state_changed_at": terminal_ended_at,
                        "started_at": active_started_at,
                        "ended_at": None,
                        "deleted_at": None,
                        "summary_text": "synthetic terminal no-end summary",
                        "recording_object_key": None,
                        "recording_egress_id": terminal_no_end_egress_id,
                        "recording_url": None,
                        "created_at": created_at,
                        "updated_at": terminal_ended_at,
                    },
                    {
                        "id": call_ids["deleted"],
                        "user_id": user_ids["deleted"],
                        "livekit_room_id": "room-synthetic-deleted",
                        "caller_number": "+353000000003",
                        "status": "completed",
                        "state_changed_at": deleted_ended_at,
                        "started_at": active_started_at,
                        "ended_at": None,
                        "deleted_at": deleted_at,
                        "summary_text": "synthetic deleted summary",
                        "recording_object_key": deleted_object_key,
                        "recording_egress_id": deleted_egress_id,
                        "recording_url": "https://synthetic.invalid/deleted",
                        "created_at": created_at,
                        "updated_at": deleted_at,
                    },
                    {
                        "id": call_ids["empty_metadata"],
                        "user_id": user_ids["empty_metadata"],
                        "livekit_room_id": "room-synthetic-empty",
                        "caller_number": "+353000000004",
                        "status": "connected",
                        "state_changed_at": active_started_at,
                        "started_at": active_started_at,
                        "ended_at": None,
                        "deleted_at": None,
                        "summary_text": "synthetic empty metadata summary",
                        "recording_object_key": "",
                        "recording_egress_id": "",
                        "recording_url": "",
                        "created_at": created_at,
                        "updated_at": active_started_at,
                    },
                    {
                        "id": call_ids["incomplete_room"],
                        "user_id": user_ids["incomplete_room"],
                        "livekit_room_id": None,
                        "caller_number": "+353000000005",
                        "status": "connected",
                        "state_changed_at": active_started_at,
                        "started_at": active_started_at,
                        "ended_at": None,
                        "deleted_at": None,
                        "summary_text": "synthetic incomplete room summary",
                        "recording_object_key": incomplete_object_key,
                        "recording_egress_id": None,
                        "recording_url": None,
                        "created_at": created_at,
                        "updated_at": active_started_at,
                    },
                    {
                        "id": call_ids["blank_provider_key"],
                        "user_id": user_ids["blank_provider_key"],
                        "livekit_room_id": "",
                        "caller_number": "+353000000007",
                        "status": "connected",
                        "state_changed_at": active_started_at,
                        "started_at": active_started_at,
                        "ended_at": None,
                        "deleted_at": None,
                        "summary_text": "synthetic blank provider key summary",
                        "recording_object_key": blank_provider_object_key,
                        "recording_egress_id": "",
                        "recording_url": None,
                        "created_at": created_at,
                        "updated_at": active_started_at,
                    },
                    {
                        "id": call_ids["blank_provider_url"],
                        "user_id": user_ids["blank_provider_url"],
                        "livekit_room_id": "room-synthetic-blank-provider-url",
                        "caller_number": "+353000000008",
                        "status": "finalizing",
                        "state_changed_at": active_started_at,
                        "started_at": active_started_at,
                        "ended_at": None,
                        "deleted_at": None,
                        "summary_text": "synthetic blank provider url summary",
                        "recording_object_key": None,
                        "recording_egress_id": "",
                        "recording_url": "https://synthetic.invalid/blank-provider",
                        "created_at": created_at,
                        "updated_at": active_started_at,
                    },
                ],
            )

            legacy_statuses = ("pending", "processing", "delivered", "failed")
            legacy_event_ids = {status: uuid4() for status in legacy_statuses}
            await connection.execute(
                sa.text(
                    "INSERT INTO outbox_events "
                    "(id, idempotency_key, topic, aggregate_type, aggregate_id, "
                    "payload, status, attempt_count, next_attempt_at, "
                    "last_error_code, delivered_at, created_at, updated_at) "
                    "VALUES (:id, :idempotency_key, 'recording.stop', "
                    "'call-recording', :aggregate_id, "
                    "json_build_object('call_id', CAST(:aggregate_id_text AS text)), "
                    ":status, 1, :next_attempt_at, :last_error_code, "
                    ":delivered_at, :created_at, :updated_at)"
                ),
                [
                    {
                        "id": legacy_event_ids[status],
                        "idempotency_key": (
                            f"recording.stop:{call_ids['terminal']}:{status}"
                        ),
                        "aggregate_id": call_ids["terminal"],
                        "aggregate_id_text": str(call_ids["terminal"]),
                        "status": status,
                        "next_attempt_at": terminal_ended_at,
                        "last_error_code": (
                            "recording_provider_unavailable"
                            if status == "failed"
                            else None
                        ),
                        "delivered_at": (
                            terminal_ended_at if status == "delivered" else None
                        ),
                        "created_at": created_at,
                        "updated_at": terminal_ended_at,
                    }
                    for status in legacy_statuses
                ],
            )
            no_replacement_event_id = uuid4()
            await connection.execute(
                sa.text(
                    "INSERT INTO outbox_events "
                    "(id, idempotency_key, topic, aggregate_type, aggregate_id, "
                    "payload, status, attempt_count, next_attempt_at, "
                    "last_error_code, delivered_at, created_at, updated_at) "
                    "VALUES (:id, :idempotency_key, 'recording.stop', "
                    "'call-recording', :aggregate_id, "
                    "json_build_object('call_id', CAST(:aggregate_id_text AS text)), "
                    "'pending', 0, :next_attempt_at, NULL, NULL, "
                    ":created_at, :updated_at)"
                ),
                {
                    "id": no_replacement_event_id,
                    "idempotency_key": (
                        f"recording.stop:{call_ids['empty_metadata']}:no-replacement"
                    ),
                    "aggregate_id": call_ids["empty_metadata"],
                    "aggregate_id_text": str(call_ids["empty_metadata"]),
                    "next_attempt_at": active_started_at,
                    "created_at": created_at,
                    "updated_at": active_started_at,
                },
            )
            seeded_legacy_events = {
                row["idempotency_key"]: dict(row)
                for row in (
                    await connection.execute(
                        sa.text(
                            "SELECT * FROM outbox_events "
                            "WHERE topic = 'recording.stop' "
                            "ORDER BY idempotency_key"
                        )
                    )
                ).mappings()
            }

        retained_legacy_keys = {
            f"recording.stop:{call_ids['terminal']}:delivered",
            f"recording.stop:{call_ids['terminal']}:failed",
            f"recording.stop:{call_ids['empty_metadata']}:no-replacement",
        }
        removed_legacy_event_ids = {
            legacy_event_ids["pending"],
            legacy_event_ids["processing"],
        }
        retained_legacy_events = {
            key: seeded_legacy_events[key] for key in retained_legacy_keys
        }

        async with migration_engine.connect() as connection:
            migration_db_started_at = await connection.scalar(
                sa.text("SELECT clock_timestamp()")
            )
        assert isinstance(migration_db_started_at, datetime)
        await migration_engine.dispose()
        migration_engine = None
        run_alembic("upgrade", "0014_recording_egress_ops")
        migration_engine = create_async_engine(migration_url)

        async with migration_engine.connect() as connection:
            migration_db_finished_at = await connection.scalar(
                sa.text("SELECT clock_timestamp()")
            )
            revision = await connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            )
            constraint_rows = (
                (
                    await connection.execute(
                        sa.text(
                            "SELECT conname, pg_get_constraintdef(oid) AS definition "
                            "FROM pg_constraint WHERE conrelid = "
                            "'recording_egress_operations'::regclass "
                            "ORDER BY conname"
                        )
                    )
                )
                .mappings()
                .all()
            )
            foreign_key_delete_action = await connection.scalar(
                sa.text(
                    "SELECT confdeltype::text FROM pg_constraint "
                    "WHERE conrelid = 'recording_egress_operations'::regclass "
                    "AND conname = "
                    "'fk_recording_egress_operations_call_id_calls'"
                )
            )
            index_rows = (
                (
                    await connection.execute(
                        sa.text(
                            "SELECT index_class.relname AS index_name, "
                            "indexes.indisunique, "
                            "array_agg(attributes.attname "
                            "ORDER BY keys.ordinality) AS columns "
                            "FROM pg_index AS indexes "
                            "JOIN pg_class AS index_class "
                            "ON index_class.oid = indexes.indexrelid "
                            "CROSS JOIN LATERAL "
                            "unnest(indexes.indkey) WITH ORDINALITY "
                            "AS keys(attnum, ordinality) "
                            "JOIN pg_attribute AS attributes "
                            "ON attributes.attrelid = indexes.indrelid "
                            "AND attributes.attnum = keys.attnum "
                            "WHERE indexes.indrelid = "
                            "'recording_egress_operations'::regclass "
                            "GROUP BY index_class.relname, indexes.indisunique "
                            "ORDER BY index_class.relname"
                        )
                    )
                )
                .mappings()
                .all()
            )
            operation_rows = {
                _as_uuid(row["call_id"]): row
                for row in (
                    await connection.execute(
                        sa.text(
                            "SELECT * FROM recording_egress_operations ORDER BY call_id"
                        )
                    )
                ).mappings()
            }
            event_rows = (
                (
                    await connection.execute(
                        sa.text(
                            "SELECT * "
                            "FROM outbox_events "
                            "WHERE topic IN ('recording.stop', 'recording.reconcile') "
                            "ORDER BY idempotency_key"
                        )
                    )
                )
                .mappings()
                .all()
            )

        assert revision == "0014_recording_egress_ops"
        assert {row["conname"]: row["definition"] for row in constraint_rows} == {
            "ck_recording_egress_operations_delete_implies_stop": (
                "CHECK (((delete_requested_at IS NULL) OR "
                "(stop_requested_at IS NOT NULL)))"
            ),
            "ck_recording_egress_operations_legacy_room_consistent": (
                "CHECK ((((legacy_incomplete = false) AND "
                "(room_name IS NOT NULL)) OR ((legacy_incomplete = true) AND "
                "(room_name IS NULL) AND ((start_state)::text = ANY "
                "((ARRAY['started'::character varying, "
                "'uncertain'::character varying])::text[])))))"
            ),
            "ck_recording_egress_operations_object_delete_implies_request": (
                "CHECK (((object_deleted_at IS NULL) OR "
                "(delete_requested_at IS NOT NULL)))"
            ),
            "ck_recording_egress_operations_prepared_attempt_consistent": (
                "CHECK ((((start_state)::text <> 'prepared'::text) OR "
                "(start_attempted_at IS NULL)))"
            ),
            "ck_recording_egress_operations_provider_identity_consistent": (
                "CHECK (((((start_state)::text = 'started'::text) AND "
                "(provider_egress_id IS NOT NULL)) OR "
                "(((start_state)::text <> 'started'::text) AND "
                "(provider_egress_id IS NULL))))"
            ),
            "ck_recording_egress_operations_start_state_allowed": (
                "CHECK (((start_state)::text = ANY "
                "((ARRAY['prepared'::character varying, "
                "'starting'::character varying, 'started'::character varying, "
                "'not_started'::character varying, "
                "'uncertain'::character varying])::text[])))"
            ),
            "fk_recording_egress_operations_call_id_calls": (
                "FOREIGN KEY (call_id) REFERENCES calls(id) ON DELETE RESTRICT"
            ),
            "pk_recording_egress_operations": "PRIMARY KEY (id)",
            "uq_recording_egress_operations_call_id": "UNIQUE (call_id)",
            "uq_recording_egress_operations_provider_egress_id": (
                "UNIQUE (provider_egress_id)"
            ),
        }
        assert foreign_key_delete_action == "r"
        assert {
            row["index_name"]: (
                row["indisunique"],
                tuple(row["columns"]),
            )
            for row in index_rows
        } == {
            "ix_recording_egress_operations_due_work": (
                False,
                (
                    "start_state",
                    "stop_requested_at",
                    "delete_requested_at",
                    "updated_at",
                ),
            ),
            "pk_recording_egress_operations": (True, ("id",)),
            "uq_recording_egress_operations_call_id": (True, ("call_id",)),
            "uq_recording_egress_operations_provider_egress_id": (
                True,
                ("provider_egress_id",),
            ),
        }

        assert set(operation_rows) == {
            call_ids["active"],
            call_ids["terminal"],
            call_ids["terminal_no_end"],
            call_ids["deleted"],
            call_ids["incomplete_room"],
            call_ids["blank_provider_key"],
            call_ids["blank_provider_url"],
        }
        private_columns = {
            "id",
            "call_id",
            "room_name",
            "legacy_incomplete",
            "expected_object_key",
            "provider_egress_id",
            "start_state",
            "start_attempted_at",
            "stop_requested_at",
            "delete_requested_at",
            "provider_terminal_at",
            "object_deleted_at",
            "last_reconciled_at",
            "last_error_code",
            "created_at",
            "updated_at",
        }
        assert all(set(row) == private_columns for row in operation_rows.values())
        assert call_ids["empty_metadata"] not in operation_rows

        active = operation_rows[call_ids["active"]]
        assert _as_uuid(active["id"]) == call_ids["active"]
        assert active["room_name"] == "room-synthetic-active"
        assert active["legacy_incomplete"] is False
        assert active["expected_object_key"] == active_object_key
        assert active["provider_egress_id"] is None
        assert active["start_state"] == "uncertain"
        assert active["stop_requested_at"] is None
        assert active["delete_requested_at"] is None

        terminal = operation_rows[call_ids["terminal"]]
        assert terminal["expected_object_key"] == (
            f"calls/{user_ids['terminal']}/{call_ids['terminal']}.ogg"
        )
        assert terminal["provider_egress_id"] == terminal_egress_id
        assert terminal["start_state"] == "started"
        assert terminal["stop_requested_at"] == terminal_ended_at
        assert terminal["delete_requested_at"] is None

        terminal_no_end = operation_rows[call_ids["terminal_no_end"]]
        assert terminal_no_end["provider_egress_id"] == terminal_no_end_egress_id
        assert terminal_no_end["start_state"] == "started"
        assert (
            migration_db_started_at
            <= terminal_no_end["stop_requested_at"]
            <= migration_db_finished_at
        )
        assert terminal_no_end["delete_requested_at"] is None

        deleted = operation_rows[call_ids["deleted"]]
        assert deleted["expected_object_key"] == deleted_object_key
        assert deleted["provider_egress_id"] == deleted_egress_id
        assert deleted["start_state"] == "started"
        assert deleted["stop_requested_at"] == deleted_at
        assert deleted["delete_requested_at"] == deleted_at

        incomplete = operation_rows[call_ids["incomplete_room"]]
        assert incomplete["room_name"] is None
        assert incomplete["legacy_incomplete"] is True
        assert incomplete["expected_object_key"] == incomplete_object_key
        assert incomplete["provider_egress_id"] is None
        assert incomplete["start_state"] == "uncertain"
        assert incomplete["stop_requested_at"] is None
        assert incomplete["delete_requested_at"] is None

        blank_provider_key = operation_rows[call_ids["blank_provider_key"]]
        assert blank_provider_key["room_name"] is None
        assert blank_provider_key["legacy_incomplete"] is True
        assert blank_provider_key["expected_object_key"] == blank_provider_object_key
        assert blank_provider_key["provider_egress_id"] is None
        assert blank_provider_key["start_state"] == "uncertain"

        blank_provider_url = operation_rows[call_ids["blank_provider_url"]]
        assert blank_provider_url["room_name"] == ("room-synthetic-blank-provider-url")
        assert blank_provider_url["legacy_incomplete"] is False
        assert blank_provider_url["expected_object_key"] == (
            f"calls/{user_ids['blank_provider_url']}/"
            f"{call_ids['blank_provider_url']}.ogg"
        )
        assert blank_provider_url["provider_egress_id"] is None
        assert blank_provider_url["start_state"] == "uncertain"

        for operation in operation_rows.values():
            assert operation["start_attempted_at"] is None
            assert operation["provider_terminal_at"] is None
            assert operation["object_deleted_at"] is None
            assert operation["last_reconciled_at"] is None
            assert operation["last_error_code"] is None
            assert (
                migration_db_started_at
                <= operation["created_at"]
                <= migration_db_finished_at
            )
            assert operation["updated_at"] == operation["created_at"]

        reconcile_events = [
            row for row in event_rows if row["topic"] == "recording.reconcile"
        ]
        assert {row["idempotency_key"] for row in reconcile_events} == {
            f"recording.reconcile:{call_ids['active']}:start",
            f"recording.reconcile:{call_ids['terminal']}:stop",
            f"recording.reconcile:{call_ids['terminal_no_end']}:stop",
            f"recording.reconcile:{call_ids['deleted']}:delete",
            f"recording.reconcile:{call_ids['incomplete_room']}:start",
            f"recording.reconcile:{call_ids['blank_provider_key']}:start",
            f"recording.reconcile:{call_ids['blank_provider_url']}:start",
        }
        for event in reconcile_events:
            operation_id = _as_uuid(event["aggregate_id"])
            assert event["aggregate_type"] == "recording-egress-operation"
            assert _as_payload(event["payload"]) == {"operation_id": str(operation_id)}
            assert event["status"] == "pending"
            assert event["attempt_count"] == 0
            assert event["last_error_code"] is None
            assert event["delivered_at"] is None
            migration_event_timestamps = {
                event["next_attempt_at"],
                event["created_at"],
                event["updated_at"],
            }
            assert len(migration_event_timestamps) == 1
            migration_event_timestamp = migration_event_timestamps.pop()
            assert (
                migration_db_started_at
                <= migration_event_timestamp
                <= migration_db_finished_at
            )

        legacy_events = {
            row["idempotency_key"]: row
            for row in event_rows
            if row["topic"] == "recording.stop"
        }
        assert set(legacy_events) == {
            f"recording.stop:{call_ids['terminal']}:delivered",
            f"recording.stop:{call_ids['terminal']}:failed",
            (f"recording.stop:{call_ids['empty_metadata']}:no-replacement"),
        }
        assert {
            key: dict(row) for key, row in legacy_events.items()
        } == retained_legacy_events
        assert removed_legacy_event_ids.isdisjoint(
            {_as_uuid(row["id"]) for row in event_rows}
        )

        operation_insert = sa.text(
            "INSERT INTO recording_egress_operations "
            "(id, call_id, room_name, legacy_incomplete, expected_object_key, "
            "provider_egress_id, start_state, start_attempted_at, "
            "stop_requested_at, delete_requested_at, object_deleted_at) "
            "VALUES (:id, :call_id, :room_name, :legacy_incomplete, "
            ":expected_object_key, :provider_egress_id, :start_state, "
            ":start_attempted_at, :stop_requested_at, :delete_requested_at, "
            ":object_deleted_at)"
        )

        async def assert_insert_succeeds(
            *,
            start_state: str,
            room_name: str | None = "room-synthetic-valid",
            legacy_incomplete: bool = False,
            provider_egress_id: str | None = None,
        ) -> None:
            operation_id = uuid4()
            async with migration_engine.begin() as connection:
                await connection.execute(
                    operation_insert,
                    {
                        "id": operation_id,
                        "call_id": call_ids["empty_metadata"],
                        "room_name": room_name,
                        "legacy_incomplete": legacy_incomplete,
                        "expected_object_key": "calls/synthetic-valid.ogg",
                        "provider_egress_id": provider_egress_id,
                        "start_state": start_state,
                        "start_attempted_at": None,
                        "stop_requested_at": None,
                        "delete_requested_at": None,
                        "object_deleted_at": None,
                    },
                )
                await connection.execute(
                    sa.text("DELETE FROM recording_egress_operations WHERE id = :id"),
                    {"id": operation_id},
                )

        async def assert_insert_fails(
            *,
            constraint_name: str,
            call_id: UUID,
            room_name: str | None = "room-synthetic-invalid",
            legacy_incomplete: bool = False,
            provider_egress_id: str | None = None,
            start_state: str = "uncertain",
            start_attempted_at: datetime | None = None,
            stop_requested_at: datetime | None = None,
            delete_requested_at: datetime | None = None,
            object_deleted_at: datetime | None = None,
        ) -> None:
            with pytest.raises(IntegrityError) as error:
                async with migration_engine.begin() as connection:
                    await connection.execute(
                        operation_insert,
                        {
                            "id": uuid4(),
                            "call_id": call_id,
                            "room_name": room_name,
                            "legacy_incomplete": legacy_incomplete,
                            "expected_object_key": "calls/synthetic-invalid.ogg",
                            "provider_egress_id": provider_egress_id,
                            "start_state": start_state,
                            "start_attempted_at": start_attempted_at,
                            "stop_requested_at": stop_requested_at,
                            "delete_requested_at": delete_requested_at,
                            "object_deleted_at": object_deleted_at,
                        },
                    )
            assert constraint_name in str(error.value.orig)

        for valid_state in (
            "prepared",
            "starting",
            "started",
            "not_started",
            "uncertain",
        ):
            await assert_insert_succeeds(
                start_state=valid_state,
                provider_egress_id=(
                    "EG_synthetic_valid_started" if valid_state == "started" else None
                ),
            )
        await assert_insert_succeeds(
            start_state="started",
            room_name=None,
            legacy_incomplete=True,
            provider_egress_id="EG_synthetic_valid_legacy_started",
        )
        await assert_insert_succeeds(
            start_state="uncertain",
            room_name=None,
            legacy_incomplete=True,
        )

        await assert_insert_fails(
            constraint_name="uq_recording_egress_operations_call_id",
            call_id=call_ids["active"],
        )
        await assert_insert_fails(
            constraint_name="uq_recording_egress_operations_provider_egress_id",
            call_id=call_ids["empty_metadata"],
            provider_egress_id=terminal_egress_id,
            start_state="started",
        )
        await assert_insert_fails(
            constraint_name="ck_recording_egress_operations_start_state_allowed",
            call_id=call_ids["empty_metadata"],
            start_state="mystery",
        )
        await assert_insert_fails(
            constraint_name=(
                "ck_recording_egress_operations_provider_identity_consistent"
            ),
            call_id=call_ids["empty_metadata"],
            start_state="started",
        )
        await assert_insert_fails(
            constraint_name="ck_recording_egress_operations_legacy_room_consistent",
            call_id=call_ids["empty_metadata"],
            room_name=None,
        )
        await assert_insert_fails(
            constraint_name=(
                "ck_recording_egress_operations_prepared_attempt_consistent"
            ),
            call_id=call_ids["empty_metadata"],
            start_state="prepared",
            start_attempted_at=active_started_at,
        )
        await assert_insert_fails(
            constraint_name="ck_recording_egress_operations_delete_implies_stop",
            call_id=call_ids["empty_metadata"],
            delete_requested_at=deleted_at,
        )
        await assert_insert_fails(
            constraint_name=(
                "ck_recording_egress_operations_object_delete_implies_request"
            ),
            call_id=call_ids["empty_metadata"],
            stop_requested_at=deleted_ended_at,
            object_deleted_at=deleted_at,
        )

        first_revision_event_ids = {_as_uuid(row["id"]) for row in reconcile_events}
        async with migration_engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "UPDATE outbox_events SET status = 'delivered', "
                    "delivered_at = :finished_at, last_error_code = NULL "
                    "WHERE idempotency_key = :idempotency_key"
                ),
                {
                    "finished_at": migration_db_finished_at,
                    "idempotency_key": (
                        f"recording.reconcile:{call_ids['active']}:start"
                    ),
                },
            )
            await connection.execute(
                sa.text(
                    "UPDATE outbox_events SET status = 'failed', "
                    "delivered_at = NULL, "
                    "last_error_code = 'recording_unresolved' "
                    "WHERE idempotency_key = :idempotency_key"
                ),
                {
                    "idempotency_key": (
                        f"recording.reconcile:{call_ids['terminal']}:stop"
                    )
                },
            )

        await migration_engine.dispose()
        migration_engine = None
        run_alembic("downgrade", "0013_outbox_routing_target")
        migration_engine = create_async_engine(migration_url)
        async with migration_engine.connect() as connection:
            downgraded_revision = await connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            )
            operation_table = await connection.scalar(
                sa.text("SELECT to_regclass('public.recording_egress_operations')")
            )
            remaining_revision_events = await connection.scalar(
                sa.text(
                    "SELECT COUNT(*) FROM outbox_events "
                    "WHERE topic = 'recording.reconcile' "
                    "AND aggregate_type = 'recording-egress-operation'"
                )
            )
            legacy_after_downgrade = {
                row["idempotency_key"]: dict(row)
                for row in (
                    await connection.execute(
                        sa.text(
                            "SELECT * FROM outbox_events "
                            "WHERE topic = 'recording.stop' "
                            "ORDER BY idempotency_key"
                        )
                    )
                ).mappings()
            }
            reupgrade_db_started_at = await connection.scalar(
                sa.text("SELECT clock_timestamp()")
            )
        assert downgraded_revision == "0013_outbox_routing_target"
        assert operation_table is None
        assert remaining_revision_events == 0
        assert legacy_after_downgrade == retained_legacy_events
        assert removed_legacy_event_ids.isdisjoint(
            {_as_uuid(row["id"]) for row in legacy_after_downgrade.values()}
        )
        assert isinstance(reupgrade_db_started_at, datetime)

        await migration_engine.dispose()
        migration_engine = None
        run_alembic("upgrade", "0014_recording_egress_ops")
        migration_engine = create_async_engine(migration_url)
        async with migration_engine.connect() as connection:
            reupgrade_db_finished_at = await connection.scalar(
                sa.text("SELECT clock_timestamp()")
            )
            reupgraded_revision = await connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            )
            reupgraded_operation_ids = set(
                (
                    await connection.execute(
                        sa.text("SELECT id FROM recording_egress_operations")
                    )
                ).scalars()
            )
            replacement_events = (
                (
                    await connection.execute(
                        sa.text(
                            "SELECT * "
                            "FROM outbox_events "
                            "WHERE topic = 'recording.reconcile' "
                            "ORDER BY idempotency_key"
                        )
                    )
                )
                .mappings()
                .all()
            )
            legacy_after_reupgrade = {
                row["idempotency_key"]: dict(row)
                for row in (
                    await connection.execute(
                        sa.text(
                            "SELECT * FROM outbox_events "
                            "WHERE topic = 'recording.stop' "
                            "ORDER BY idempotency_key"
                        )
                    )
                ).mappings()
            }

        assert reupgraded_revision == "0014_recording_egress_ops"
        assert {_as_uuid(value) for value in reupgraded_operation_ids} == set(
            operation_rows
        )
        assert {row["idempotency_key"] for row in replacement_events} == {
            row["idempotency_key"] for row in reconcile_events
        }
        assert first_revision_event_ids.isdisjoint(
            {_as_uuid(row["id"]) for row in replacement_events}
        )
        assert legacy_after_reupgrade == retained_legacy_events
        assert removed_legacy_event_ids.isdisjoint(
            {_as_uuid(row["id"]) for row in legacy_after_reupgrade.values()}
        )
        for event in replacement_events:
            operation_id = _as_uuid(event["aggregate_id"])
            assert event["aggregate_type"] == "recording-egress-operation"
            assert _as_payload(event["payload"]) == {"operation_id": str(operation_id)}
            assert event["status"] == "pending"
            assert event["attempt_count"] == 0
            assert event["last_error_code"] is None
            assert event["delivered_at"] is None
            replacement_timestamps = {
                event["next_attempt_at"],
                event["created_at"],
                event["updated_at"],
            }
            assert len(replacement_timestamps) == 1
            replacement_timestamp = replacement_timestamps.pop()
            assert (
                reupgrade_db_started_at
                <= replacement_timestamp
                <= reupgrade_db_finished_at
            )
    finally:
        if migration_engine is not None:
            await migration_engine.dispose()
        try:
            async with admin_engine.connect() as connection:
                await connection.execute(
                    sa.text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                    ),
                    {"database_name": database_name},
                )
                await connection.execute(
                    sa.text(f'DROP DATABASE IF EXISTS "{database_name}"')
                )
        finally:
            await admin_engine.dispose()
