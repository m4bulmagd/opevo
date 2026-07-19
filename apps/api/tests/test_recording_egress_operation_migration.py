from __future__ import annotations

from datetime import UTC, datetime
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import UUID, uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call
from app.models.recording_egress_operation import (
    RECORDING_START_STATES,
    RecordingEgressOperation,
)
from app.models.user import User
from app.repositories.recording_egress_operation_repository import (
    RecordingEgressOperationRepository,
)


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0014_add_recording_egress_operations.py"
)


def _load_migration() -> ModuleType:
    assert MIGRATION_PATH.exists(), "Recording egress operation migration must exist"
    spec = spec_from_file_location("recording_egress_operation_migration", MIGRATION_PATH)
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
        clerk_user_id=f"recording_operation_{suffix}_{uuid4().hex}",
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
    assert (await repository.observability_snapshot()).counts == {
        "prepared": 1,
        "starting": 0,
        "started": 0,
        "not_started": 0,
        "uncertain": 0,
    }

    await repository.delete(operation)

    assert await repository.get_by_id(operation.id) is None
    assert (await repository.observability_snapshot()).counts == {
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

    assert (await repository.observability_snapshot()).counts == {
        state: 1 for state in state_inputs
    }


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
) -> None:
    now = datetime(2026, 7, 19, 8, tzinfo=UTC)
    connection.execute(
        outbox_events.insert().values(
            id=uuid4(),
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


def test_sqlite_migration_backfills_operations_and_reference_only_work() -> None:
    migration = _load_migration()
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    calls, outbox_events = _legacy_tables(metadata)
    metadata.create_all(engine)
    now = datetime(2026, 7, 19, 8, tzinfo=UTC)
    ids = {
        "known_terminal": uuid4(),
        "object_active": uuid4(),
        "empty_object_active": uuid4(),
        "url_active": uuid4(),
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
            "deleted_at": None,
            "recording_object_key": "calls/existing/object.ogg",
            "recording_egress_id": None,
            "recording_url": None,
        },
        {
            "id": ids["empty_object_active"],
            "user_id": user_ids["empty_object_active"],
            "livekit_room_id": "room-empty-object-active",
            "status": "connected",
            "deleted_at": None,
            "recording_object_key": "",
            "recording_egress_id": None,
            "recording_url": None,
        },
        {
            "id": ids["url_active"],
            "user_id": user_ids["url_active"],
            "livekit_room_id": "room-url-active",
            "status": "pending",
            "deleted_at": None,
            "recording_object_key": None,
            "recording_egress_id": None,
            "recording_url": "https://playback.example/url-only",
        },
        {
            "id": ids["missing_room"],
            "user_id": user_ids["missing_room"],
            "livekit_room_id": None,
            "status": "connected",
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
            "deleted_at": now,
            "recording_object_key": "calls/legacy/deleted.ogg",
            "recording_egress_id": "EG_deleted",
            "recording_url": None,
        },
        {
            "id": ids["known_active"],
            "user_id": user_ids["known_active"],
            "livekit_room_id": "room-known-active",
            "status": "connected",
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

    assert set(operation_rows) == set(ids.values())
    known = operation_rows[ids["known_terminal"]]
    assert _as_uuid(known["id"]) == ids["known_terminal"]
    assert known["start_state"] == "started"
    assert known["provider_egress_id"] == "EG_test"
    assert known["expected_object_key"] == (
        f"calls/{user_ids['known_terminal']}/{ids['known_terminal']}.ogg"
    )
    assert known["stop_requested_at"] is not None
    assert known["delete_requested_at"] is None

    object_only = operation_rows[ids["object_active"]]
    assert object_only["start_state"] == "uncertain"
    assert object_only["expected_object_key"] == "calls/existing/object.ogg"
    assert object_only["stop_requested_at"] is None
    assert object_only["delete_requested_at"] is None

    empty_object = operation_rows[ids["empty_object_active"]]
    assert empty_object["start_state"] == "uncertain"
    assert empty_object["expected_object_key"] == (
        f"calls/{user_ids['empty_object_active']}/{ids['empty_object_active']}.ogg"
    )

    url_only = operation_rows[ids["url_active"]]
    assert url_only["start_state"] == "uncertain"
    assert url_only["expected_object_key"] == (
        f"calls/{user_ids['url_active']}/{ids['url_active']}.ogg"
    )

    missing_room = operation_rows[ids["missing_room"]]
    assert missing_room["room_name"] is None
    assert bool(missing_room["legacy_incomplete"]) is True
    assert operation_rows[ids["object_active"]]["legacy_incomplete"] in (False, 0)

    deleted = operation_rows[ids["deleted"]]
    assert deleted["stop_requested_at"] is not None
    assert deleted["delete_requested_at"] is not None

    reconcile_events = [
        row for row in event_rows if row["topic"] == "recording.reconcile"
    ]
    assert {row["idempotency_key"].rsplit(":", 1)[-1] for row in reconcile_events} == {
        "delete",
        "start",
        "stop",
    }
    assert {
        _as_uuid(row["aggregate_id"]) for row in reconcile_events
    } == {
        ids["known_terminal"],
        ids["object_active"],
        ids["empty_object_active"],
        ids["url_active"],
        ids["deleted"],
    }
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
    assert next(
        row for row in legacy_events if row["idempotency_key"].endswith(":delivered")
    )["status"] == "delivered"

    for original in seeded:
        persisted = call_rows[original["id"]]
        assert persisted["recording_object_key"] == original["recording_object_key"]
        assert persisted["recording_egress_id"] == original["recording_egress_id"]
        assert persisted["recording_url"] == original["recording_url"]

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
    assert operations.timeline[1][1] == (
        "ix_recording_egress_operations_due_work",
    )
    assert operations.timeline[1][2] == {
        "table_name": "recording_egress_operations"
    }
    assert operations.timeline[2][1] == ("recording_egress_operations",)
