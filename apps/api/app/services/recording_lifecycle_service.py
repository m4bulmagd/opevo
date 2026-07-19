from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call
from app.models.recording_egress_operation import RecordingEgressOperation
from app.providers.livekit_recording.base import (
    RecordingEgressResult,
    StartOutcome,
    build_recording_object_key,
)
from app.repositories.call_repository import CallRepository
from app.repositories.outbox_repository import OutboxRepository
from app.repositories.recording_egress_operation_repository import (
    RecordingEgressOperationRepository,
)
from app.services.outbox_service import OutboxService


START_RESULT_LEASE = timedelta(minutes=2)
RECORDING_AGGREGATE_TYPE = "recording-egress-operation"
RECORDING_START_ERROR_CODES = frozenset(
    {
        "timeout",
        "rate_limited",
        "unavailable",
        "authentication",
        "validation",
        "conflict",
        "unknown",
    }
)


@dataclass(frozen=True)
class RecordingEgressEventFact:
    external_event_id: str
    event_type: Literal["egress_started", "egress_updated", "egress_ended"]
    egress_id: str
    room_name: str
    status: int
    object_key: str | None


@dataclass(frozen=True)
class RecordingStartClaim:
    operation_id: UUID
    call_id: UUID
    room_name: str
    expected_object_key: str


class RecordingLifecycleError(ValueError):
    pass


class RecordingLifecycleService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.call_repository = CallRepository(session)
        self.operation_repository = RecordingEgressOperationRepository(session)
        self.outbox_repository = OutboxRepository(session)
        self.now = now_provider or (lambda: datetime.now(UTC))
        self.outbox_service = OutboxService(session, now_provider=self.now)

    async def prepare_start(self, call: Call) -> RecordingEgressOperation:
        locked_call = (
            await self.call_repository.get_by_id_including_deleted_for_update(call.id)
        )
        if locked_call is None:
            raise RecordingLifecycleError("Recording call is unavailable")
        if locked_call.deleted_at is not None:
            raise RecordingLifecycleError("Recording call is deleted")
        if locked_call.status != "connected":
            raise RecordingLifecycleError("Recording call must be connected")
        if not locked_call.livekit_room_id:
            raise RecordingLifecycleError("Recording call room is required")

        expected_object_key = build_recording_object_key(
            user_id=locked_call.user_id,
            call_id=locked_call.id,
        )
        operation = await self.operation_repository.get_by_call_id_for_update(
            locked_call.id
        )
        if operation is None:
            operation = await self.operation_repository.add(
                RecordingEgressOperation(
                    call_id=locked_call.id,
                    room_name=locked_call.livekit_room_id,
                    legacy_incomplete=False,
                    expected_object_key=expected_object_key,
                    start_state="prepared",
                )
            )
        elif (
            operation.legacy_incomplete
            or operation.room_name != locked_call.livekit_room_id
            or operation.expected_object_key != expected_object_key
        ):
            raise RecordingLifecycleError("Recording operation identity conflicts")

        await self.outbox_service.add(
            topic="recording.reconcile",
            aggregate_type=RECORDING_AGGREGATE_TYPE,
            aggregate_id=operation.id,
            idempotency_key=f"recording.reconcile:{operation.id}:start",
            payload={"operation_id": str(operation.id)},
            next_attempt_at=self.now() + START_RESULT_LEASE,
        )
        await self.session.flush()
        return operation

    async def begin_start(
        self,
        operation_id: UUID,
    ) -> RecordingStartClaim | None:
        locked = await self._lock_call_then_operation(operation_id)
        if locked is None:
            return None
        call, operation = locked
        if (
            call.deleted_at is not None
            or call.status != "connected"
            or operation.start_state != "prepared"
            or operation.stop_requested_at is not None
            or operation.delete_requested_at is not None
            or operation.legacy_incomplete
            or not operation.room_name
        ):
            return None

        operation.start_state = "starting"
        operation.start_attempted_at = self.now()
        await self.session.flush()
        return RecordingStartClaim(
            operation_id=operation.id,
            call_id=call.id,
            room_name=operation.room_name,
            expected_object_key=operation.expected_object_key,
        )

    async def record_start_success(
        self,
        operation_id: UUID,
        result: RecordingEgressResult,
    ) -> RecordingEgressOperation | None:
        locked = await self._lock_call_then_operation(operation_id)
        if locked is None:
            return None
        call, operation = locked
        if result.object_key != operation.expected_object_key:
            raise RecordingLifecycleError("Recording result object key conflicts")
        if operation.start_state == "started":
            if operation.provider_egress_id != result.egress_id:
                raise RecordingLifecycleError(
                    "Recording provider identity conflicts"
                )
        elif operation.start_state not in {"starting", "uncertain"}:
            raise RecordingLifecycleError("Recording start state conflicts")

        if call.deleted_at is None:
            if call.recording_object_key not in {None, result.object_key}:
                raise RecordingLifecycleError(
                    "Recording projection object key conflicts"
                )
            if call.recording_egress_id not in {None, result.egress_id}:
                raise RecordingLifecycleError(
                    "Recording projection provider identity conflicts"
                )

        operation.start_state = "started"
        operation.provider_egress_id = result.egress_id
        operation.last_error_code = None
        if call.deleted_at is None:
            call.recording_object_key = result.object_key
            call.recording_egress_id = result.egress_id
            call.recording_url = result.url

        await self.outbox_repository.make_oldest_pending_due(
            aggregate_type=RECORDING_AGGREGATE_TYPE,
            aggregate_id=operation.id,
            due_at=self.now(),
        )
        await self.session.flush()
        return operation

    async def record_start_error(
        self,
        operation_id: UUID,
        *,
        outcome: StartOutcome,
        error_code: str,
    ) -> RecordingEgressOperation | None:
        if outcome not in {"not_started", "unknown"}:
            raise RecordingLifecycleError("Recording start outcome is invalid")
        if error_code not in RECORDING_START_ERROR_CODES:
            raise RecordingLifecycleError("Recording start error code is invalid")

        locked = await self._lock_call_then_operation(operation_id)
        if locked is None:
            return None
        _, operation = locked
        if operation.start_state == "prepared":
            raise RecordingLifecycleError("Recording start state conflicts")
        if operation.start_state == "starting":
            operation.start_state = (
                "not_started" if outcome == "not_started" else "uncertain"
            )
        elif operation.start_state == "uncertain":
            operation.start_state = "uncertain"
        elif operation.start_state == "not_started":
            operation.start_state = "not_started"

        if operation.start_state != "started":
            operation.last_error_code = error_code
        await self.outbox_repository.make_oldest_pending_due(
            aggregate_type=RECORDING_AGGREGATE_TYPE,
            aggregate_id=operation.id,
            due_at=self.now(),
        )
        await self.session.flush()
        return operation

    async def request_stop(
        self,
        call: Call,
    ) -> RecordingEgressOperation | None:
        return await self._request_for_call(call, "stop")

    async def request_deletion(
        self,
        call: Call,
    ) -> RecordingEgressOperation | None:
        return await self._request_for_call(call, "delete")

    async def _request_for_call(
        self,
        call: Call,
        phase: Literal["stop", "delete"],
    ) -> RecordingEgressOperation | None:
        locked_call = (
            await self.call_repository.get_by_id_including_deleted_for_update(call.id)
        )
        if locked_call is None:
            return None
        operation = await self.operation_repository.get_by_call_id_for_update(
            locked_call.id
        )
        if operation is None:
            operation = await self._repair_legacy_operation(locked_call)
        if operation is None:
            return None
        return await self._request(operation, phase)

    async def _repair_legacy_operation(
        self,
        call: Call,
    ) -> RecordingEgressOperation | None:
        has_playback_metadata = any(
            (
                call.recording_object_key,
                call.recording_egress_id,
                call.recording_url,
            )
        )
        if not has_playback_metadata:
            return None

        room_name = call.livekit_room_id or None
        provider_egress_id = call.recording_egress_id or None
        return await self.operation_repository.add(
            RecordingEgressOperation(
                call_id=call.id,
                room_name=room_name,
                legacy_incomplete=room_name is None,
                expected_object_key=(
                    call.recording_object_key
                    or build_recording_object_key(
                        user_id=call.user_id,
                        call_id=call.id,
                    )
                ),
                provider_egress_id=provider_egress_id,
                start_state=(
                    "started" if provider_egress_id is not None else "uncertain"
                ),
            )
        )

    async def _request(
        self,
        operation: RecordingEgressOperation,
        phase: Literal["stop", "delete"],
    ) -> RecordingEgressOperation:
        requested_at = self.now()
        if operation.stop_requested_at is None:
            operation.stop_requested_at = requested_at
        if phase == "delete" and operation.delete_requested_at is None:
            operation.delete_requested_at = requested_at

        await self.outbox_service.add(
            topic="recording.reconcile",
            aggregate_type=RECORDING_AGGREGATE_TYPE,
            aggregate_id=operation.id,
            idempotency_key=f"recording.reconcile:{operation.id}:{phase}",
            payload={"operation_id": str(operation.id)},
            next_attempt_at=requested_at,
        )
        await self.outbox_repository.make_oldest_pending_due(
            aggregate_type=RECORDING_AGGREGATE_TYPE,
            aggregate_id=operation.id,
            due_at=requested_at,
        )
        await self.session.flush()
        return operation

    async def _lock_call_then_operation(
        self,
        operation_id: UUID,
    ) -> tuple[Call, RecordingEgressOperation] | None:
        discovered = await self.operation_repository.get_by_id(operation_id)
        if discovered is None:
            return None
        call = await self.call_repository.get_by_id_including_deleted_for_update(
            discovered.call_id
        )
        if call is None:
            return None
        operation = await self.operation_repository.get_by_id_for_update(operation_id)
        if operation is None or operation.call_id != call.id:
            return None
        return call, operation
