from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import AsyncContextManager, Literal, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call
from app.models.recording_egress_operation import RecordingEgressOperation
from app.providers.livekit_recording.base import RecordingEgressSnapshot
from app.repositories.call_repository import CallRepository
from app.repositories.recording_egress_operation_repository import (
    RecordingEgressOperationRepository,
)
from app.services.recording_lifecycle_service import (
    RECORDING_AGGREGATE_TYPE,
    RECORDING_IDENTITY_CONFLICT_CODE,
    START_RESULT_LEASE,
)
from app.services.outbox_service import OutboxService


RecordingReconciliationErrorCode = Literal[
    "recording_unresolved",
    "recording_provider_unavailable",
    "recording_storage_unavailable",
    "recording_identity_mismatch",
    "recording_identity_conflict",
    "recording_legacy_incomplete",
]
RECORDING_RECONCILIATION_ERROR_CODES = frozenset(
    {
        "recording_unresolved",
        "recording_provider_unavailable",
        "recording_storage_unavailable",
        "recording_identity_mismatch",
        "recording_identity_conflict",
        "recording_legacy_incomplete",
    }
)


@dataclass(frozen=True)
class ReconciliationResult:
    outcome: Literal["complete", "retry"]
    error_code: RecordingReconciliationErrorCode | None = None


@dataclass(frozen=True)
class _OperationSnapshot:
    operation_id: UUID
    call_id: UUID
    room_name: str | None
    legacy_incomplete: bool
    expected_object_key: str
    provider_egress_id: str | None
    start_state: str
    stop_requested_at: datetime | None
    delete_requested_at: datetime | None
    provider_terminal_at: datetime | None
    object_deleted_at: datetime | None
    last_error_code: str | None


class _RecordingProvider(Protocol):
    async def list_room_egresses(
        self,
        *,
        room_name: str,
    ) -> tuple[RecordingEgressSnapshot, ...]: ...

    async def ensure_not_running(self, egress_id: str) -> None: ...


class _StorageProvider(Protocol):
    async def delete_object(self, *, object_key: str) -> None: ...


class _ConcurrentStateChange(RuntimeError):
    pass


_PersistenceStatus = Literal["updated", "missing", "changed", "conflict"]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _same_instant(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    return _as_utc(left) == _as_utc(right)


class RecordingReconciler:
    """Reconcile one private recording operation across provider-free SQL seams."""

    def __init__(
        self,
        session_factory: Callable[[], AsyncContextManager[AsyncSession]],
        provider: _RecordingProvider,
        storage: _StorageProvider,
        *,
        now_provider: Callable[[], datetime],
    ) -> None:
        self.session_factory = session_factory
        self.provider = provider
        self.storage = storage
        self.now = now_provider

    async def reconcile(self, operation_id: UUID) -> ReconciliationResult:
        try:
            snapshot = await self._load_and_recover_stale_start(operation_id)
        except _ConcurrentStateChange:
            return ReconciliationResult("retry", "recording_unresolved")
        if snapshot is None:
            return ReconciliationResult("complete")
        if snapshot.last_error_code == RECORDING_IDENTITY_CONFLICT_CODE:
            return await self._reconcile_conflict(snapshot)
        if snapshot.provider_egress_id is not None:
            return await self._reconcile_known(snapshot)
        if snapshot.start_state == "not_started":
            return await self._finish_definite_non_start(snapshot)
        return await self._reconcile_unknown(snapshot)

    async def _load_and_recover_stale_start(
        self,
        operation_id: UUID,
    ) -> _OperationSnapshot | None:
        now = _as_utc(self.now())
        stale_before = now - START_RESULT_LEASE
        async with self.session_factory() as session:
            locked = await self._lock_call_then_operation(session, operation_id)
            if locked is None:
                await session.commit()
                return None
            call, operation = locked

            if operation.last_error_code == RECORDING_IDENTITY_CONFLICT_CODE:
                if operation.provider_egress_id is None:
                    operation.start_state = "uncertain"
                self._hide_playback_projection(call)
            else:
                if (
                    operation.start_state == "prepared"
                    and _as_utc(operation.created_at) <= stale_before
                ):
                    operation.start_state = "not_started"
                    operation.last_error_code = None
                elif operation.start_state == "starting" and (
                    operation.start_attempted_at is None
                    or _as_utc(operation.start_attempted_at) <= stale_before
                ):
                    operation.start_state = "uncertain"

                if self._is_definitively_complete_without_io(operation):
                    operation.last_error_code = None
            operation.last_reconciled_at = now
            await session.flush()
            snapshot = self._snapshot(call, operation)
            await session.commit()
            return snapshot

    @staticmethod
    def _is_definitively_complete_without_io(
        operation: RecordingEgressOperation,
    ) -> bool:
        if operation.delete_requested_at is not None:
            return False
        if operation.start_state == "not_started":
            return True
        if operation.provider_egress_id is None:
            return False
        return (
            operation.stop_requested_at is None
            or operation.provider_terminal_at is not None
        )

    async def _reconcile_known(
        self,
        snapshot: _OperationSnapshot,
    ) -> ReconciliationResult:
        if snapshot.stop_requested_at is None:
            return ReconciliationResult("complete")

        if snapshot.provider_terminal_at is None:
            try:
                await self.provider.ensure_not_running(
                    snapshot.provider_egress_id  # type: ignore[arg-type]
                )
            except Exception:
                return await self._record_retry(
                    snapshot,
                    "recording_provider_unavailable",
                )
            status, refreshed = await self._persist_provider_terminal(snapshot)
            if status == "missing":
                return ReconciliationResult("complete")
            if status == "conflict":
                return ReconciliationResult(
                    "retry",
                    RECORDING_IDENTITY_CONFLICT_CODE,
                )
            if status != "updated" or refreshed is None:
                return ReconciliationResult("retry", "recording_unresolved")
            snapshot = refreshed

        if snapshot.delete_requested_at is None:
            return ReconciliationResult("complete")
        return await self._delete_object_and_operation(snapshot)

    async def _finish_definite_non_start(
        self,
        snapshot: _OperationSnapshot,
    ) -> ReconciliationResult:
        if snapshot.delete_requested_at is None:
            return ReconciliationResult("complete")
        return await self._delete_object_and_operation(snapshot)

    async def _reconcile_unknown(
        self,
        snapshot: _OperationSnapshot,
    ) -> ReconciliationResult:
        if snapshot.legacy_incomplete or not snapshot.room_name:
            return await self._record_retry(
                snapshot,
                "recording_legacy_incomplete",
            )
        if snapshot.start_state == "prepared":
            return await self._record_retry(snapshot, "recording_unresolved")

        try:
            listed = await self.provider.list_room_egresses(
                room_name=snapshot.room_name
            )
        except Exception:
            return await self._record_retry(
                snapshot,
                "recording_provider_unavailable",
            )

        exact_by_id: dict[str, RecordingEgressSnapshot] = {}
        for item in listed:
            if (
                item.room_name == snapshot.room_name
                and item.object_key == snapshot.expected_object_key
            ):
                exact_by_id.setdefault(item.egress_id, item)
        exact = tuple(exact_by_id.values())

        if len(exact) > 1:
            status, refreshed = await self._persist_identity_conflict(snapshot)
            if status == "missing":
                return ReconciliationResult("complete")
            if status not in {"updated", "conflict"} or refreshed is None:
                return ReconciliationResult("retry", "recording_unresolved")
            return await self._stop_conflicting_identities(
                refreshed,
                tuple(item.egress_id for item in exact),
            )

        if len(exact) == 1:
            status, refreshed = await self._attach_exact_identity(
                snapshot,
                exact[0],
            )
            if status == "missing":
                return ReconciliationResult("complete")
            if status == "conflict":
                if refreshed is None:
                    return ReconciliationResult("retry", "recording_unresolved")
                return await self._stop_conflicting_identities(
                    refreshed,
                    (exact[0].egress_id,),
                )
            if status != "updated" or refreshed is None:
                return ReconciliationResult("retry", "recording_unresolved")
            return await self._reconcile_known(refreshed)

        error_code: RecordingReconciliationErrorCode = (
            "recording_unresolved" if not listed else "recording_identity_mismatch"
        )
        return await self._record_retry(snapshot, error_code)

    async def _reconcile_conflict(
        self,
        snapshot: _OperationSnapshot,
    ) -> ReconciliationResult:
        exact_ids: tuple[str, ...] = ()
        if not snapshot.legacy_incomplete and snapshot.room_name:
            try:
                listed = await self.provider.list_room_egresses(
                    room_name=snapshot.room_name
                )
            except Exception:
                listed = ()
            exact_ids = tuple(
                dict.fromkeys(
                    item.egress_id
                    for item in listed
                    if item.room_name == snapshot.room_name
                    and item.object_key == snapshot.expected_object_key
                )
            )

        status, refreshed = await self._persist_identity_conflict(snapshot)
        if status == "missing":
            return ReconciliationResult("complete")
        if status != "updated" or refreshed is None:
            return ReconciliationResult("retry", "recording_unresolved")
        return await self._stop_conflicting_identities(refreshed, exact_ids)

    async def _stop_conflicting_identities(
        self,
        snapshot: _OperationSnapshot,
        exact_ids: tuple[str, ...],
    ) -> ReconciliationResult:
        safe_ids = tuple(
            dict.fromkeys(
                candidate
                for candidate in (snapshot.provider_egress_id, *exact_ids)
                if candidate is not None
            )
        )
        for egress_id in safe_ids:
            try:
                await self.provider.ensure_not_running(egress_id)
            except Exception:
                continue
        return ReconciliationResult("retry", RECORDING_IDENTITY_CONFLICT_CODE)

    async def _delete_object_and_operation(
        self,
        snapshot: _OperationSnapshot,
    ) -> ReconciliationResult:
        if snapshot.last_error_code == RECORDING_IDENTITY_CONFLICT_CODE:
            return ReconciliationResult(
                "retry",
                RECORDING_IDENTITY_CONFLICT_CODE,
            )
        if not self._safe_to_delete_object(snapshot):
            return await self._record_retry(snapshot, "recording_unresolved")

        if snapshot.object_deleted_at is None:
            try:
                await self.storage.delete_object(
                    object_key=snapshot.expected_object_key
                )
            except FileNotFoundError:
                pass
            except Exception:
                return await self._record_retry(
                    snapshot,
                    "recording_storage_unavailable",
                )

            status, refreshed = await self._persist_object_deleted(snapshot)
            if status == "missing":
                return ReconciliationResult("complete")
            if status == "conflict":
                return ReconciliationResult(
                    "retry",
                    RECORDING_IDENTITY_CONFLICT_CODE,
                )
            if status != "updated" or refreshed is None:
                return ReconciliationResult("retry", "recording_unresolved")
            snapshot = refreshed

        status = await self._remove_deleted_operation(snapshot)
        if status in {"updated", "missing"}:
            return ReconciliationResult("complete")
        if status == "conflict":
            return ReconciliationResult(
                "retry",
                RECORDING_IDENTITY_CONFLICT_CODE,
            )
        return ReconciliationResult("retry", "recording_unresolved")

    async def _record_retry(
        self,
        snapshot: _OperationSnapshot,
        error_code: RecordingReconciliationErrorCode,
    ) -> ReconciliationResult:
        status, current_error = await self._persist_inspection_error(
            snapshot,
            error_code,
        )
        if status == "missing":
            return ReconciliationResult("complete")
        if status == "changed":
            return ReconciliationResult("retry", "recording_unresolved")
        if status == "conflict" or current_error == RECORDING_IDENTITY_CONFLICT_CODE:
            return ReconciliationResult(
                "retry",
                RECORDING_IDENTITY_CONFLICT_CODE,
            )
        return ReconciliationResult("retry", error_code)

    async def _persist_inspection_error(
        self,
        snapshot: _OperationSnapshot,
        error_code: RecordingReconciliationErrorCode,
    ) -> tuple[_PersistenceStatus, str | None]:
        async with self.session_factory() as session:
            try:
                locked = await self._lock_call_then_operation(
                    session,
                    snapshot.operation_id,
                )
            except _ConcurrentStateChange:
                await session.rollback()
                return "changed", None
            if locked is None:
                await session.commit()
                return "missing", None
            call, operation = locked
            if operation.last_error_code == RECORDING_IDENTITY_CONFLICT_CODE:
                self._hide_playback_projection(call)
                operation.last_reconciled_at = _as_utc(self.now())
                await session.flush()
                await session.commit()
                return "conflict", RECORDING_IDENTITY_CONFLICT_CODE
            if not self._same_unknown_or_known_state(operation, snapshot):
                current_error = operation.last_error_code
                await session.rollback()
                return "changed", current_error

            operation.last_error_code = error_code
            durable_error = error_code
            operation.last_reconciled_at = _as_utc(self.now())
            await session.flush()
            await session.commit()
            return "updated", durable_error

    async def _persist_provider_terminal(
        self,
        snapshot: _OperationSnapshot,
    ) -> tuple[_PersistenceStatus, _OperationSnapshot | None]:
        async with self.session_factory() as session:
            try:
                locked = await self._lock_call_then_operation(
                    session,
                    snapshot.operation_id,
                )
            except _ConcurrentStateChange:
                await session.rollback()
                return "changed", None
            if locked is None:
                await session.commit()
                return "missing", None
            call, operation = locked
            if operation.last_error_code == RECORDING_IDENTITY_CONFLICT_CODE:
                self._hide_playback_projection(call)
                operation.last_reconciled_at = _as_utc(self.now())
                await session.flush()
                refreshed = self._snapshot(call, operation)
                await session.commit()
                return "conflict", refreshed
            if (
                operation.call_id != snapshot.call_id
                or operation.provider_egress_id != snapshot.provider_egress_id
                or operation.start_state != "started"
                or operation.expected_object_key != snapshot.expected_object_key
                or operation.stop_requested_at is None
            ):
                await session.rollback()
                return "changed", None

            if operation.provider_terminal_at is None:
                operation.provider_terminal_at = _as_utc(self.now())
            operation.last_reconciled_at = _as_utc(self.now())
            operation.last_error_code = None
            await session.flush()
            refreshed = self._snapshot(call, operation)
            await session.commit()
            return "updated", refreshed

    async def _attach_exact_identity(
        self,
        snapshot: _OperationSnapshot,
        exact: RecordingEgressSnapshot,
    ) -> tuple[_PersistenceStatus, _OperationSnapshot | None]:
        async with self.session_factory() as session:
            try:
                locked = await self._lock_call_then_operation(
                    session,
                    snapshot.operation_id,
                )
            except _ConcurrentStateChange:
                await session.rollback()
                return "changed", None
            if locked is None:
                return await self._restore_or_merge_missing_conflict(
                    session,
                    snapshot,
                    exact.egress_id,
                )
            call, operation = locked
            if (
                operation.call_id != snapshot.call_id
                or operation.room_name != snapshot.room_name
                or operation.legacy_incomplete != snapshot.legacy_incomplete
                or operation.expected_object_key != snapshot.expected_object_key
            ):
                await session.rollback()
                return "changed", None

            if operation.last_error_code == RECORDING_IDENTITY_CONFLICT_CODE:
                self._hide_playback_projection(call)
                operation.last_reconciled_at = _as_utc(self.now())
                await session.flush()
                refreshed = self._snapshot(call, operation)
                await session.commit()
                return "conflict", refreshed

            projection_conflicts = call.deleted_at is None and (
                call.recording_object_key not in {None, operation.expected_object_key}
                or call.recording_egress_id not in {None, exact.egress_id}
            )
            known_identity_conflict = (
                operation.provider_egress_id is not None
                and operation.provider_egress_id != exact.egress_id
            )
            if known_identity_conflict or projection_conflicts:
                if operation.provider_egress_id is None:
                    operation.start_state = "uncertain"
                operation.last_error_code = RECORDING_IDENTITY_CONFLICT_CODE
                operation.last_reconciled_at = _as_utc(self.now())
                self._hide_playback_projection(call)
                await session.flush()
                refreshed = self._snapshot(call, operation)
                await session.commit()
                return "conflict", refreshed

            if operation.provider_egress_id is not None:
                if (
                    operation.start_state != "started"
                    or operation.provider_egress_id != exact.egress_id
                ):
                    await session.rollback()
                    return "changed", None
                operation.last_reconciled_at = _as_utc(self.now())
                if call.deleted_at is not None:
                    self._hide_playback_projection(call)
                await session.flush()
                refreshed = self._snapshot(call, operation)
                await session.commit()
                return "updated", refreshed

            if operation.start_state in {"prepared", "not_started"}:
                operation.start_state = "uncertain"
                operation.last_error_code = RECORDING_IDENTITY_CONFLICT_CODE
                operation.last_reconciled_at = _as_utc(self.now())
                self._hide_playback_projection(call)
                await session.flush()
                refreshed = self._snapshot(call, operation)
                await session.commit()
                return "conflict", refreshed

            if operation.start_state not in {"starting", "uncertain"}:
                await session.rollback()
                return "changed", None

            operation.start_state = "started"
            operation.provider_egress_id = exact.egress_id
            operation.last_error_code = None
            operation.last_reconciled_at = _as_utc(self.now())
            if call.deleted_at is None:
                call.recording_object_key = operation.expected_object_key
                call.recording_egress_id = exact.egress_id
                # A listing snapshot has no trusted customer URL. Preserve any
                # already-stored URL and let playback derive from the object key.
            await session.flush()
            refreshed = self._snapshot(call, operation)
            await session.commit()
            return "updated", refreshed

    async def _restore_or_merge_missing_conflict(
        self,
        session: AsyncSession,
        snapshot: _OperationSnapshot,
        recovered_provider_id: str | None,
    ) -> tuple[_PersistenceStatus, _OperationSnapshot | None]:
        calls = CallRepository(session)
        operations = RecordingEgressOperationRepository(session)
        call = await calls.get_by_id_including_deleted_for_update(snapshot.call_id)
        if call is None:
            await session.rollback()
            return "changed", None

        operation = await operations.get_by_id_for_update(snapshot.operation_id)
        if operation is None:
            operation = await operations.add(
                RecordingEgressOperation(
                    id=snapshot.operation_id,
                    call_id=snapshot.call_id,
                    room_name=snapshot.room_name,
                    legacy_incomplete=snapshot.legacy_incomplete,
                    expected_object_key=snapshot.expected_object_key,
                    provider_egress_id=recovered_provider_id,
                    start_state=(
                        "started"
                        if recovered_provider_id is not None
                        else "uncertain"
                    ),
                    stop_requested_at=snapshot.stop_requested_at,
                    delete_requested_at=snapshot.delete_requested_at,
                    last_reconciled_at=_as_utc(self.now()),
                    last_error_code=RECORDING_IDENTITY_CONFLICT_CODE,
                )
            )
        elif (
            operation.call_id != snapshot.call_id
            or operation.room_name != snapshot.room_name
            or operation.legacy_incomplete != snapshot.legacy_incomplete
            or operation.expected_object_key != snapshot.expected_object_key
        ):
            await session.rollback()
            return "changed", None
        else:
            if operation.provider_egress_id is None:
                operation.start_state = "uncertain"
            if operation.stop_requested_at is None:
                operation.stop_requested_at = snapshot.stop_requested_at
            if operation.delete_requested_at is None:
                operation.delete_requested_at = snapshot.delete_requested_at
            operation.last_reconciled_at = _as_utc(self.now())
            operation.last_error_code = RECORDING_IDENTITY_CONFLICT_CODE

        self._hide_playback_projection(call)
        await OutboxService(session, now_provider=self.now).add(
            topic="recording.reconcile",
            aggregate_type=RECORDING_AGGREGATE_TYPE,
            aggregate_id=operation.id,
            idempotency_key=(
                f"recording.reconcile:{operation.id}:missing-operation-conflict"
            ),
            payload={"operation_id": str(operation.id)},
            next_attempt_at=_as_utc(self.now()),
        )
        await session.flush()
        refreshed = self._snapshot(call, operation)
        await session.commit()
        return "conflict", refreshed

    async def _persist_identity_conflict(
        self,
        snapshot: _OperationSnapshot,
    ) -> tuple[_PersistenceStatus, _OperationSnapshot | None]:
        async with self.session_factory() as session:
            try:
                locked = await self._lock_call_then_operation(
                    session,
                    snapshot.operation_id,
                )
            except _ConcurrentStateChange:
                await session.rollback()
                return "changed", None
            if locked is None:
                return await self._restore_or_merge_missing_conflict(
                    session,
                    snapshot,
                    None,
                )
            call, operation = locked
            if (
                operation.call_id != snapshot.call_id
                or operation.room_name != snapshot.room_name
                or operation.legacy_incomplete != snapshot.legacy_incomplete
                or operation.expected_object_key != snapshot.expected_object_key
            ):
                await session.rollback()
                return "changed", None

            if operation.provider_egress_id is None:
                operation.start_state = "uncertain"
            operation.last_error_code = RECORDING_IDENTITY_CONFLICT_CODE
            operation.last_reconciled_at = _as_utc(self.now())
            self._hide_playback_projection(call)
            await session.flush()
            refreshed = self._snapshot(call, operation)
            await session.commit()
            return "updated", refreshed

    async def _persist_object_deleted(
        self,
        snapshot: _OperationSnapshot,
    ) -> tuple[_PersistenceStatus, _OperationSnapshot | None]:
        async with self.session_factory() as session:
            try:
                locked = await self._lock_call_then_operation(
                    session,
                    snapshot.operation_id,
                )
            except _ConcurrentStateChange:
                await session.rollback()
                return "changed", None
            if locked is None:
                await session.commit()
                return "missing", None
            call, operation = locked
            if operation.last_error_code == RECORDING_IDENTITY_CONFLICT_CODE:
                self._hide_playback_projection(call)
                operation.last_reconciled_at = _as_utc(self.now())
                await session.flush()
                refreshed = self._snapshot(call, operation)
                await session.commit()
                return "conflict", refreshed
            if (
                operation.call_id != snapshot.call_id
                or operation.expected_object_key != snapshot.expected_object_key
                or operation.start_state != snapshot.start_state
                or operation.provider_egress_id != snapshot.provider_egress_id
                or not _same_instant(
                    operation.provider_terminal_at,
                    snapshot.provider_terminal_at,
                )
                or operation.delete_requested_at is None
                or not self._safe_to_delete_operation_object(operation)
            ):
                await session.rollback()
                return "changed", None

            if operation.object_deleted_at is None:
                operation.object_deleted_at = _as_utc(self.now())
            operation.last_reconciled_at = _as_utc(self.now())
            operation.last_error_code = None
            await session.flush()
            refreshed = self._snapshot(call, operation)
            await session.commit()
            return "updated", refreshed

    async def _remove_deleted_operation(
        self,
        snapshot: _OperationSnapshot,
    ) -> _PersistenceStatus:
        async with self.session_factory() as session:
            try:
                locked = await self._lock_call_then_operation(
                    session,
                    snapshot.operation_id,
                )
            except _ConcurrentStateChange:
                await session.rollback()
                return "changed"
            if locked is None:
                await session.commit()
                return "missing"
            call, operation = locked
            if operation.last_error_code == RECORDING_IDENTITY_CONFLICT_CODE:
                self._hide_playback_projection(call)
                operation.last_reconciled_at = _as_utc(self.now())
                await session.flush()
                await session.commit()
                return "conflict"
            if (
                operation.call_id != snapshot.call_id
                or operation.expected_object_key != snapshot.expected_object_key
                or operation.start_state != snapshot.start_state
                or operation.provider_egress_id != snapshot.provider_egress_id
                or not _same_instant(
                    operation.provider_terminal_at,
                    snapshot.provider_terminal_at,
                )
                or operation.delete_requested_at is None
                or operation.object_deleted_at is None
                or not self._safe_to_delete_operation_object(operation)
            ):
                await session.rollback()
                return "changed"

            await RecordingEgressOperationRepository(session).delete(operation)
            await session.commit()
            return "updated"

    async def _lock_call_then_operation(
        self,
        session: AsyncSession,
        operation_id: UUID,
    ) -> tuple[Call, RecordingEgressOperation] | None:
        operations = RecordingEgressOperationRepository(session)
        discovered = await operations.get_by_id(operation_id)
        if discovered is None:
            return None
        discovered_call_id = discovered.call_id
        call = await CallRepository(session).get_by_id_including_deleted_for_update(
            discovered_call_id
        )
        if call is None:
            raise _ConcurrentStateChange
        operation = await operations.get_by_id_for_update(operation_id)
        if operation is None:
            return None
        if operation.call_id != call.id:
            raise _ConcurrentStateChange
        return call, operation

    @staticmethod
    def _same_unknown_or_known_state(
        operation: RecordingEgressOperation,
        snapshot: _OperationSnapshot,
    ) -> bool:
        return (
            operation.call_id == snapshot.call_id
            and operation.room_name == snapshot.room_name
            and operation.legacy_incomplete == snapshot.legacy_incomplete
            and operation.expected_object_key == snapshot.expected_object_key
            and operation.provider_egress_id == snapshot.provider_egress_id
            and operation.start_state == snapshot.start_state
            and _same_instant(
                operation.provider_terminal_at,
                snapshot.provider_terminal_at,
            )
            and _same_instant(
                operation.object_deleted_at,
                snapshot.object_deleted_at,
            )
        )

    @staticmethod
    def _safe_to_delete_object(snapshot: _OperationSnapshot) -> bool:
        return snapshot.last_error_code != RECORDING_IDENTITY_CONFLICT_CODE and (
            snapshot.start_state == "not_started"
            or (
                snapshot.provider_egress_id is not None
                and snapshot.provider_terminal_at is not None
            )
        )

    @staticmethod
    def _safe_to_delete_operation_object(
        operation: RecordingEgressOperation,
    ) -> bool:
        return operation.last_error_code != RECORDING_IDENTITY_CONFLICT_CODE and (
            operation.start_state == "not_started"
            or (
                operation.provider_egress_id is not None
                and operation.provider_terminal_at is not None
            )
        )

    @staticmethod
    def _hide_playback_projection(call: Call) -> None:
        call.recording_object_key = None
        call.recording_egress_id = None
        call.recording_url = None

    @staticmethod
    def _snapshot(
        call: Call,
        operation: RecordingEgressOperation,
    ) -> _OperationSnapshot:
        return _OperationSnapshot(
            operation_id=operation.id,
            call_id=call.id,
            room_name=operation.room_name,
            legacy_incomplete=operation.legacy_incomplete,
            expected_object_key=operation.expected_object_key,
            provider_egress_id=operation.provider_egress_id,
            start_state=operation.start_state,
            stop_requested_at=operation.stop_requested_at,
            delete_requested_at=operation.delete_requested_at,
            provider_terminal_at=operation.provider_terminal_at,
            object_deleted_at=operation.object_deleted_at,
            last_error_code=operation.last_error_code,
        )
