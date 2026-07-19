from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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

    async def get_by_room_name(
        self,
        room_name: str,
    ) -> RecordingEgressOperation | None:
        result = await self.session.execute(
            select(RecordingEgressOperation).where(
                RecordingEgressOperation.room_name == room_name
            )
        )
        return result.scalar_one_or_none()

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
    ) -> RecordingOperationObservabilitySnapshot:
        rows = await self.session.execute(
            select(
                RecordingEgressOperation.start_state,
                func.count(RecordingEgressOperation.id),
            ).group_by(RecordingEgressOperation.start_state)
        )
        counts = {state: 0 for state in _START_STATE_ORDER}
        for state, count in rows:
            if state in counts:
                counts[state] = int(count)
        return RecordingOperationObservabilitySnapshot(counts=counts)
