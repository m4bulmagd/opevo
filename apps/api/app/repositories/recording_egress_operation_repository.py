from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call
from app.models.recording_egress_operation import RecordingEgressOperation


_START_STATE_ORDER = (
    "prepared",
    "starting",
    "started",
    "not_started",
    "uncertain",
)


@dataclass(frozen=True)
class RecordingOperationObservabilitySnapshot:
    counts: dict[str, int]
    oldest_unresolved_age_seconds: float
    pending_stop_count: int
    oldest_pending_stop_age_seconds: float
    pending_deletion_count: int
    oldest_pending_deletion_age_seconds: float


def _age_seconds(*, now: datetime, oldest: datetime | None) -> float:
    if oldest is None:
        return 0.0
    normalized_now = (
        now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    )
    normalized_oldest = (
        oldest.replace(tzinfo=UTC)
        if oldest.tzinfo is None
        else oldest.astimezone(UTC)
    )
    return max(0.0, (normalized_now - normalized_oldest).total_seconds())


class RecordingEgressOperationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(
        self,
        operation_id: UUID,
    ) -> RecordingEgressOperation | None:
        return await self.session.get(RecordingEgressOperation, operation_id)

    async def get_by_id_for_update(
        self,
        operation_id: UUID,
    ) -> RecordingEgressOperation | None:
        result = await self.session.execute(
            select(RecordingEgressOperation)
            .where(RecordingEgressOperation.id == operation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_by_call_id_for_update(
        self,
        call_id: UUID,
    ) -> RecordingEgressOperation | None:
        result = await self.session.execute(
            select(RecordingEgressOperation)
            .where(RecordingEgressOperation.call_id == call_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_by_call_id_for_user(
        self,
        *,
        call_id: UUID,
        user_id: UUID,
    ) -> RecordingEgressOperation | None:
        result = await self.session.execute(
            select(RecordingEgressOperation)
            .join(Call, Call.id == RecordingEgressOperation.call_id)
            .where(
                RecordingEgressOperation.call_id == call_id,
                Call.user_id == user_id,
                Call.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_provider_egress_id(
        self,
        provider_egress_id: str,
    ) -> RecordingEgressOperation | None:
        result = await self.session.execute(
            select(RecordingEgressOperation).where(
                RecordingEgressOperation.provider_egress_id == provider_egress_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_room_name(
        self,
        room_name: str,
    ) -> RecordingEgressOperation | None:
        matches = list(
            await self.session.scalars(
                select(RecordingEgressOperation)
                .where(RecordingEgressOperation.room_name == room_name)
                .limit(2)
            )
        )
        return matches[0] if len(matches) == 1 else None

    async def add(
        self,
        operation: RecordingEgressOperation,
    ) -> RecordingEgressOperation:
        self.session.add(operation)
        await self.session.flush()
        return operation

    async def delete(self, operation: RecordingEgressOperation) -> None:
        await self.session.delete(operation)
        await self.session.flush()

    async def observability_snapshot(
        self,
        now: datetime,
    ) -> RecordingOperationObservabilitySnapshot:
        rows = await self.session.execute(
            select(
                RecordingEgressOperation.start_state,
                func.count(),
            ).group_by(RecordingEgressOperation.start_state)
        )
        counts = {state: 0 for state in _START_STATE_ORDER}
        for state, count in rows:
            if state in counts:
                counts[state] = int(count)

        pending_stop = and_(
            RecordingEgressOperation.stop_requested_at.is_not(None),
            RecordingEgressOperation.start_state != "not_started",
            or_(
                RecordingEgressOperation.provider_terminal_at.is_(None),
                RecordingEgressOperation.last_error_code
                == "recording_identity_conflict",
            ),
        )
        unresolved = or_(
            RecordingEgressOperation.start_state.in_(
                ("prepared", "starting", "uncertain")
            ),
            RecordingEgressOperation.last_error_code.is_not(None),
            pending_stop,
            RecordingEgressOperation.delete_requested_at.is_not(None),
        )
        pending_deletion = and_(
            RecordingEgressOperation.delete_requested_at.is_not(None),
            RecordingEgressOperation.object_deleted_at.is_(None),
        )

        oldest_unresolved = await self.session.scalar(
            select(func.min(RecordingEgressOperation.created_at)).where(unresolved)
        )
        pending_stop_count, oldest_pending_stop = (
            await self.session.execute(
                select(
                    func.count(),
                    func.min(RecordingEgressOperation.stop_requested_at),
                ).where(pending_stop)
            )
        ).one()
        pending_deletion_count, oldest_pending_deletion = (
            await self.session.execute(
                select(
                    func.count(),
                    func.min(RecordingEgressOperation.delete_requested_at),
                ).where(pending_deletion)
            )
        ).one()

        return RecordingOperationObservabilitySnapshot(
            counts=counts,
            oldest_unresolved_age_seconds=_age_seconds(
                now=now,
                oldest=oldest_unresolved,
            ),
            pending_stop_count=int(pending_stop_count),
            oldest_pending_stop_age_seconds=_age_seconds(
                now=now,
                oldest=oldest_pending_stop,
            ),
            pending_deletion_count=int(pending_deletion_count),
            oldest_pending_deletion_age_seconds=_age_seconds(
                now=now,
                oldest=oldest_pending_deletion,
            ),
        )
