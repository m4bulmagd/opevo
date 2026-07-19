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
RECORDING_IDENTITY_CONFLICT_CODE: Literal["recording_identity_conflict"] = (
    "recording_identity_conflict"
)
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
EGRESS_EVENT_TYPES = frozenset({"egress_started", "egress_updated", "egress_ended"})
EGRESS_STATUSES = frozenset(range(7))
EGRESS_TERMINAL_STATUSES = frozenset({3, 4, 5, 6})


@dataclass(frozen=True)
class RecordingEgressEventFact:
    external_event_id: str
    event_type: Literal["egress_started", "egress_updated", "egress_ended"]
    egress_id: str
    room_name: str
    status: int
    object_key: str | None
    object_key_evidence: Literal["absent", "exact", "invalid"]


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
        locked_call = await self.call_repository.get_by_id_including_deleted_for_update(
            call.id
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
        identity_conflict = (
            operation.last_error_code == RECORDING_IDENTITY_CONFLICT_CODE
        )
        if operation.start_state == "started":
            if operation.provider_egress_id != result.egress_id:
                raise RecordingLifecycleError("Recording provider identity conflicts")
        elif operation.start_state not in {"starting", "uncertain"}:
            raise RecordingLifecycleError("Recording start state conflicts")

        if call.deleted_at is None and not identity_conflict:
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
        if identity_conflict:
            operation.last_error_code = RECORDING_IDENTITY_CONFLICT_CODE
            self._hide_playback_projection(call)
        else:
            operation.last_error_code = None
        if call.deleted_at is None and not identity_conflict:
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
        identity_conflict = (
            operation.last_error_code == RECORDING_IDENTITY_CONFLICT_CODE
        )
        if operation.start_state == "prepared":
            raise RecordingLifecycleError("Recording start state conflicts")
        if identity_conflict and operation.start_state in {"starting", "uncertain"}:
            operation.start_state = "uncertain"
        elif operation.start_state == "starting":
            operation.start_state = (
                "not_started" if outcome == "not_started" else "uncertain"
            )
        elif operation.start_state == "uncertain":
            operation.start_state = "uncertain"
        elif operation.start_state == "not_started":
            operation.start_state = "not_started"

        if identity_conflict:
            operation.last_error_code = RECORDING_IDENTITY_CONFLICT_CODE
        elif operation.start_state != "started":
            operation.last_error_code = error_code
        await self.outbox_repository.make_oldest_pending_due(
            aggregate_type=RECORDING_AGGREGATE_TYPE,
            aggregate_id=operation.id,
            due_at=self.now(),
        )
        await self.session.flush()
        return operation

    async def accept_egress_event(
        self,
        fact: RecordingEgressEventFact,
    ) -> Literal["accepted", "missing", "mismatch", "conflict"]:
        if not self._valid_egress_fact(fact):
            return "missing"
        if fact.object_key_evidence == "invalid":
            return "mismatch"

        discovered = await self.operation_repository.get_by_provider_egress_id(
            fact.egress_id
        )
        if discovered is None:
            discovered = await self.operation_repository.get_by_room_name(
                fact.room_name
            )
        if discovered is None:
            return "missing"

        locked = await self._lock_call_then_operation(discovered.id)
        if locked is None:
            return "missing"
        call, operation = locked

        if (
            operation.room_name != fact.room_name
            or call.livekit_room_id != fact.room_name
        ):
            return "mismatch"
        if (
            fact.object_key is not None
            and fact.object_key != operation.expected_object_key
        ):
            return "mismatch"

        known_identity = operation.provider_egress_id
        if known_identity is None and fact.object_key is None:
            return "mismatch"
        if (
            known_identity is not None
            and known_identity != fact.egress_id
            and fact.object_key_evidence == "absent"
        ):
            return "mismatch"
        if known_identity is not None and known_identity != fact.egress_id:
            await self._persist_egress_conflict(call, operation)
            return "conflict"

        contradictory_start = operation.start_state in {
            "prepared",
            "not_started",
        }
        sticky_conflict = operation.last_error_code == RECORDING_IDENTITY_CONFLICT_CODE
        projection_conflict = call.deleted_at is None and (
            call.recording_object_key not in {None, operation.expected_object_key}
            or call.recording_egress_id not in {None, fact.egress_id}
        )
        if contradictory_start or sticky_conflict or projection_conflict:
            terminal_changed = False
            if known_identity == fact.egress_id:
                terminal_changed = self._record_egress_terminal(operation, fact)
            await self._persist_egress_conflict(call, operation)
            if terminal_changed:
                await self._ensure_egress_reconcile_event(operation, "terminal")
            return "conflict"

        identity_attached = False
        if known_identity is None:
            if operation.start_state not in {"starting", "uncertain"}:
                await self._persist_egress_conflict(call, operation)
                return "conflict"
            operation.start_state = "started"
            operation.provider_egress_id = fact.egress_id
            operation.last_error_code = None
            identity_attached = True

        if call.deleted_at is not None:
            self._hide_playback_projection(call)
        else:
            call.recording_object_key = operation.expected_object_key
            call.recording_egress_id = fact.egress_id

        terminal_changed = self._record_egress_terminal(operation, fact)
        if identity_attached:
            await self._ensure_egress_reconcile_event(operation, "identity")
        if terminal_changed:
            await self._ensure_egress_reconcile_event(operation, "terminal")
        await self.outbox_repository.make_oldest_pending_due(
            aggregate_type=RECORDING_AGGREGATE_TYPE,
            aggregate_id=operation.id,
            due_at=self.now(),
        )
        await self.session.flush()
        return "accepted"

    @staticmethod
    def _valid_egress_fact(fact: RecordingEgressEventFact) -> bool:
        string_values = (
            (fact.external_event_id, 255),
            (fact.event_type, 100),
            (fact.egress_id, 255),
            (fact.room_name, 255),
        )
        return (
            all(
                type(value) is str
                and bool(value.strip())
                and len(value) <= limit
                and "\x00" not in value
                for value, limit in string_values
            )
            and fact.event_type in EGRESS_EVENT_TYPES
            and type(fact.status) is int
            and fact.status in EGRESS_STATUSES
            and type(fact.object_key_evidence) is str
            and fact.object_key_evidence in {"absent", "exact", "invalid"}
            and (
                (
                    fact.object_key_evidence == "exact"
                    and type(fact.object_key) is str
                    and bool(fact.object_key.strip())
                    and len(fact.object_key) <= 512
                    and "\x00" not in fact.object_key
                )
                or (
                    fact.object_key_evidence in {"absent", "invalid"}
                    and fact.object_key is None
                )
            )
        )

    def _record_egress_terminal(
        self,
        operation: RecordingEgressOperation,
        fact: RecordingEgressEventFact,
    ) -> bool:
        if (
            fact.event_type != "egress_ended"
            or fact.status not in EGRESS_TERMINAL_STATUSES
            or operation.provider_egress_id != fact.egress_id
            or operation.provider_terminal_at is not None
        ):
            return False
        operation.provider_terminal_at = self.now()
        return True

    async def _persist_egress_conflict(
        self,
        call: Call,
        operation: RecordingEgressOperation,
    ) -> None:
        if operation.start_state in {"prepared", "not_started"}:
            operation.start_state = "uncertain"
        operation.last_error_code = RECORDING_IDENTITY_CONFLICT_CODE
        self._hide_playback_projection(call)
        await self._ensure_egress_reconcile_event(operation, "conflict")
        await self.outbox_repository.make_oldest_pending_due(
            aggregate_type=RECORDING_AGGREGATE_TYPE,
            aggregate_id=operation.id,
            due_at=self.now(),
        )
        await self.session.flush()

    async def _ensure_egress_reconcile_event(
        self,
        operation: RecordingEgressOperation,
        phase: Literal["identity", "terminal", "conflict"],
    ) -> None:
        await self.outbox_service.add(
            topic="recording.reconcile",
            aggregate_type=RECORDING_AGGREGATE_TYPE,
            aggregate_id=operation.id,
            idempotency_key=(f"recording.reconcile:{operation.id}:webhook-{phase}"),
            payload={"operation_id": str(operation.id)},
            next_attempt_at=self.now(),
        )

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
        locked_call = await self.call_repository.get_by_id_including_deleted_for_update(
            call.id
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

    @staticmethod
    def _hide_playback_projection(call: Call) -> None:
        call.recording_object_key = None
        call.recording_egress_id = None
        call.recording_url = None
