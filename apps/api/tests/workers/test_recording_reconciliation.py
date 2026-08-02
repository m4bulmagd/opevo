from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.call import Call
from app.models.outbox_event import OutboxEvent
from app.models.recording_egress_operation import RecordingEgressOperation
from app.core.provider_failures import ProviderFailure
from app.providers.livekit_recording.base import (
    RecordingEgressResult,
    RecordingEgressSnapshot,
)
from app.repositories.call_repository import CallRepository
from app.repositories.recording_egress_operation_repository import (
    RecordingEgressOperationRepository,
)
from app.services.recording_lifecycle_service import (
    RECORDING_AGGREGATE_TYPE,
    START_RESULT_LEASE,
    RecordingLifecycleService,
)
from app.workers.jobs import outbox_topics
from app.workers.jobs.outbox_delivery import OutboxDeliveryError
from app.workers.jobs.outbox_topics import deliver_recording_reconcile
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
    expected_conflict_category: str | None = None


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
        expected_result=ReconciliationResult("retry", "recording_identity_mismatch"),
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
        expected_result=ReconciliationResult("retry", "recording_identity_conflict"),
        expected_conflict_category="multiple_exact_match",
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
        expected_result=ReconciliationResult("retry", "recording_legacy_incomplete"),
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
        expected_result=ReconciliationResult("retry", "recording_storage_unavailable"),
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


class _HandlerReconciler:
    def __init__(self, result: object | Exception) -> None:
        self.result = result
        self.calls: list[UUID] = []

    async def reconcile(self, operation_id: UUID) -> object:
        self.calls.append(operation_id)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _RecordingObservability:
    def __init__(self) -> None:
        self.results: list[str] = []
        self.multiple_exact_count = 0
        self.events: list[str] = []

    def record_recording_reconciliation_result(self, result: str) -> None:
        self.results.append(result)
        self.events.append(f"result:{result}")

    def record_multiple_exact_match_conflict(self) -> None:
        self.multiple_exact_count += 1
        self.events.append("multiple_exact_match")


def _recording_reconcile_event(operation_id: UUID) -> OutboxEvent:
    return OutboxEvent(
        id=uuid4(),
        idempotency_key=f"recording.reconcile:{operation_id}:task7-handler",
        topic="recording.reconcile",
        aggregate_type="recording-egress-operation",
        aggregate_id=operation_id,
        payload={"operation_id": str(operation_id)},
        status="processing",
        attempt_count=1,
        next_attempt_at=NOW,
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
            raise ProviderFailure(
                provider="s3",
                operation="delete_object",
                disposition="retryable",
                error_class="unavailable",
            )


class ForcedConflictPersistenceReconciler(RecordingReconciler):
    def __init__(
        self,
        *args,
        persistence_status: Literal["missing", "changed"],
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.persistence_status = persistence_status

    async def _persist_identity_conflict(self, _snapshot):
        return self.persistence_status, None


class SimulatedStopInterruption(BaseException):
    pass


class InterruptingConflictProvider(FakeProvider):
    def __init__(
        self,
        tracker: TrackingSessionFactory,
        session_factory,
        operation_id: UUID,
        *,
        snapshots: tuple[RecordingEgressSnapshot, ...],
    ) -> None:
        super().__init__(tracker, snapshots=snapshots)
        self.session_factory = session_factory
        self.operation_id = operation_id
        self.durable_conflict_seen = False
        self.projection_hidden_seen = False

    async def ensure_not_running(self, egress_id: str) -> None:
        self._record("ensure_not_running", egress_id)
        async with self.session_factory() as session:
            operation = await session.get(
                RecordingEgressOperation,
                self.operation_id,
            )
            assert operation is not None
            call = await session.get(Call, operation.call_id)
            assert call is not None
            self.durable_conflict_seen = (
                operation.last_error_code == "recording_identity_conflict"
            )
            self.projection_hidden_seen = (
                call.recording_object_key is None
                and call.recording_egress_id is None
                and call.recording_url is None
            )
        raise SimulatedStopInterruption


class ConflictObservingProvider(FakeProvider):
    def __init__(
        self,
        tracker: TrackingSessionFactory,
        session_factory,
        operation_id: UUID,
        *,
        snapshots: tuple[RecordingEgressSnapshot, ...] = (),
        ensure_failures: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__(
            tracker,
            snapshots=snapshots,
            ensure_failures=ensure_failures,
        )
        self.session_factory = session_factory
        self.operation_id = operation_id
        self.list_projection_observations: list[bool] = []
        self.stop_observations: list[tuple[str, str | None, bool]] = []
        self.stop_recovery_event_observations: list[bool] = []

    async def list_room_egresses(
        self,
        *,
        room_name: str,
    ) -> tuple[RecordingEgressSnapshot, ...]:
        self._record("list_room_egresses", room_name)
        async with self.session_factory() as session:
            operation = await session.get(
                RecordingEgressOperation,
                self.operation_id,
            )
            assert operation is not None
            call = await session.get(Call, operation.call_id)
            assert call is not None
            self.list_projection_observations.append(
                call.recording_object_key is None
                and call.recording_egress_id is None
                and call.recording_url is None
            )
        return self.snapshots

    async def ensure_not_running(self, egress_id: str) -> None:
        async with self.session_factory() as session:
            operation = await session.get(
                RecordingEgressOperation,
                self.operation_id,
            )
            assert operation is not None
            call = await session.get(Call, operation.call_id)
            assert call is not None
            self.stop_observations.append(
                (
                    egress_id,
                    operation.last_error_code,
                    call.recording_object_key is None
                    and call.recording_egress_id is None
                    and call.recording_url is None,
                )
            )
            recovery_event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.idempotency_key
                    == f"recording.reconcile:{self.operation_id}:missing-operation-conflict"
                )
            )
            self.stop_recovery_event_observations.append(
                recovery_event is not None
                and recovery_event.topic == "recording.reconcile"
                and recovery_event.aggregate_type == RECORDING_AGGREGATE_TYPE
                and recovery_event.aggregate_id == self.operation_id
                and recovery_event.payload == {"operation_id": str(self.operation_id)}
                and recovery_event.status == "pending"
            )
        await super().ensure_not_running(egress_id)


class ConcurrentListedSuccessProvider(ConflictObservingProvider):
    def __init__(
        self,
        tracker: TrackingSessionFactory,
        session_factory,
        operation_id: UUID,
        *,
        snapshots: tuple[RecordingEgressSnapshot, ...],
        concurrent_egress_id: str,
        ensure_failures: frozenset[str] = frozenset(),
        add_latest_deletion_intent: bool = False,
    ) -> None:
        super().__init__(
            tracker,
            session_factory,
            operation_id,
            snapshots=snapshots,
            ensure_failures=ensure_failures,
        )
        self.concurrent_egress_id = concurrent_egress_id
        self.add_latest_deletion_intent = add_latest_deletion_intent

    async def list_room_egresses(
        self,
        *,
        room_name: str,
    ) -> tuple[RecordingEgressSnapshot, ...]:
        self._record("list_room_egresses", room_name)
        async with self.session_factory() as session:
            operation = await RecordingLifecycleService(
                session,
                now_provider=lambda: NOW,
            ).record_start_success(
                self.operation_id,
                RecordingEgressResult(
                    egress_id=self.concurrent_egress_id,
                    object_key=OBJECT_KEY,
                    url="https://private.invalid/concurrent",
                ),
            )
            assert operation is not None
            if self.add_latest_deletion_intent:
                requested_at = NOW + timedelta(seconds=1)
                operation.stop_requested_at = requested_at
                operation.delete_requested_at = requested_at
                call = await session.get(Call, operation.call_id)
                assert call is not None
                call.deleted_at = requested_at
                call.recording_object_key = None
                call.recording_egress_id = None
                call.recording_url = None
            await session.commit()
        return self.snapshots


class ConcurrentNotStartedProvider(ConflictObservingProvider):
    async def list_room_egresses(
        self,
        *,
        room_name: str,
    ) -> tuple[RecordingEgressSnapshot, ...]:
        self._record("list_room_egresses", room_name)
        async with self.session_factory() as session:
            operation = await RecordingLifecycleService(
                session,
                now_provider=lambda: NOW,
            ).record_start_error(
                self.operation_id,
                outcome="not_started",
                error_code="validation",
            )
            assert operation is not None
            await session.commit()
        return self.snapshots


class RemovedOperationDuringListingProvider(ConflictObservingProvider):
    def __init__(
        self,
        tracker: TrackingSessionFactory,
        session_factory,
        operation_id: UUID,
        *,
        snapshots: tuple[RecordingEgressSnapshot, ...],
        cleanup_storage: FakeStorage,
        ensure_failures: frozenset[str] = frozenset(),
        owner_delete_requested_at: datetime | None = None,
    ) -> None:
        super().__init__(
            tracker,
            session_factory,
            operation_id,
            snapshots=snapshots,
            ensure_failures=ensure_failures,
        )
        self.cleanup_storage = cleanup_storage
        self.owner_delete_requested_at = owner_delete_requested_at

    async def list_room_egresses(
        self,
        *,
        room_name: str,
    ) -> tuple[RecordingEgressSnapshot, ...]:
        self._record("list_room_egresses", room_name)
        async with self.session_factory() as session:
            transition_at = self.owner_delete_requested_at or NOW
            lifecycle = RecordingLifecycleService(
                session,
                now_provider=lambda: transition_at,
            )
            if self.owner_delete_requested_at is not None:
                discovered = await RecordingEgressOperationRepository(
                    session
                ).get_by_id(self.operation_id)
                assert discovered is not None
                calls = CallRepository(session)
                call = await calls.get_by_id_including_deleted_for_update(
                    discovered.call_id
                )
                assert call is not None
                operation = await RecordingEgressOperationRepository(
                    session
                ).get_by_id_for_update(self.operation_id)
                assert operation is not None
                await calls.purge_customer_content(call)
                call.deleted_at = self.owner_delete_requested_at
                requested = await lifecycle.request_deletion(call)
                assert requested is operation

            changed = await lifecycle.record_start_error(
                self.operation_id,
                outcome="not_started",
                error_code="validation",
            )
            assert changed is not None
            await session.commit()

        cleanup_result = await RecordingReconciler(
            self.tracker,
            FakeProvider(self.tracker),
            self.cleanup_storage,
            now_provider=lambda: transition_at + timedelta(seconds=1),
        ).reconcile(self.operation_id)
        assert cleanup_result == ReconciliationResult("complete")
        async with self.session_factory() as session:
            assert await session.get(
                RecordingEgressOperation,
                self.operation_id,
            ) is None
        return self.snapshots


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

    if case.expected_conflict_category is None:
        assert result == case.expected_result
    else:
        assert result.outcome == case.expected_result.outcome
        assert result.error_code == case.expected_result.error_code
        assert result.conflict_category == case.expected_conflict_category
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
async def test_recording_reconciliation_propagates_storage_defects(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(
        name="storage defect",
        start_state="started",
        provider_egress_id="EG_known",
        stop_requested=True,
        delete_requested=True,
        provider_terminal=True,
    )
    _, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    tracker = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )

    class DefectiveStorage(FakeStorage):
        async def delete_object(self, *, object_key: str) -> None:
            await super().delete_object(object_key=object_key)
            raise RuntimeError("STORAGE_DEFECT_SENTINEL")

    with pytest.raises(RuntimeError, match="STORAGE_DEFECT_SENTINEL"):
        await RecordingReconciler(
            tracker,
            FakeProvider(tracker),
            DefectiveStorage(tracker),
            now_provider=lambda: NOW,
        ).reconcile(operation_id)


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
async def test_already_conflicted_multiple_exact_listing_keeps_specific_signal(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(name="sticky multiple conflict", start_state="uncertain")
    _, operation_id = await _persist_operation(
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
                egress_id="EG_first",
                room_name="room-owned",
                status=1,
                object_key=OBJECT_KEY,
            ),
            RecordingEgressSnapshot(
                egress_id="EG_second",
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

    assert result.outcome == "retry"
    assert result.error_code == "recording_identity_conflict"
    assert result.conflict_category == "multiple_exact_match"
    assert provider.calls == [
        ("list_room_egresses", "room-owned"),
        ("ensure_not_running", "EG_first"),
        ("ensure_not_running", "EG_second"),
    ]


@pytest.mark.anyio
async def test_stale_prepared_recovery_cannot_clear_unknown_identity_conflict(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(name="stale prepared conflict", start_state="prepared")
    call_id, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    operation = await db_session.get(RecordingEgressOperation, operation_id)
    call = await db_session.get(Call, call_id)
    assert operation is not None
    assert call is not None
    operation.last_error_code = "recording_identity_conflict"
    call.recording_object_key = OBJECT_KEY
    call.recording_url = "https://private.invalid/must-hide"
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

    assert result == ReconciliationResult("retry", "recording_identity_conflict")
    assert provider.calls == [("list_room_egresses", "room-owned")]
    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    call = await db_session.get(Call, call_id)
    assert stored is not None
    assert stored.start_state == "uncertain"
    assert stored.last_error_code == "recording_identity_conflict"
    assert call is not None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None


@pytest.mark.anyio
async def test_multiple_match_conflict_is_durable_before_first_stop_and_retry(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(name="interrupted conflict", start_state="uncertain")
    call_id, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    exact = (
        RecordingEgressSnapshot(
            egress_id="EG_first",
            room_name="room-owned",
            status=1,
            object_key=OBJECT_KEY,
        ),
        RecordingEgressSnapshot(
            egress_id="EG_second",
            room_name="room-owned",
            status=1,
            object_key=OBJECT_KEY,
        ),
    )
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)
    interrupted_provider = InterruptingConflictProvider(
        tracker,
        base_factory,
        operation_id,
        snapshots=exact,
    )

    with pytest.raises(SimulatedStopInterruption):
        await RecordingReconciler(
            tracker,
            interrupted_provider,
            FakeStorage(tracker),
            now_provider=lambda: NOW,
        ).reconcile(operation_id)

    assert interrupted_provider.durable_conflict_seen is True
    assert interrupted_provider.projection_hidden_seen is True

    retry_provider = FakeProvider(tracker, snapshots=(exact[1],))
    retry_result = await RecordingReconciler(
        tracker,
        retry_provider,
        FakeStorage(tracker),
        now_provider=lambda: NOW + timedelta(seconds=1),
    ).reconcile(operation_id)

    assert retry_result == ReconciliationResult(
        "retry",
        "recording_identity_conflict",
    )
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
async def test_singleton_projection_conflict_is_persisted_before_exact_stop(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(name="projection conflict", start_state="uncertain")
    call_id, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    call = await db_session.get(Call, call_id)
    assert call is not None
    call.recording_egress_id = "EG_existing_projection"
    await db_session.commit()
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)
    provider = ConflictObservingProvider(
        tracker,
        base_factory,
        operation_id,
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

    assert result == ReconciliationResult("retry", "recording_identity_conflict")
    assert provider.calls == [
        ("list_room_egresses", "room-owned"),
        ("ensure_not_running", "EG_exact"),
    ]
    assert provider.stop_observations == [
        ("EG_exact", "recording_identity_conflict", True)
    ]
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
async def test_known_conflict_survives_empty_and_singleton_listings_and_cleanup_proof(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(
        name="known sticky conflict",
        start_state="started",
        provider_egress_id="EG_known",
        stop_requested=True,
        delete_requested=True,
        provider_terminal=True,
    )
    call_id, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    operation = await db_session.get(RecordingEgressOperation, operation_id)
    call = await db_session.get(Call, call_id)
    assert operation is not None
    assert call is not None
    operation.last_error_code = "recording_identity_conflict"
    call.recording_object_key = OBJECT_KEY
    call.recording_egress_id = "EG_known"
    call.recording_url = "https://private.invalid/must-hide"
    await db_session.commit()
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)
    provider = ConflictObservingProvider(
        tracker,
        base_factory,
        operation_id,
    )
    storage = FakeStorage(tracker)
    reconciler = RecordingReconciler(
        tracker,
        provider,
        storage,
        now_provider=lambda: NOW,
    )

    empty_result = await reconciler.reconcile(operation_id)
    provider.snapshots = (
        RecordingEgressSnapshot(
            egress_id="EG_known",
            room_name="room-owned",
            status=1,
            object_key=OBJECT_KEY,
        ),
    )
    same_identity_result = await reconciler.reconcile(operation_id)
    provider.snapshots = (
        RecordingEgressSnapshot(
            egress_id="EG_later",
            room_name="room-owned",
            status=1,
            object_key=OBJECT_KEY,
        ),
    )
    different_identity_result = await reconciler.reconcile(operation_id)

    assert empty_result == ReconciliationResult(
        "retry",
        "recording_identity_conflict",
    )
    assert same_identity_result == ReconciliationResult(
        "retry",
        "recording_identity_conflict",
    )
    assert different_identity_result == ReconciliationResult(
        "retry",
        "recording_identity_conflict",
        "multiple_exact_match",
    )
    assert provider.calls == [
        ("list_room_egresses", "room-owned"),
        ("ensure_not_running", "EG_known"),
        ("list_room_egresses", "room-owned"),
        ("ensure_not_running", "EG_known"),
        ("list_room_egresses", "room-owned"),
        ("ensure_not_running", "EG_known"),
        ("ensure_not_running", "EG_later"),
    ]
    assert provider.list_projection_observations == [True, True, True]
    assert storage.calls == []
    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    call = await db_session.get(Call, call_id)
    assert stored is not None
    assert stored.start_state == "started"
    assert stored.provider_egress_id == "EG_known"
    assert stored.last_error_code == "recording_identity_conflict"
    assert stored.object_deleted_at is None
    assert call is not None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None


@pytest.mark.anyio
@pytest.mark.parametrize("persistence_status", ["missing", "changed"])
@pytest.mark.parametrize(
    "observation_path",
    ["first_multiple_listing", "already_conflicted_union"],
)
async def test_multiple_identity_observation_survives_failed_persistence_refresh(
    db_session: AsyncSession,
    active_user,
    persistence_status: Literal["missing", "changed"],
    observation_path: str,
) -> None:
    already_conflicted = observation_path == "already_conflicted_union"
    case = MatrixCase(
        name=observation_path,
        start_state="started" if already_conflicted else "uncertain",
        provider_egress_id="EG_durable" if already_conflicted else None,
    )
    _, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    if already_conflicted:
        operation = await db_session.get(RecordingEgressOperation, operation_id)
        assert operation is not None
        operation.last_error_code = "recording_identity_conflict"
        await db_session.commit()

    listed_ids = (
        ("EG_listed",)
        if already_conflicted
        else ("EG_listed_first", "EG_listed_second")
    )
    tracker = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )
    provider = FakeProvider(
        tracker,
        snapshots=tuple(
            RecordingEgressSnapshot(
                egress_id=egress_id,
                room_name="room-owned",
                status=1,
                object_key=OBJECT_KEY,
            )
            for egress_id in listed_ids
        ),
    )

    result = await ForcedConflictPersistenceReconciler(
        tracker,
        provider,
        FakeStorage(tracker),
        now_provider=lambda: NOW,
        persistence_status=persistence_status,
    ).reconcile(operation_id)

    assert result == ReconciliationResult(
        "retry",
        "recording_identity_conflict",
        "multiple_exact_match",
    )
    assert provider.calls == [("list_room_egresses", "room-owned")]


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

    assert result == ReconciliationResult("retry", "recording_provider_unavailable")
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


@pytest.mark.anyio
async def test_singleton_exact_racing_not_started_error_conflicts_before_cleanup(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(
        name="exact not-started race",
        start_state="starting",
        stop_requested=True,
        delete_requested=True,
    )
    _, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert operation is not None
    operation.start_attempted_at = NOW
    await db_session.commit()
    exact = RecordingEgressSnapshot(
        egress_id="EG_exact",
        room_name="room-owned",
        status=1,
        object_key=OBJECT_KEY,
    )
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)
    provider = ConcurrentNotStartedProvider(
        tracker,
        base_factory,
        operation_id,
        snapshots=(exact,),
    )
    storage = FakeStorage(tracker)

    result = await RecordingReconciler(
        tracker,
        provider,
        storage,
        now_provider=lambda: NOW,
    ).reconcile(operation_id)

    assert result == ReconciliationResult("retry", "recording_identity_conflict")
    assert provider.calls == [
        ("list_room_egresses", "room-owned"),
        ("ensure_not_running", "EG_exact"),
    ]
    assert provider.stop_observations == [
        ("EG_exact", "recording_identity_conflict", True)
    ]
    assert storage.calls == []
    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored is not None
    assert stored.start_state == "uncertain"
    assert stored.provider_egress_id is None
    assert stored.last_error_code == "recording_identity_conflict"
    assert stored.object_deleted_at is None


@pytest.mark.anyio
async def test_exact_evidence_restores_authority_after_reclaimed_cleanup(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(
        name="removed during exact listing",
        start_state="starting",
        stop_requested=True,
        delete_requested=True,
    )
    call_id, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert operation is not None
    operation.start_attempted_at = NOW
    await db_session.commit()

    exact = RecordingEgressSnapshot(
        egress_id="EG_exact",
        room_name="room-owned",
        status=1,
        object_key=OBJECT_KEY,
    )
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)
    cleanup_storage = FakeStorage(tracker)
    outer_storage = FakeStorage(tracker)
    provider = RemovedOperationDuringListingProvider(
        tracker,
        base_factory,
        operation_id,
        snapshots=(exact,),
        cleanup_storage=cleanup_storage,
    )

    result = await RecordingReconciler(
        tracker,
        provider,
        outer_storage,
        now_provider=lambda: NOW + timedelta(seconds=2),
    ).reconcile(operation_id)

    assert result == ReconciliationResult(
        "retry",
        "recording_identity_conflict",
    )
    assert provider.calls == [
        ("list_room_egresses", "room-owned"),
        ("ensure_not_running", "EG_exact"),
    ]
    assert cleanup_storage.calls == [("delete_object", OBJECT_KEY)]
    assert outer_storage.calls == []
    assert provider.stop_observations == [
        ("EG_exact", "recording_identity_conflict", True)
    ]
    assert provider.stop_recovery_event_observations == [True]

    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored is not None
    assert stored.start_state == "started"
    assert stored.provider_egress_id == "EG_exact"
    assert stored.stop_requested_at is not None
    assert stored.delete_requested_at is not None
    assert stored.provider_terminal_at is None
    assert stored.object_deleted_at is None
    assert stored.last_error_code == "recording_identity_conflict"
    call = await db_session.get(Call, call_id)
    assert call is not None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None

    event = await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.idempotency_key
            == f"recording.reconcile:{operation_id}:missing-operation-conflict"
        )
    )
    assert event is not None
    assert event.topic == "recording.reconcile"
    assert event.aggregate_type == RECORDING_AGGREGATE_TYPE
    assert event.aggregate_id == operation_id
    assert event.payload == {"operation_id": str(operation_id)}
    assert event.status == "pending"
    assert event.next_attempt_at.replace(tzinfo=UTC) == NOW + timedelta(seconds=2)


@pytest.mark.anyio
async def test_restored_authority_recovers_later_owner_deletion_intent(
    db_session: AsyncSession,
    active_user,
) -> None:
    earlier_stop_requested_at = NOW - timedelta(minutes=1)
    later_delete_requested_at = NOW + timedelta(minutes=1)
    case = MatrixCase(
        name="owner deletes during exact listing",
        start_state="starting",
        stop_requested=True,
    )
    call_id, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert operation is not None
    operation.start_attempted_at = NOW
    operation.stop_requested_at = earlier_stop_requested_at
    assert operation.delete_requested_at is None
    await db_session.commit()

    exact = RecordingEgressSnapshot(
        egress_id="EG_exact",
        room_name="room-owned",
        status=1,
        object_key=OBJECT_KEY,
    )
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)
    cleanup_storage = FakeStorage(tracker)
    outer_storage = FakeStorage(tracker)
    provider = RemovedOperationDuringListingProvider(
        tracker,
        base_factory,
        operation_id,
        snapshots=(exact,),
        cleanup_storage=cleanup_storage,
        owner_delete_requested_at=later_delete_requested_at,
    )

    result = await RecordingReconciler(
        tracker,
        provider,
        outer_storage,
        now_provider=lambda: later_delete_requested_at + timedelta(seconds=2),
    ).reconcile(operation_id)

    assert result == ReconciliationResult(
        "retry",
        "recording_identity_conflict",
    )
    assert provider.calls == [
        ("list_room_egresses", "room-owned"),
        ("ensure_not_running", "EG_exact"),
    ]
    assert cleanup_storage.calls == [("delete_object", OBJECT_KEY)]
    assert outer_storage.calls == []

    db_session.expire_all()
    restored = await db_session.get(RecordingEgressOperation, operation_id)
    assert restored is not None
    assert restored.stop_requested_at is not None
    assert restored.stop_requested_at.replace(tzinfo=UTC) == earlier_stop_requested_at
    assert restored.delete_requested_at is not None
    assert (
        restored.delete_requested_at.replace(tzinfo=UTC)
        == later_delete_requested_at
    )
    assert restored.last_error_code == "recording_identity_conflict"
    call = await db_session.get(Call, call_id)
    assert call is not None
    assert call.deleted_at is not None
    assert call.deleted_at.replace(tzinfo=UTC) == later_delete_requested_at
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None
    assert provider.stop_recovery_event_observations == [True]


@pytest.mark.anyio
async def test_missing_conflict_merge_recovers_tombstone_delete_intent(
    db_session: AsyncSession,
    active_user,
) -> None:
    current_stop_requested_at = NOW - timedelta(minutes=2)
    snapshot_stop_requested_at = NOW - timedelta(minutes=1)
    tombstone_at = NOW + timedelta(minutes=1)
    case = MatrixCase(
        name="concurrent restore before missing conflict merge",
        start_state="starting",
        stop_requested=True,
    )
    call_id, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert operation is not None
    operation.start_attempted_at = NOW
    operation.stop_requested_at = snapshot_stop_requested_at
    await db_session.commit()

    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)
    provider = FakeProvider(tracker)
    storage = FakeStorage(tracker)
    reconciler = RecordingReconciler(
        tracker,
        provider,
        storage,
        now_provider=lambda: tombstone_at + timedelta(seconds=1),
    )
    stale_snapshot = await reconciler._load_and_recover_stale_start(operation_id)
    assert stale_snapshot is not None
    assert stale_snapshot.stop_requested_at is not None
    assert (
        stale_snapshot.stop_requested_at.replace(tzinfo=UTC)
        == snapshot_stop_requested_at
    )
    assert stale_snapshot.delete_requested_at is None

    db_session.expire_all()
    concurrently_restored = await db_session.get(
        RecordingEgressOperation,
        operation_id,
    )
    call = await db_session.get(Call, call_id)
    assert concurrently_restored is not None
    assert call is not None
    concurrently_restored.start_state = "started"
    concurrently_restored.provider_egress_id = "EG_concurrent"
    concurrently_restored.stop_requested_at = current_stop_requested_at
    concurrently_restored.delete_requested_at = None
    call.deleted_at = tombstone_at
    call.recording_object_key = OBJECT_KEY
    call.recording_egress_id = "EG_concurrent"
    call.recording_url = "https://private.invalid/must-hide"
    await db_session.commit()

    async with base_factory() as session:
        status, refreshed = await reconciler._restore_or_merge_missing_conflict(
            session,
            stale_snapshot,
            "EG_observed",
        )

    assert status == "conflict"
    assert refreshed is not None
    assert provider.calls == []
    assert storage.calls == []
    db_session.expire_all()
    merged = await db_session.get(RecordingEgressOperation, operation_id)
    call = await db_session.get(Call, call_id)
    assert merged is not None
    assert merged.start_state == "started"
    assert merged.provider_egress_id == "EG_concurrent"
    assert merged.stop_requested_at is not None
    assert (
        merged.stop_requested_at.replace(tzinfo=UTC)
        == current_stop_requested_at
    )
    assert merged.delete_requested_at is not None
    assert merged.delete_requested_at.replace(tzinfo=UTC) == tombstone_at
    assert merged.last_error_code == "recording_identity_conflict"
    assert call is not None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None


@pytest.mark.anyio
async def test_multiple_exact_evidence_restores_uncertain_authority_after_cleanup(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(
        name="removed during multiple exact listing",
        start_state="starting",
        stop_requested=True,
        delete_requested=True,
    )
    call_id, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert operation is not None
    operation.start_attempted_at = NOW
    await db_session.commit()

    exact_a = RecordingEgressSnapshot(
        egress_id="EG_A",
        room_name="room-owned",
        status=1,
        object_key=OBJECT_KEY,
    )
    exact_a_duplicate = RecordingEgressSnapshot(
        egress_id="EG_A",
        room_name="room-owned",
        status=2,
        object_key=OBJECT_KEY,
    )
    exact_b = RecordingEgressSnapshot(
        egress_id="EG_B",
        room_name="room-owned",
        status=1,
        object_key=OBJECT_KEY,
    )
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)
    cleanup_storage = FakeStorage(tracker)
    outer_storage = FakeStorage(tracker)
    provider = RemovedOperationDuringListingProvider(
        tracker,
        base_factory,
        operation_id,
        snapshots=(exact_a, exact_a_duplicate, exact_b),
        cleanup_storage=cleanup_storage,
        ensure_failures=frozenset({"EG_A"}),
    )

    result = await RecordingReconciler(
        tracker,
        provider,
        outer_storage,
        now_provider=lambda: NOW + timedelta(seconds=2),
    ).reconcile(operation_id)

    assert result == ReconciliationResult(
        "retry",
        "recording_identity_conflict",
        "multiple_exact_match",
    )
    assert provider.calls == [
        ("list_room_egresses", "room-owned"),
        ("ensure_not_running", "EG_A"),
        ("ensure_not_running", "EG_B"),
    ]
    assert cleanup_storage.calls == [("delete_object", OBJECT_KEY)]
    assert outer_storage.calls == []
    assert provider.stop_observations == [
        ("EG_A", "recording_identity_conflict", True),
        ("EG_B", "recording_identity_conflict", True),
    ]
    assert provider.stop_recovery_event_observations == [True, True]

    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored is not None
    assert stored.start_state == "uncertain"
    assert stored.provider_egress_id is None
    assert stored.stop_requested_at is not None
    assert stored.delete_requested_at is not None
    assert stored.provider_terminal_at is None
    assert stored.object_deleted_at is None
    assert stored.last_error_code == "recording_identity_conflict"
    call = await db_session.get(Call, call_id)
    assert call is not None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None

    event = await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.idempotency_key
            == f"recording.reconcile:{operation_id}:missing-operation-conflict"
        )
    )
    assert event is not None
    assert event.topic == "recording.reconcile"
    assert event.aggregate_type == RECORDING_AGGREGATE_TYPE
    assert event.aggregate_id == operation_id
    assert event.payload == {"operation_id": str(operation_id)}
    assert event.status == "pending"
    assert event.next_attempt_at.replace(tzinfo=UTC) == NOW + timedelta(seconds=2)


@pytest.mark.anyio
async def test_restored_authority_survives_stop_failure_and_later_retry(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(
        name="removed operation stop failure",
        start_state="starting",
        stop_requested=True,
        delete_requested=True,
    )
    _, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert operation is not None
    operation.start_attempted_at = NOW
    await db_session.commit()
    exact = RecordingEgressSnapshot(
        egress_id="EG_exact",
        room_name="room-owned",
        status=1,
        object_key=OBJECT_KEY,
    )
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)
    cleanup_storage = FakeStorage(tracker)
    outer_storage = FakeStorage(tracker)
    first_provider = RemovedOperationDuringListingProvider(
        tracker,
        base_factory,
        operation_id,
        snapshots=(exact,),
        cleanup_storage=cleanup_storage,
        ensure_failures=frozenset({"EG_exact"}),
    )

    first = await RecordingReconciler(
        tracker,
        first_provider,
        outer_storage,
        now_provider=lambda: NOW + timedelta(seconds=2),
    ).reconcile(operation_id)

    assert first == ReconciliationResult(
        "retry",
        "recording_identity_conflict",
    )
    assert first_provider.calls == [
        ("list_room_egresses", "room-owned"),
        ("ensure_not_running", "EG_exact"),
    ]
    assert first_provider.stop_recovery_event_observations == [True]
    assert cleanup_storage.calls == [("delete_object", OBJECT_KEY)]
    assert outer_storage.calls == []
    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored is not None
    assert stored.last_error_code == "recording_identity_conflict"
    event = await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.idempotency_key
            == f"recording.reconcile:{operation_id}:missing-operation-conflict"
        )
    )
    assert event is not None
    assert event.status == "pending"

    second_provider = FakeProvider(tracker, snapshots=(exact,))
    second = await RecordingReconciler(
        tracker,
        second_provider,
        outer_storage,
        now_provider=lambda: NOW + timedelta(seconds=3),
    ).reconcile(operation_id)

    assert second == ReconciliationResult(
        "retry",
        "recording_identity_conflict",
    )
    assert second_provider.calls == [
        ("list_room_egresses", "room-owned"),
        ("ensure_not_running", "EG_exact"),
    ]
    assert outer_storage.calls == []
    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored is not None
    assert stored.last_error_code == "recording_identity_conflict"
    assert stored.object_deleted_at is None


@pytest.mark.anyio
async def test_two_stale_restorers_merge_one_operation_and_recovery_event(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(
        name="two removed operation restorers",
        start_state="starting",
        stop_requested=True,
        delete_requested=True,
    )
    call_id, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert operation is not None
    operation.start_attempted_at = NOW
    await db_session.commit()
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)
    first_provider = ConflictObservingProvider(
        tracker,
        base_factory,
        operation_id,
    )
    second_provider = ConflictObservingProvider(
        tracker,
        base_factory,
        operation_id,
    )
    storage = FakeStorage(tracker)
    first_reconciler = RecordingReconciler(
        tracker,
        first_provider,
        storage,
        now_provider=lambda: NOW + timedelta(seconds=2),
    )
    second_reconciler = RecordingReconciler(
        tracker,
        second_provider,
        storage,
        now_provider=lambda: NOW + timedelta(seconds=3),
    )
    stale_snapshot = await first_reconciler._load_and_recover_stale_start(
        operation_id
    )
    assert stale_snapshot is not None
    async with base_factory() as session:
        changed = await RecordingLifecycleService(
            session,
            now_provider=lambda: NOW,
        ).record_start_error(
            operation_id,
            outcome="not_started",
            error_code="validation",
        )
        assert changed is not None
        await session.commit()
    cleanup = await RecordingReconciler(
        tracker,
        FakeProvider(tracker),
        storage,
        now_provider=lambda: NOW + timedelta(seconds=1),
    ).reconcile(operation_id)
    assert cleanup == ReconciliationResult("complete")
    exact_a = RecordingEgressSnapshot(
        egress_id="EG_A",
        room_name="room-owned",
        status=1,
        object_key=OBJECT_KEY,
    )
    exact_b = RecordingEgressSnapshot(
        egress_id="EG_B",
        room_name="room-owned",
        status=1,
        object_key=OBJECT_KEY,
    )

    first_status, first_snapshot = await first_reconciler._attach_exact_identity(
        stale_snapshot,
        exact_a,
    )
    second_status, second_snapshot = (
        await second_reconciler._attach_exact_identity(stale_snapshot, exact_b)
    )

    assert (first_status, second_status) == ("conflict", "conflict")
    assert first_snapshot is not None
    assert second_snapshot is not None
    await first_reconciler._stop_conflicting_identities(
        first_snapshot,
        (exact_a.egress_id,),
    )
    await second_reconciler._stop_conflicting_identities(
        second_snapshot,
        (exact_b.egress_id,),
    )

    db_session.expire_all()
    operations = list(
        await db_session.scalars(
            select(RecordingEgressOperation).where(
                RecordingEgressOperation.call_id == call_id
            )
        )
    )
    events = list(
        await db_session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.idempotency_key
                == f"recording.reconcile:{operation_id}:missing-operation-conflict"
            )
        )
    )
    assert len(operations) == 1
    assert len(events) == 1
    assert operations[0].start_state == "started"
    assert operations[0].provider_egress_id == "EG_A"
    assert operations[0].last_error_code == "recording_identity_conflict"
    assert operations[0].object_deleted_at is None
    assert events[0].status == "pending"
    assert first_provider.calls == [("ensure_not_running", "EG_A")]
    assert first_provider.stop_recovery_event_observations == [True]
    assert second_provider.calls == [
        ("ensure_not_running", "EG_A"),
        ("ensure_not_running", "EG_B"),
    ]
    assert second_provider.stop_recovery_event_observations == [True, True]
    assert storage.calls == [("delete_object", OBJECT_KEY)]


@pytest.mark.anyio
async def test_singleton_listing_racing_different_direct_success_conflicts_and_stops_union(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(name="different direct identity", start_state="uncertain")
    call_id, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    exact = RecordingEgressSnapshot(
        egress_id="EG_A",
        room_name="room-owned",
        status=1,
        object_key=OBJECT_KEY,
    )
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)
    provider = ConcurrentListedSuccessProvider(
        tracker,
        base_factory,
        operation_id,
        snapshots=(exact,),
        concurrent_egress_id="EG_B",
    )

    result = await RecordingReconciler(
        tracker,
        provider,
        FakeStorage(tracker),
        now_provider=lambda: NOW,
    ).reconcile(operation_id)

    assert result == ReconciliationResult(
        "retry",
        "recording_identity_conflict",
        "multiple_exact_match",
    )
    assert provider.calls == [
        ("list_room_egresses", "room-owned"),
        ("ensure_not_running", "EG_B"),
        ("ensure_not_running", "EG_A"),
    ]
    assert provider.stop_observations == [
        ("EG_B", "recording_identity_conflict", True),
        ("EG_A", "recording_identity_conflict", True),
    ]
    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    call = await db_session.get(Call, call_id)
    assert stored is not None
    assert stored.start_state == "started"
    assert stored.provider_egress_id == "EG_B"
    assert stored.last_error_code == "recording_identity_conflict"
    assert call is not None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None


@pytest.mark.anyio
async def test_multiple_listing_racing_direct_success_never_deletes_on_partial_stop(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(
        name="multiple direct identity deletion race",
        start_state="uncertain",
        stop_requested=True,
        delete_requested=True,
    )
    _, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    snapshots = (
        RecordingEgressSnapshot(
            egress_id="EG_A",
            room_name="room-owned",
            status=1,
            object_key=OBJECT_KEY,
        ),
        RecordingEgressSnapshot(
            egress_id="EG_A",
            room_name="room-owned",
            status=2,
            object_key=OBJECT_KEY,
        ),
        RecordingEgressSnapshot(
            egress_id="EG_C",
            room_name="room-owned",
            status=1,
            object_key=OBJECT_KEY,
        ),
    )
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)
    provider = ConcurrentListedSuccessProvider(
        tracker,
        base_factory,
        operation_id,
        snapshots=snapshots,
        concurrent_egress_id="EG_B",
        ensure_failures=frozenset({"EG_A"}),
    )
    storage = FakeStorage(tracker)

    result = await RecordingReconciler(
        tracker,
        provider,
        storage,
        now_provider=lambda: NOW,
    ).reconcile(operation_id)

    assert result == ReconciliationResult(
        "retry",
        "recording_identity_conflict",
        "multiple_exact_match",
    )
    assert provider.calls == [
        ("list_room_egresses", "room-owned"),
        ("ensure_not_running", "EG_B"),
        ("ensure_not_running", "EG_A"),
        ("ensure_not_running", "EG_C"),
    ]
    assert storage.calls == []
    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored is not None
    assert stored.start_state == "started"
    assert stored.provider_egress_id == "EG_B"
    assert stored.last_error_code == "recording_identity_conflict"
    assert stored.object_deleted_at is None


@pytest.mark.anyio
async def test_singleton_listing_racing_same_direct_success_honors_latest_deletion(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(name="same direct identity", start_state="uncertain")
    call_id, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    exact = RecordingEgressSnapshot(
        egress_id="EG_same",
        room_name="room-owned",
        status=1,
        object_key=OBJECT_KEY,
    )
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)
    provider = ConcurrentListedSuccessProvider(
        tracker,
        base_factory,
        operation_id,
        snapshots=(exact,),
        concurrent_egress_id="EG_same",
        add_latest_deletion_intent=True,
    )
    storage = FakeStorage(tracker)

    result = await RecordingReconciler(
        tracker,
        provider,
        storage,
        now_provider=lambda: NOW + timedelta(seconds=2),
    ).reconcile(operation_id)

    assert result == ReconciliationResult("complete")
    assert provider.calls == [
        ("list_room_egresses", "room-owned"),
        ("ensure_not_running", "EG_same"),
    ]
    assert all(
        error_code != "recording_identity_conflict"
        for _, error_code, _ in provider.stop_observations
    )
    assert storage.calls == [("delete_object", OBJECT_KEY)]
    db_session.expire_all()
    assert await db_session.get(RecordingEgressOperation, operation_id) is None
    call = await db_session.get(Call, call_id)
    assert call is not None
    assert call.deleted_at is not None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None


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
            call = await CallRepository(session).get_by_id_including_deleted_for_update(
                discovered.call_id
            )
            assert call is not None
            operation = await operations.get_by_id_for_update(self.operation_id)
            assert operation is not None
            operation.provider_egress_id = "EG_concurrent"
            await session.commit()


class ConcurrentConflictStorage(FakeStorage):
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
            call = await CallRepository(session).get_by_id_including_deleted_for_update(
                discovered.call_id
            )
            assert call is not None
            operation = await operations.get_by_id_for_update(self.operation_id)
            assert operation is not None
            operation.last_error_code = "recording_identity_conflict"
            call.recording_object_key = OBJECT_KEY
            call.recording_egress_id = operation.provider_egress_id
            call.recording_url = "https://private.invalid/racing-conflict"
            await session.commit()


class ConflictInjectingSessionFactory:
    def __init__(
        self,
        base_factory,
        *,
        operation_id: UUID,
        inject_on_entry: int,
    ) -> None:
        self.base_factory = base_factory
        self.operation_id = operation_id
        self.inject_on_entry = inject_on_entry
        self.entries = 0

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[AsyncSession]:
        self.entries += 1
        if self.entries == self.inject_on_entry:
            async with self.base_factory() as racing_session:
                operations = RecordingEgressOperationRepository(racing_session)
                discovered = await operations.get_by_id(self.operation_id)
                assert discovered is not None
                call = await CallRepository(
                    racing_session
                ).get_by_id_including_deleted_for_update(discovered.call_id)
                assert call is not None
                operation = await operations.get_by_id_for_update(self.operation_id)
                assert operation is not None
                operation.last_error_code = "recording_identity_conflict"
                call.recording_object_key = OBJECT_KEY
                call.recording_egress_id = operation.provider_egress_id
                call.recording_url = "https://private.invalid/racing-conflict"
                await racing_session.commit()
        async with self.base_factory() as session:
            yield session


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


@pytest.mark.anyio
async def test_conflict_racing_storage_blocks_object_proof_and_operation_removal(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(
        name="storage conflict race",
        start_state="started",
        provider_egress_id="EG_known",
        stop_requested=True,
        delete_requested=True,
        provider_terminal=True,
    )
    call_id, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)

    result = await RecordingReconciler(
        tracker,
        FakeProvider(tracker),
        ConcurrentConflictStorage(tracker, base_factory, operation_id),
        now_provider=lambda: NOW,
    ).reconcile(operation_id)

    assert result == ReconciliationResult("retry", "recording_identity_conflict")
    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    call = await db_session.get(Call, call_id)
    assert stored is not None
    assert stored.last_error_code == "recording_identity_conflict"
    assert stored.object_deleted_at is None
    assert call is not None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None


@pytest.mark.anyio
async def test_conflict_racing_operation_removal_preserves_existing_object_proof(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(
        name="operation removal conflict race",
        start_state="not_started",
        stop_requested=True,
        delete_requested=True,
    )
    call_id, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert operation is not None
    operation.object_deleted_at = NOW
    await db_session.commit()
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    injecting_factory = ConflictInjectingSessionFactory(
        base_factory,
        operation_id=operation_id,
        inject_on_entry=2,
    )
    tracker = TrackingSessionFactory(base_factory)

    result = await RecordingReconciler(
        injecting_factory,
        FakeProvider(tracker),
        FakeStorage(tracker),
        now_provider=lambda: NOW,
    ).reconcile(operation_id)

    assert result == ReconciliationResult("retry", "recording_identity_conflict")
    assert tracker.open_contexts == 0
    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    call = await db_session.get(Call, call_id)
    assert stored is not None
    assert stored.last_error_code == "recording_identity_conflict"
    assert stored.object_deleted_at is not None
    assert call is not None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("result", "expected_metric"),
    [
        (ReconciliationResult("complete"), "complete"),
        (
            ReconciliationResult("retry", "recording_unresolved"),
            "recording_unresolved",
        ),
        (
            ReconciliationResult("retry", "recording_provider_unavailable"),
            "recording_provider_unavailable",
        ),
        (
            ReconciliationResult("retry", "recording_storage_unavailable"),
            "recording_storage_unavailable",
        ),
        (
            ReconciliationResult("retry", "recording_identity_mismatch"),
            "recording_identity_mismatch",
        ),
        (
            ReconciliationResult("retry", "recording_identity_conflict"),
            "recording_identity_conflict",
        ),
        (
            ReconciliationResult("retry", "recording_legacy_incomplete"),
            "recording_legacy_incomplete",
        ),
    ],
)
async def test_recording_handler_emits_one_bounded_result_per_valid_invocation(
    result: ReconciliationResult,
    expected_metric: str,
) -> None:
    operation_id = uuid4()
    reconciler = _HandlerReconciler(result)
    observability = _RecordingObservability()

    if result.outcome == "complete":
        await deliver_recording_reconcile(
            {
                "recording_reconciler": reconciler,
                "observability": observability,
            },
            _recording_reconcile_event(operation_id),
        )
    else:
        with pytest.raises(OutboxDeliveryError) as exc_info:
            await deliver_recording_reconcile(
                {
                    "recording_reconciler": reconciler,
                    "observability": observability,
                },
                _recording_reconcile_event(operation_id),
            )
        assert exc_info.value.error_code == expected_metric
        assert exc_info.value.retryable is True
        assert exc_info.value.exhaustible is False

    assert reconciler.calls == [operation_id]
    assert observability.results == [expected_metric]
    assert observability.multiple_exact_count == 0


@pytest.mark.anyio
async def test_recording_handler_does_not_emit_for_invalid_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_id = uuid4()
    reconciler = _HandlerReconciler(ReconciliationResult("complete"))
    observability = _RecordingObservability()
    event = _recording_reconcile_event(operation_id)
    event.payload = {"operation_id": str(uuid4())}
    observability_gets = 0

    def get_observability() -> _RecordingObservability:
        nonlocal observability_gets
        observability_gets += 1
        return observability

    monkeypatch.setattr(outbox_topics, "get_observability", get_observability)

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_recording_reconcile(
            {"recording_reconciler": reconciler},
            event,
        )

    assert exc_info.value.error_code == "invalid_payload"
    assert reconciler.calls == []
    assert observability_gets == 0
    assert observability.results == []
    assert observability.multiple_exact_count == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "result",
    [
        RuntimeError("PRIVATE_RECONCILER_FAILURE"),
        SimpleNamespace(outcome="unexpected", error_code=None),
        SimpleNamespace(
            outcome="retry",
            error_code="recording_identity_conflict",
            conflict_category="PRIVATE_CONFLICT_CATEGORY",
        ),
        SimpleNamespace(
            outcome="complete",
            error_code=None,
            conflict_category="multiple_exact_match",
        ),
        SimpleNamespace(
            outcome="retry",
            error_code="recording_unresolved",
            conflict_category="multiple_exact_match",
        ),
    ],
)
async def test_recording_handler_maps_failure_or_invalid_shape_to_one_unresolved_result(
    result: object | Exception,
) -> None:
    operation_id = uuid4()
    observability = _RecordingObservability()

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_recording_reconcile(
            {
                "recording_reconciler": _HandlerReconciler(result),
                "observability": observability,
            },
            _recording_reconcile_event(operation_id),
        )

    assert exc_info.value.error_code == "recording_unresolved"
    assert exc_info.value.retryable is True
    assert exc_info.value.exhaustible is False
    assert observability.results == ["recording_unresolved"]
    assert observability.multiple_exact_count == 0


@pytest.mark.anyio
async def test_recording_handler_counts_multiple_exact_before_conflict_retry() -> None:
    operation_id = uuid4()
    observability = _RecordingObservability()

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_recording_reconcile(
            {
                "recording_reconciler": _HandlerReconciler(
                    SimpleNamespace(
                        outcome="retry",
                        error_code="recording_identity_conflict",
                        conflict_category="multiple_exact_match",
                    )
                ),
                "observability": observability,
            },
            _recording_reconcile_event(operation_id),
        )

    assert exc_info.value.error_code == "recording_identity_conflict"
    assert observability.events == [
        "result:recording_identity_conflict",
        "multiple_exact_match",
    ]
    assert observability.multiple_exact_count == 1


@pytest.mark.anyio
async def test_recording_handler_uses_default_observability_for_valid_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_id = uuid4()
    observability = _RecordingObservability()
    monkeypatch.setattr(
        outbox_topics,
        "get_observability",
        lambda: observability,
        raising=False,
    )

    await deliver_recording_reconcile(
        {"recording_reconciler": _HandlerReconciler(ReconciliationResult("complete"))},
        _recording_reconcile_event(operation_id),
    )

    assert observability.results == ["complete"]
