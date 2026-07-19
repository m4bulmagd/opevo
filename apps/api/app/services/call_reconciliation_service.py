from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from uuid import UUID

from app.core.config import get_settings
from app.repositories.call_repository import CallRepository
from app.repositories.usage_repository import UsageRepository
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.livekit_dispatch_lock import livekit_dispatch_lock
from app.services.recording_lifecycle_service import RecordingLifecycleService


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconciliationResult:
    scanned: int = 0
    recovered: int = 0
    failed: int = 0
    deferred: int = 0


class CallReconciliationService:
    def __init__(self, session_factory, *, settings=None) -> None:
        self.session_factory = session_factory
        self.settings = settings or get_settings()

    async def reconcile(
        self,
        now: datetime,
        limit: int = 100,
    ) -> ReconciliationResult:
        bounded_limit = max(0, min(limit, 100))
        if bounded_limit == 0:
            return ReconciliationResult()

        scanned = recovered = failed = deferred = 0
        pending_before = now - timedelta(
            seconds=self.settings.call_reconciliation_pending_stale_seconds
        )
        async with self.session_factory() as session:
            pending_ids = await CallRepository(session).list_stale_pending_ids(
                stale_before=pending_before,
                limit=bounded_limit,
            )
            await session.commit()

        for call_id in pending_ids:
            async with livekit_dispatch_lock(self.session_factory, call_id):
                async with self.session_factory() as session:
                    repository = CallRepository(session)
                    call = await repository.get_by_id_for_update(call_id)
                    if (
                        call is None
                        or call.status != "pending"
                        or self._as_aware(call.state_changed_at) > pending_before
                    ):
                        await session.rollback()
                        continue
                    await repository.mark_dispatch_failed(
                        call,
                        failure_code="dispatch_timeout",
                    )
                    await RecordingLifecycleService(
                        session,
                        now_provider=lambda: now,
                    ).request_stop(call)
                    call.last_reconciled_at = now
                    await session.commit()
                    scanned += 1
                    failed += 1

        remaining = bounded_limit - scanned
        claims: list[tuple[UUID, int]] = []
        if remaining > 0:
            async with self.session_factory() as session:
                repository = CallRepository(session)
                rows = await repository.claim_stale_reconciliation_rows(
                    connected_before=now
                    - timedelta(
                        seconds=self.settings.call_reconciliation_connected_stale_seconds
                    ),
                    ending_before=now
                    - timedelta(
                        seconds=self.settings.call_reconciliation_ending_grace_seconds
                    ),
                    finalizing_before=now
                    - timedelta(
                        seconds=self.settings.call_reconciliation_finalizing_lease_seconds
                    ),
                    limit=remaining,
                )
                usage_repository = UsageRepository(session)
                recording_lifecycle = RecordingLifecycleService(
                    session,
                    now_provider=lambda: now,
                )
                lifecycle = CallLifecycleService(
                    session,
                    call_repository=repository,
                    recording_lifecycle_service=recording_lifecycle,
                )
                for call in rows:
                    scanned += 1
                    if call.status == "connected":
                        started_at = self._as_aware(call.started_at or call.created_at)
                        bounded_end = min(
                            self._as_aware(now),
                            started_at
                            + timedelta(
                                seconds=self.settings.call_reconciliation_connected_stale_seconds
                            ),
                        )
                        await lifecycle.end_from_sip(
                            call_id=call.id,
                            ended_at=bounded_end,
                        )
                        call.last_reconciled_at = now
                        recovered += 1
                        continue

                    await recording_lifecycle.request_stop(call)
                    debit = await usage_repository.get_call_debit(call_id=call.id)
                    at_attempt_cap = (
                        call.finalization_attempt_count
                        >= self.settings.call_reconciliation_max_attempts
                    )
                    if at_attempt_cap and debit is None:
                        if call.status == "ending":
                            await repository.transition(
                                call.id,
                                from_states={"ending"},
                                to_state="finalizing",
                            )
                        await repository.transition(
                            call.id,
                            from_states={"finalizing"},
                            to_state="failed",
                            failure_code="finalization_exhausted",
                        )
                        call.last_reconciled_at = now
                        failed += 1
                        continue

                    if call.status == "ending":
                        call.status = "finalizing"
                    if not at_attempt_cap:
                        call.finalization_attempt_count += 1
                    call.state_changed_at = now
                    call.last_reconciled_at = now
                    claims.append((call.id, call.finalization_attempt_count))
                await session.commit()

        for call_id, generation in claims:
            try:
                async with self.session_factory() as session:
                    result = await CallLifecycleService(session).complete_finalization(
                        call_id,
                        generation=generation,
                    )
            except Exception as error:
                logger.warning(
                    "call reconciliation deferred call_id=%s generation=%d "
                    "error_type=%s",
                    call_id,
                    generation,
                    type(error).__name__,
                    extra={
                        "event": "call_reconciliation_deferred",
                        "operation": "complete_call_finalization",
                        "status": "deferred",
                        "call_id": str(call_id),
                        "generation": generation,
                        "error_type": type(error).__name__,
                    },
                )
                deferred += 1
                continue
            if not result.stale_generation:
                recovered += 1

        return ReconciliationResult(
            scanned=scanned,
            recovered=recovered,
            failed=failed,
            deferred=deferred,
        )

    @staticmethod
    def _as_aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
