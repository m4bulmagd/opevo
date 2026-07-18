from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.outbox_event import OutboxEvent


@dataclass(frozen=True)
class OutboxSnapshot:
    counts: dict[str, int]
    oldest_unfinished_age_seconds: float


class OutboxRepository:
    CLAIM_LEASE = timedelta(minutes=5)

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_once(
        self,
        *,
        topic: str,
        aggregate_type: str,
        aggregate_id: UUID,
        idempotency_key: str,
        payload: dict,
        next_attempt_at: datetime,
    ) -> OutboxEvent:
        event_id = uuid4()
        values = {
            "id": event_id,
            "topic": topic,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "idempotency_key": idempotency_key,
            "payload": payload,
            "status": "pending",
            "attempt_count": 0,
            "next_attempt_at": next_attempt_at,
            "last_error_code": None,
            "routing_target_provider_number_id": None,
            "delivered_at": None,
        }
        dialect_name = self.session.bind.dialect.name
        insert = sqlite_insert if dialect_name == "sqlite" else postgresql_insert
        statement = (
            insert(OutboxEvent)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(OutboxEvent.id)
        )
        inserted_id = await self.session.scalar(statement)
        durable_id = inserted_id or await self.session.scalar(
            select(OutboxEvent.id).where(
                OutboxEvent.idempotency_key == idempotency_key
            )
        )
        if durable_id is None:
            raise RuntimeError("Outbox identity insert did not produce a durable row")
        event = await self.session.get(OutboxEvent, durable_id)
        if event is None:
            raise RuntimeError("Outbox identity row could not be loaded")
        return event

    async def count(self) -> int:
        value = await self.session.scalar(
            select(func.count()).select_from(OutboxEvent)
        )
        return int(value or 0)

    async def observability_snapshot(self, now: datetime) -> OutboxSnapshot:
        statuses = ("pending", "processing", "delivered", "failed")
        rows = await self.session.execute(
            select(OutboxEvent.status, func.count(OutboxEvent.id))
            .where(OutboxEvent.status.in_(statuses))
            .group_by(OutboxEvent.status)
        )
        counts = {status: 0 for status in statuses}
        for status, count in rows:
            counts[status] = int(count)

        oldest = await self.session.scalar(
            select(func.min(OutboxEvent.created_at)).where(
                OutboxEvent.status.in_(("pending", "processing"))
            )
        )
        if oldest is None:
            age = 0.0
        else:
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=UTC)
            if now.tzinfo is None:
                now = now.replace(tzinfo=UTC)
            age = max(0.0, (now - oldest).total_seconds())
        return OutboxSnapshot(
            counts=counts,
            oldest_unfinished_age_seconds=age,
        )

    async def claim_batch(
        self,
        *,
        limit: int,
        now: datetime,
    ) -> list[OutboxEvent]:
        if limit < 1:
            return []

        older = aliased(OutboxEvent)
        earlier_unfinished_for_aggregate = exists(
            select(older.id).where(
                older.aggregate_type == OutboxEvent.aggregate_type,
                older.aggregate_id == OutboxEvent.aggregate_id,
                older.status.in_(("pending", "processing")),
                or_(
                    older.created_at < OutboxEvent.created_at,
                    and_(
                        older.created_at == OutboxEvent.created_at,
                        older.id < OutboxEvent.id,
                    ),
                ),
            )
        )
        result = await self.session.execute(
            select(OutboxEvent)
            .where(
                or_(
                    (OutboxEvent.status == "pending")
                    & (OutboxEvent.next_attempt_at <= now),
                    (OutboxEvent.status == "processing")
                    & (OutboxEvent.next_attempt_at <= now),
                )
            )
            .where(~earlier_unfinished_for_aggregate)
            .order_by(
                OutboxEvent.next_attempt_at,
                OutboxEvent.created_at,
                OutboxEvent.id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        events = list(result.scalars())
        claim_deadline = now + self.CLAIM_LEASE
        for event in events:
            event.status = "processing"
            event.attempt_count += 1
            event.next_attempt_at = claim_deadline
        await self.session.flush()
        return events

    async def mark_delivered(
        self,
        *,
        event_id: UUID,
        attempt_count: int,
        delivered_at: datetime,
    ) -> OutboxEvent | None:
        event = await self._get_current_claim(
            event_id=event_id,
            attempt_count=attempt_count,
        )
        if event is None:
            return None
        event.status = "delivered"
        event.delivered_at = delivered_at
        event.last_error_code = None
        await self.session.flush()
        return event

    async def mark_failed_attempt(
        self,
        *,
        event_id: UUID,
        attempt_count: int,
        failed_at: datetime,
        error_code: str,
        retry_delays: tuple[timedelta, ...],
        terminal: bool = False,
        exhaustible: bool = True,
    ) -> OutboxEvent | None:
        event = await self._get_current_claim(
            event_id=event_id,
            attempt_count=attempt_count,
        )
        if event is None:
            return None

        event.last_error_code = error_code[:100]
        retries_exhausted = event.attempt_count > len(retry_delays)
        if terminal or (exhaustible and retries_exhausted):
            event.status = "failed"
            event.next_attempt_at = failed_at
        else:
            event.status = "pending"
            delay_index = min(event.attempt_count - 1, len(retry_delays) - 1)
            event.next_attempt_at = failed_at + retry_delays[delay_index]
        await self.session.flush()
        return event

    async def set_routing_target(
        self,
        *,
        event_id: UUID,
        attempt_count: int,
        provider_number_id: str,
    ) -> bool:
        event = await self._get_current_claim(
            event_id=event_id,
            attempt_count=attempt_count,
        )
        if event is None or event.topic not in {"phone.enable", "phone.disable"}:
            return False
        if event.routing_target_provider_number_id not in {
            None,
            provider_number_id,
        }:
            return False
        event.routing_target_provider_number_id = provider_number_id
        await self.session.flush()
        return True

    async def clear_routing_target(
        self,
        *,
        event_id: UUID,
        attempt_count: int,
        provider_number_id: str,
    ) -> bool:
        event = await self._get_current_claim(
            event_id=event_id,
            attempt_count=attempt_count,
        )
        if (
            event is None
            or event.routing_target_provider_number_id != provider_number_id
        ):
            return False
        event.routing_target_provider_number_id = None
        await self.session.flush()
        return True

    async def _get_current_claim(
        self,
        *,
        event_id: UUID,
        attempt_count: int,
    ) -> OutboxEvent | None:
        result = await self.session.execute(
            select(OutboxEvent)
            .where(
                OutboxEvent.id == event_id,
                OutboxEvent.status == "processing",
                OutboxEvent.attempt_count == attempt_count,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()
