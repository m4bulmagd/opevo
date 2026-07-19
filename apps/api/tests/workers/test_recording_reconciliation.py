from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.call import Call
from app.models.recording_egress_operation import RecordingEgressOperation
from app.providers.livekit_recording.base import (
    RecordingEgressResult,
    RecordingEgressSnapshot,
)
from app.repositories.call_repository import CallRepository
from app.repositories.recording_egress_operation_repository import (
    RecordingEgressOperationRepository,
)
from app.services.recording_lifecycle_service import (
    START_RESULT_LEASE,
    RecordingLifecycleService,
)
from app.workers.jobs.recording_reconciliation import (
    ReconciliationResult,
    RecordingReconciler,
)


NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
OBJECT_KEY = "calls/user-id/call-id.ogg"


@dataclass(frozen=True)
class MatrixCase:
    name: str
    start_state: Literal[
        "prepared", "starting", "started", "not_started", "uncertain", "missing"
    ]
    provider_egress_id: str | None = None
    stop_requested: bool = False
    delete_requested: bool = False
    provider_terminal: bool = False
    legacy_incomplete: bool = False
    snapshots: tuple[RecordingEgressSnapshot, ...] = ()
    ensure_failures: frozenset[str] = frozenset()
    storage_result: Literal["ok", "missing", "error"] = "ok"
    reconcile_twice: bool = False
    expected_result: ReconciliationResult = ReconciliationResult("complete")
    expected_state: str | None = None
    expected_error: str | None = None
    operation_removed: bool = False
    expected_provider_calls: tuple[tuple[str, str], ...] = ()
    expected_storage_calls: tuple[tuple[str, str], ...] = ()


MATRIX = (
    MatrixCase(
        name="stale prepared",
        start_state="prepared",
        expected_state="not_started",
    ),
    MatrixCase(
        name="stale starting without an exact match",
        start_state="starting",
        expected_result=ReconciliationResult("retry", "recording_unresolved"),
        expected_state="uncertain",
        expected_error="recording_unresolved",
        expected_provider_calls=(("list_room_egresses", "room-owned"),),
    ),
    MatrixCase(
        name="started without stop intent",
        start_state="started",
        provider_egress_id="EG_known",
        expected_state="started",
    ),
    MatrixCase(
        name="started with stop intent",
        start_state="started",
        provider_egress_id="EG_known",
        stop_requested=True,
        expected_state="started",
        expected_provider_calls=(("ensure_not_running", "EG_known"),),
    ),
    MatrixCase(
        name="started with deletion intent",
        start_state="started",
        provider_egress_id="EG_known",
        stop_requested=True,
        delete_requested=True,
        operation_removed=True,
        expected_provider_calls=(("ensure_not_running", "EG_known"),),
        expected_storage_calls=(("delete_object", OBJECT_KEY),),
    ),
    MatrixCase(
        name="definitely not started with deletion intent and missing object",
        start_state="not_started",
        stop_requested=True,
        delete_requested=True,
        storage_result="missing",
        operation_removed=True,
        expected_storage_calls=(("delete_object", OBJECT_KEY),),
    ),
    MatrixCase(
        name="uncertain with one exact match",
        start_state="uncertain",
        snapshots=(
            RecordingEgressSnapshot(
                egress_id="EG_exact",
                room_name="room-owned",
                status=1,
                object_key=OBJECT_KEY,
            ),
        ),
        expected_state="started",
        expected_provider_calls=(("list_room_egresses", "room-owned"),),
    ),
    MatrixCase(
        name="uncertain with mismatched path only",
        start_state="uncertain",
        snapshots=(
            RecordingEgressSnapshot(
                egress_id="EG_other",
                room_name="room-owned",
                status=1,
                object_key="calls/other/object.ogg",
            ),
        ),
        expected_result=ReconciliationResult(
            "retry", "recording_identity_mismatch"
        ),
        expected_state="uncertain",
        expected_error="recording_identity_mismatch",
        expected_provider_calls=(("list_room_egresses", "room-owned"),),
    ),
    MatrixCase(
        name="uncertain with empty active list",
        start_state="uncertain",
        expected_result=ReconciliationResult("retry", "recording_unresolved"),
        expected_state="uncertain",
        expected_error="recording_unresolved",
        expected_provider_calls=(("list_room_egresses", "room-owned"),),
    ),
    MatrixCase(
        name="uncertain with multiple distinct exact matches",
        start_state="uncertain",
        snapshots=(
            RecordingEgressSnapshot(
                egress_id="EG_first",
                room_name="room-owned",
                status=1,
                object_key=OBJECT_KEY,
            ),
            RecordingEgressSnapshot(
                egress_id="EG_first",
                room_name="room-owned",
                status=2,
                object_key=OBJECT_KEY,
            ),
            RecordingEgressSnapshot(
                egress_id="EG_second",
                room_name="room-owned",
                status=1,
                object_key=OBJECT_KEY,
            ),
        ),
        ensure_failures=frozenset({"EG_first"}),
        expected_result=ReconciliationResult(
            "retry", "recording_identity_conflict"
        ),
        expected_state="uncertain",
        expected_error="recording_identity_conflict",
        expected_provider_calls=(
            ("list_room_egresses", "room-owned"),
            ("ensure_not_running", "EG_first"),
            ("ensure_not_running", "EG_second"),
        ),
    ),
    MatrixCase(
        name="legacy incomplete with unknown identity",
        start_state="uncertain",
        legacy_incomplete=True,
        expected_result=ReconciliationResult(
            "retry", "recording_legacy_incomplete"
        ),
        expected_state="uncertain",
        expected_error="recording_legacy_incomplete",
    ),
    MatrixCase(
        name="missing operation",
        start_state="missing",
        operation_removed=True,
    ),
    MatrixCase(
        name="storage transient failure after provider terminality",
        start_state="started",
        provider_egress_id="EG_known",
        stop_requested=True,
        delete_requested=True,
        provider_terminal=True,
        storage_result="error",
        expected_result=ReconciliationResult(
            "retry", "recording_storage_unavailable"
        ),
        expected_state="started",
        expected_error="recording_storage_unavailable",
        expected_storage_calls=(("delete_object", OBJECT_KEY),),
    ),
    MatrixCase(
        name="handler retry after operation deletion",
        start_state="not_started",
        stop_requested=True,
        delete_requested=True,
        reconcile_twice=True,
        operation_removed=True,
        expected_storage_calls=(("delete_object", OBJECT_KEY),),
    ),
)


class TrackingSessionFactory:
    def __init__(self, base_factory) -> None:
        self.base_factory = base_factory
        self.open_contexts = 0

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[AsyncSession]:
        async with self.base_factory() as session:
            self.open_contexts += 1
            try:
                yield session
            finally:
                self.open_contexts -= 1


class FakeProvider:
    def __init__(
        self,
        tracker: TrackingSessionFactory,
        *,
        snapshots: tuple[RecordingEgressSnapshot, ...] = (),
        ensure_failures: frozenset[str] = frozenset(),
        list_error: Exception | None = None,
    ) -> None:
        self.tracker = tracker
        self.snapshots = snapshots
        self.ensure_failures = ensure_failures
        self.list_error = list_error
        self.calls: list[tuple[str, str]] = []
        self.open_count_during_provider_calls = 0

    def _record(self, operation: str, value: str) -> None:
        self.open_count_during_provider_calls = max(
            self.open_count_during_provider_calls,
            self.tracker.open_contexts,
        )
        assert self.tracker.open_contexts == 0
        self.calls.append((operation, value))

    async def list_room_egresses(
        self,
        *,
        room_name: str,
    ) -> tuple[RecordingEgressSnapshot, ...]:
        self._record("list_room_egresses", room_name)
        if self.list_error is not None:
            raise self.list_error
        return self.snapshots

    async def ensure_not_running(self, egress_id: str) -> None:
        self._record("ensure_not_running", egress_id)
        if egress_id in self.ensure_failures:
            raise RuntimeError("provider-secret-that-must-not-be-persisted")

    async def start_room_recording(self, **_kwargs) -> None:
        pytest.fail("reconciliation must never start another recording")


class FakeStorage:
    def __init__(
        self,
        tracker: TrackingSessionFactory,
        *,
        result: Literal["ok", "missing", "error"] = "ok",
    ) -> None:
        self.tracker = tracker
        self.result = result
        self.calls: list[tuple[str, str]] = []

    async def delete_object(self, *, object_key: str) -> None:
        assert self.tracker.open_contexts == 0
        self.calls.append(("delete_object", object_key))
        if self.result == "missing":
            raise FileNotFoundError(object_key)
        if self.result == "error":
            raise RuntimeError("storage-secret-that-must-not-be-persisted")


async def _persist_operation(
    session: AsyncSession,
    *,
    user_id: UUID,
    case: MatrixCase,
) -> tuple[UUID, UUID]:
    call = Call(
        user_id=user_id,
        status="completed",
        duration_seconds=1,
        livekit_room_id=None if case.legacy_incomplete else "room-owned",
        deleted_at=NOW if case.delete_requested else None,
        recording_object_key=(
            OBJECT_KEY
            if case.start_state in {"started", "uncertain"}
            and not case.delete_requested
            else None
        ),
        recording_egress_id=(
            case.provider_egress_id if not case.delete_requested else None
        ),
        recording_url=(
            "https://private.invalid/existing"
            if case.start_state in {"started", "uncertain"}
            and not case.delete_requested
            else None
        ),
    )
    session.add(call)
    await session.flush()
    operation = RecordingEgressOperation(
        call_id=call.id,
        room_name=None if case.legacy_incomplete else "room-owned",
        legacy_incomplete=case.legacy_incomplete,
        expected_object_key=OBJECT_KEY,
        provider_egress_id=case.provider_egress_id,
        start_state=case.start_state,
        start_attempted_at=(
            (NOW - START_RESULT_LEASE).replace(tzinfo=None)
            if case.start_state == "starting"
            else None
        ),
        stop_requested_at=NOW if case.stop_requested else None,
        delete_requested_at=NOW if case.delete_requested else None,
        provider_terminal_at=NOW if case.provider_terminal else None,
        created_at=(NOW - START_RESULT_LEASE).replace(tzinfo=None),
        updated_at=NOW,
    )
    session.add(operation)
    await session.commit()
    return call.id, operation.id


@pytest.mark.anyio
@pytest.mark.parametrize("case", MATRIX, ids=lambda case: case.name)
async def test_recording_reconciliation_matrix(
    db_session: AsyncSession,
    active_user,
    case: MatrixCase,
) -> None:
    operation_id = uuid4()
    call_id: UUID | None = None
    if case.start_state != "missing":
        call_id, operation_id = await _persist_operation(
            db_session,
            user_id=active_user.id,
            case=case,
        )

    tracker = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )
    provider = FakeProvider(
        tracker,
        snapshots=case.snapshots,
        ensure_failures=case.ensure_failures,
    )
    storage = FakeStorage(tracker, result=case.storage_result)
    reconciler = RecordingReconciler(
        tracker,
        provider,
        storage,
        now_provider=lambda: NOW,
    )

    result = await reconciler.reconcile(operation_id)
    if case.reconcile_twice:
        assert result == ReconciliationResult("complete")
        result = await reconciler.reconcile(operation_id)

    assert result == case.expected_result
    assert provider.calls == list(case.expected_provider_calls)
    assert storage.calls == list(case.expected_storage_calls)
    assert tracker.open_contexts == 0

    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    if case.operation_removed:
        assert stored is None
    else:
        assert stored is not None
        assert stored.start_state == case.expected_state
        assert stored.last_error_code == case.expected_error
        assert stored.last_reconciled_at is not None
        if case.name == "started with stop intent":
            assert stored.provider_terminal_at is not None
            assert stored.object_deleted_at is None
        if case.name == "uncertain with one exact match":
            assert stored.provider_egress_id == "EG_exact"
        if case.name == "storage transient failure after provider terminality":
            assert stored.object_deleted_at is None

    if call_id is not None:
        call = await db_session.get(Call, call_id)
        assert call is not None
        if case.name == "uncertain with one exact match":
            assert call.recording_object_key == OBJECT_KEY
            assert call.recording_egress_id == "EG_exact"
            assert call.recording_url == "https://private.invalid/existing"
        if case.name == "uncertain with multiple distinct exact matches":
            assert call.recording_object_key is None
            assert call.recording_egress_id is None
            assert call.recording_url is None
        if case.delete_requested:
            assert call.deleted_at is not None
            assert call.recording_object_key is None
            assert call.recording_egress_id is None
            assert call.recording_url is None


@pytest.mark.anyio
async def test_provider_and_storage_io_never_run_in_a_session_context(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(
        name="boundary",
        start_state="started",
        provider_egress_id="EG_exact",
        stop_requested=True,
        delete_requested=True,
    )
    _, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    session_tracker = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )
    provider = FakeProvider(session_tracker)
    storage = FakeStorage(session_tracker)

    result = await RecordingReconciler(
        session_tracker,
        provider,
        storage,
        now_provider=lambda: NOW,
    ).reconcile(operation_id)

    assert result == ReconciliationResult("complete")
    assert provider.calls == [
        ("ensure_not_running", "EG_exact"),
    ]
    assert storage.calls == [
        ("delete_object", "calls/user-id/call-id.ogg"),
    ]
    assert provider.open_count_during_provider_calls == 0


@pytest.mark.anyio
async def test_starting_without_attempt_timestamp_becomes_uncertain_without_starting(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(name="missing attempt", start_state="starting")
    _, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert operation is not None
    operation.start_attempted_at = None
    await db_session.commit()
    tracker = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )
    provider = FakeProvider(tracker)

    result = await RecordingReconciler(
        tracker,
        provider,
        FakeStorage(tracker),
        now_provider=lambda: NOW,
    ).reconcile(operation_id)

    assert result == ReconciliationResult("retry", "recording_unresolved")
    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored is not None
    assert stored.start_state == "uncertain"
    assert provider.calls == [("list_room_egresses", "room-owned")]


@pytest.mark.anyio
async def test_identity_conflict_is_sticky_across_later_singleton_listing(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(name="sticky conflict", start_state="uncertain")
    call_id, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert operation is not None
    operation.last_error_code = "recording_identity_conflict"
    await db_session.commit()
    tracker = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )
    provider = FakeProvider(
        tracker,
        snapshots=(
            RecordingEgressSnapshot(
                egress_id="EG_later",
                room_name="room-owned",
                status=1,
                object_key=OBJECT_KEY,
            ),
        ),
    )

    result = await RecordingReconciler(
        tracker,
        provider,
        FakeStorage(tracker),
        now_provider=lambda: NOW,
    ).reconcile(operation_id)

    assert result == ReconciliationResult("retry", "recording_identity_conflict")
    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    call = await db_session.get(Call, call_id)
    assert stored is not None
    assert stored.start_state == "uncertain"
    assert stored.provider_egress_id is None
    assert stored.last_error_code == "recording_identity_conflict"
    assert call is not None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None


@pytest.mark.anyio
async def test_exact_recovery_never_restores_projection_to_tombstoned_call(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(
        name="tombstoned exact",
        start_state="uncertain",
        stop_requested=True,
        delete_requested=False,
    )
    call_id, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    call = await db_session.get(Call, call_id)
    assert call is not None
    call.deleted_at = NOW
    call.recording_object_key = None
    call.recording_egress_id = None
    call.recording_url = None
    await db_session.commit()
    tracker = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )
    provider = FakeProvider(
        tracker,
        snapshots=(
            RecordingEgressSnapshot(
                egress_id="EG_exact",
                room_name="room-owned",
                status=1,
                object_key=OBJECT_KEY,
            ),
        ),
    )

    result = await RecordingReconciler(
        tracker,
        provider,
        FakeStorage(tracker),
        now_provider=lambda: NOW,
    ).reconcile(operation_id)

    assert result == ReconciliationResult("complete")
    db_session.expire_all()
    call = await db_session.get(Call, call_id)
    operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert call is not None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None
    assert operation is not None
    assert operation.provider_egress_id == "EG_exact"
    assert operation.provider_terminal_at is not None


class SimulatedWorkerCrash(RuntimeError):
    pass


class InterruptingSessionFactory:
    def __init__(self, base_factory, *, fail_on_entry: int) -> None:
        self.base_factory = base_factory
        self.fail_on_entry = fail_on_entry
        self.entries = 0

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[AsyncSession]:
        self.entries += 1
        if self.entries == self.fail_on_entry:
            raise SimulatedWorkerCrash
        async with self.base_factory() as session:
            yield session


@pytest.mark.anyio
async def test_object_deletion_is_committed_before_operation_removal(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(
        name="two commit deletion",
        start_state="not_started",
        stop_requested=True,
        delete_requested=True,
    )
    _, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    interrupted_factory = InterruptingSessionFactory(base_factory, fail_on_entry=3)
    tracker = TrackingSessionFactory(base_factory)
    storage = FakeStorage(tracker)

    with pytest.raises(SimulatedWorkerCrash):
        await RecordingReconciler(
            interrupted_factory,
            FakeProvider(tracker),
            storage,
            now_provider=lambda: NOW,
        ).reconcile(operation_id)

    async with base_factory() as session:
        durable = await session.get(RecordingEgressOperation, operation_id)
        assert durable is not None
        assert durable.object_deleted_at is not None

    result = await RecordingReconciler(
        tracker,
        FakeProvider(tracker),
        storage,
        now_provider=lambda: NOW + timedelta(seconds=1),
    ).reconcile(operation_id)

    assert result == ReconciliationResult("complete")
    assert storage.calls == [("delete_object", OBJECT_KEY)]
    async with base_factory() as session:
        assert await session.get(RecordingEgressOperation, operation_id) is None


@pytest.mark.anyio
async def test_provider_failure_is_bounded_and_does_not_persist_exception_text(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(name="provider unavailable", start_state="uncertain")
    _, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    tracker = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )

    result = await RecordingReconciler(
        tracker,
        FakeProvider(
            tracker,
            list_error=RuntimeError("LIVEKIT_CREDENTIAL_SENTINEL"),
        ),
        FakeStorage(tracker),
        now_provider=lambda: NOW,
    ).reconcile(operation_id)

    assert result == ReconciliationResult(
        "retry", "recording_provider_unavailable"
    )
    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored is not None
    assert stored.last_error_code == "recording_provider_unavailable"
    assert "LIVEKIT_CREDENTIAL_SENTINEL" not in repr(stored.__dict__)


class ConcurrentSuccessProvider(FakeProvider):
    def __init__(self, tracker: TrackingSessionFactory, session_factory, operation_id):
        super().__init__(tracker)
        self.session_factory = session_factory
        self.operation_id = operation_id

    async def list_room_egresses(
        self,
        *,
        room_name: str,
    ) -> tuple[RecordingEgressSnapshot, ...]:
        self._record("list_room_egresses", room_name)
        async with self.session_factory() as session:
            await RecordingLifecycleService(
                session,
                now_provider=lambda: NOW,
            ).record_start_success(
                self.operation_id,
                RecordingEgressResult(
                    egress_id="EG_concurrent",
                    object_key=OBJECT_KEY,
                    url="https://private.invalid/concurrent",
                ),
            )
            await session.commit()
        return ()


@pytest.mark.anyio
async def test_concurrent_incompatible_change_is_not_overwritten_after_provider_io(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(name="concurrent result", start_state="uncertain")
    _, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)
    provider = ConcurrentSuccessProvider(tracker, base_factory, operation_id)

    result = await RecordingReconciler(
        tracker,
        provider,
        FakeStorage(tracker),
        now_provider=lambda: NOW,
    ).reconcile(operation_id)

    assert result == ReconciliationResult("retry", "recording_unresolved")
    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored is not None
    assert stored.start_state == "started"
    assert stored.provider_egress_id == "EG_concurrent"
    assert stored.last_error_code is None


class ConcurrentIdentityStorage(FakeStorage):
    def __init__(
        self,
        tracker: TrackingSessionFactory,
        session_factory,
        operation_id: UUID,
    ) -> None:
        super().__init__(tracker)
        self.session_factory = session_factory
        self.operation_id = operation_id

    async def delete_object(self, *, object_key: str) -> None:
        await super().delete_object(object_key=object_key)
        async with self.session_factory() as session:
            operations = RecordingEgressOperationRepository(session)
            discovered = await operations.get_by_id(self.operation_id)
            assert discovered is not None
            call = await CallRepository(
                session
            ).get_by_id_including_deleted_for_update(discovered.call_id)
            assert call is not None
            operation = await operations.get_by_id_for_update(self.operation_id)
            assert operation is not None
            operation.provider_egress_id = "EG_concurrent"
            await session.commit()


@pytest.mark.anyio
async def test_concurrent_identity_change_blocks_object_deletion_persistence(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(
        name="storage identity race",
        start_state="started",
        provider_egress_id="EG_original",
        stop_requested=True,
        delete_requested=True,
        provider_terminal=True,
    )
    _, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)

    result = await RecordingReconciler(
        tracker,
        FakeProvider(tracker),
        ConcurrentIdentityStorage(tracker, base_factory, operation_id),
        now_provider=lambda: NOW,
    ).reconcile(operation_id)

    assert result == ReconciliationResult("retry", "recording_unresolved")
    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored is not None
    assert stored.provider_egress_id == "EG_concurrent"
    assert stored.object_deleted_at is None
