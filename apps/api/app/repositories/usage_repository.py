from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.usage_ledger import UsageLedger
from app.models.user import User


INVOICE_GRANT_EVENT_TYPES = (
    "subscription_activated",
    "invoice_paid_reset",
)


class UsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id,
        event_type: str,
        minutes_delta: int,
        balance_after: int | None = None,
        call_id=None,
        source_id: str | None = None,
        created_at: datetime | None = None,
    ) -> UsageLedger:
        values = dict(
            user_id=user_id,
            call_id=call_id,
            event_type=event_type,
            source_id=source_id,
            minutes_delta=minutes_delta,
            balance_after=balance_after,
        )
        if created_at is not None:
            values["created_at"] = created_at
        ledger = UsageLedger(**values)
        self.session.add(ledger)
        await self.session.flush()
        return ledger

    async def get_current_balance(self, *, user_id) -> int:
        statement = (
            select(UsageLedger.balance_after)
            .where(UsageLedger.user_id == user_id)
            .where(UsageLedger.balance_after.is_not(None))
            .order_by(UsageLedger.created_at.desc(), UsageLedger.id.desc())
            .limit(1)
        )
        result = await self.session.execute(statement)
        balance = result.scalar_one_or_none()
        return int(balance or 0)

    async def lock_user(self, *, user_id) -> User | None:
        result = await self.session.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def lock_invoice_source(self, *, source_id: str) -> None:
        if self.session.get_bind().dialect.name != "postgresql":
            return
        await self.session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(:source_id, 0)"
                ")"
            ),
            {"source_id": source_id},
        )

    async def next_created_at(self, *, user_id) -> datetime:
        latest = await self.session.scalar(
            select(UsageLedger.created_at)
            .where(UsageLedger.user_id == user_id)
            .order_by(UsageLedger.created_at.desc(), UsageLedger.id.desc())
            .limit(1)
        )
        if self.session.get_bind().dialect.name == "postgresql":
            current = await self.session.scalar(select(func.clock_timestamp()))
        else:
            current = datetime.now(UTC)
        if current is None:
            current = datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        if latest is None:
            return current
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        return max(current, latest + timedelta(microseconds=1))

    async def get_call_debit(self, *, call_id) -> UsageLedger | None:
        result = await self.session.execute(
            select(UsageLedger).where(
                UsageLedger.call_id == call_id,
                UsageLedger.event_type == "call_completed",
            )
        )
        return result.scalar_one_or_none()

    async def get_invoice_grant_by_source_id(
        self,
        *,
        source_id: str,
    ) -> UsageLedger | None:
        result = await self.session.execute(
            select(UsageLedger)
            .where(
                UsageLedger.source_id == source_id,
                UsageLedger.event_type.in_(INVOICE_GRANT_EVENT_TYPES),
            )
            .order_by(UsageLedger.created_at.desc(), UsageLedger.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def has_invoice_grant(self, *, user_id) -> bool:
        result = await self.session.execute(
            select(UsageLedger.id)
            .where(
                UsageLedger.user_id == user_id,
                UsageLedger.event_type.in_(INVOICE_GRANT_EVENT_TYPES),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def list_recent_by_user_id(self, *, user_id, limit: int) -> list[UsageLedger]:
        statement = (
            select(UsageLedger)
            .where(UsageLedger.user_id == user_id)
            .order_by(UsageLedger.created_at.desc(), UsageLedger.id.desc())
            .limit(limit)
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())
