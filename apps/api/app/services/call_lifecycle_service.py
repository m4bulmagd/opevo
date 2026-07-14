from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.repositories.call_repository import CallRepository, CallTransitionError
from app.repositories.message_repository import MessageRepository
from app.repositories.notification_repository import NotificationRepository
from app.services.outbox_service import OutboxService
from app.services.usage_accounting_service import UsageAccountingService


@dataclass(frozen=True)
class FinalizationClaim:
    generation: int
    already_completed: bool = False
    unavailable: bool = False


@dataclass(frozen=True)
class CallFinalizationResult:
    minutes_charged: int
    already_completed: bool = False
    stale_generation: bool = False


class CallLifecycleService:
    """Owns durable end facts and provider-free two-phase finalization."""

    def __init__(
        self,
        session,
        *,
        call_repository: CallRepository | None = None,
        usage_accounting_service: UsageAccountingService | None = None,
        notification_repository: NotificationRepository | None = None,
        message_repository: MessageRepository | None = None,
        outbox_service: OutboxService | None = None,
    ) -> None:
        self.session = session
        self.call_repository = call_repository or CallRepository(session)
        self.usage_accounting_service = usage_accounting_service or UsageAccountingService(session)
        self.notification_repository = notification_repository or NotificationRepository(session)
        self.message_repository = message_repository or MessageRepository(session)
        self.outbox_service = outbox_service or OutboxService(session)

    async def end_from_agent(
        self,
        *,
        call_id: UUID,
        duration_seconds: int,
        ended_at: datetime | None = None,
    ):
        if duration_seconds < 0:
            raise ValueError("Call duration must be nonnegative")
        call = await self.call_repository.get_by_id_for_update(call_id)
        if call is None:
            raise ValueError("Call not found")
        if call.status == "failed":
            raise CallTransitionError("Failed call cannot accept completion")
        if call.status in {"ending", "finalizing", "completed"}:
            return call
        if call.status not in {"pending", "connected"}:
            raise CallTransitionError("Call cannot be ended from its current state")

        frozen_end = ended_at or datetime.now(UTC)
        if call.ended_at is None:
            call.ended_at = frozen_end
        if call.duration_seconds is None:
            call.duration_seconds = duration_seconds
        if call.started_at is None:
            lower_bound = self._as_utc(call.created_at)
            candidate = self._as_utc(call.ended_at) - timedelta(seconds=call.duration_seconds)
            call.started_at = max(lower_bound, candidate)
        call.status = "ending"
        call.failure_code = None
        call.state_changed_at = datetime.now(UTC)
        await self._add_recording_stop_intent(call)
        await self.session.flush()
        return call

    async def end_from_sip(
        self,
        *,
        call_id: UUID,
        ended_at: datetime | None = None,
    ):
        call = await self.call_repository.get_by_id_for_update(call_id)
        if call is None:
            raise ValueError("Call not found")
        if call.status == "pending":
            failed = await self.call_repository.transition(
                call.id,
                from_states={"pending"},
                to_state="failed",
                failure_code="caller_left_before_connect",
            )
            failed.ended_at = failed.ended_at or ended_at or datetime.now(UTC)
            failed.duration_seconds = failed.duration_seconds or 0
            await self._add_recording_stop_intent(failed)
            await self.session.flush()
            return failed
        if call.status != "connected":
            return call

        frozen_end = ended_at or datetime.now(UTC)
        started_at = self._as_utc(call.started_at or call.created_at)
        duration_seconds = max(
            0,
            int((self._as_utc(frozen_end) - started_at).total_seconds()),
        )
        return await self.end_from_agent(
            call_id=call.id,
            duration_seconds=duration_seconds,
            ended_at=frozen_end,
        )

    async def claim_finalization(self, call_id: UUID) -> FinalizationClaim:
        try:
            call = await self.call_repository.get_by_id_for_update(call_id)
            if call is None:
                raise ValueError("Call not found")
            if call.status == "completed":
                claim = FinalizationClaim(
                    generation=call.finalization_attempt_count,
                    already_completed=True,
                )
            elif call.status == "failed":
                claim = FinalizationClaim(
                    generation=call.finalization_attempt_count,
                    unavailable=True,
                )
            elif call.status == "finalizing":
                claim = FinalizationClaim(generation=call.finalization_attempt_count)
            elif call.status == "ending":
                self._normalize_end_facts(call)
                call.status = "finalizing"
                call.failure_code = None
                call.finalization_attempt_count += 1
                call.state_changed_at = datetime.now(UTC)
                await self.session.flush()
                claim = FinalizationClaim(generation=call.finalization_attempt_count)
            else:
                raise CallTransitionError("Call is not ready for finalization")
            await self.session.commit()
            return claim
        except Exception:
            await self.session.rollback()
            raise

    async def complete_finalization(
        self,
        call_id: UUID,
        *,
        generation: int,
    ) -> CallFinalizationResult:
        try:
            call = await self.call_repository.get_by_id_for_update(call_id)
            if call is None:
                raise ValueError("Call not found")
            if call.status == "completed":
                result = self._completed_result(call, already_completed=True)
                await self.session.commit()
                return result
            if call.status != "finalizing" or call.finalization_attempt_count != generation:
                minutes_charged = call.minutes_charged or 0
                await self.session.rollback()
                return CallFinalizationResult(
                    minutes_charged=minutes_charged,
                    stale_generation=True,
                )
            duration_seconds = self._normalize_end_facts(call)

            debit = await self.usage_accounting_service.debit_call(
                call_id=call.id,
                duration_seconds=duration_seconds,
            )
            await self.notification_repository.get_or_create(
                user_id=call.user_id,
                call_id=call.id,
                notification_type="call_completed",
                status="pending",
                payload={"event": "call_completed", "call_id": str(call.id)},
            )
            transcript_version = await self.message_repository.max_sequence_by_call_id(
                call.id
            )
            await self.outbox_service.add(
                topic="summary.generate",
                aggregate_type="call-summary",
                aggregate_id=call.id,
                idempotency_key=(
                    f"summary.generate:{call.id}:v{transcript_version}"
                ),
                payload={"call_id": str(call.id)},
            )
            if call.recording_egress_id:
                await self.outbox_service.add(
                    topic="recording.stop",
                    aggregate_type="call-recording",
                    aggregate_id=call.id,
                    idempotency_key=f"recording.stop:{call.id}",
                    payload={"call_id": str(call.id)},
                )
            if debit.balance_after == 0:
                await self.outbox_service.add(
                    topic="phone.disable",
                    aggregate_type="user",
                    aggregate_id=call.user_id,
                    idempotency_key=f"phone.disable:call:{call.id}",
                    payload={"user_id": str(call.user_id)},
                )

            call.minutes_charged = debit.minutes_charged
            call.status = "completed"
            call.failure_code = None
            call.state_changed_at = datetime.now(UTC)
            await self.session.flush()
            result = self._completed_result(call, already_completed=False)
            await self.session.commit()
            return result
        except Exception:
            await self.session.rollback()
            raise

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _completed_result(call, *, already_completed: bool) -> CallFinalizationResult:
        return CallFinalizationResult(
            minutes_charged=call.minutes_charged or 0,
            already_completed=already_completed,
        )

    @classmethod
    def _normalize_end_facts(cls, call) -> int:
        """Fill incomplete legacy/recovery facts without using wall-clock time."""
        created_at = cls._as_utc(call.created_at)
        state_changed_at = cls._as_utc(call.state_changed_at or call.created_at)
        started_at = cls._as_utc(call.started_at) if call.started_at else None
        ended_at = cls._as_utc(call.ended_at) if call.ended_at else None
        duration_seconds = call.duration_seconds

        if started_at is None and ended_at is None:
            ended_at = state_changed_at
            if duration_seconds is None:
                started_at = created_at
            else:
                started_at = max(
                    created_at,
                    ended_at - timedelta(seconds=duration_seconds),
                )
        elif started_at is None:
            assert ended_at is not None
            if duration_seconds is None:
                started_at = created_at
            else:
                started_at = max(
                    created_at,
                    ended_at - timedelta(seconds=duration_seconds),
                )
        elif ended_at is None:
            if duration_seconds is None:
                ended_at = max(started_at, state_changed_at)
            else:
                ended_at = started_at + timedelta(seconds=duration_seconds)

        assert started_at is not None
        assert ended_at is not None
        call.started_at = started_at
        call.ended_at = ended_at
        if duration_seconds is None:
            call.duration_seconds = max(
                0,
                int((ended_at - started_at).total_seconds()),
            )
        return call.duration_seconds

    async def _add_recording_stop_intent(self, call) -> None:
        if not call.recording_egress_id:
            return
        await self.outbox_service.add(
            topic="recording.stop",
            aggregate_type="call-recording",
            aggregate_id=call.id,
            idempotency_key=f"recording.stop:{call.id}",
            payload={"call_id": str(call.id)},
        )
