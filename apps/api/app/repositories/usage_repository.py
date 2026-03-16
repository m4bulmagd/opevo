from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.usage_ledger import UsageLedger


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
    ) -> UsageLedger:
        ledger = UsageLedger(
            user_id=user_id,
            call_id=call_id,
            event_type=event_type,
            minutes_delta=minutes_delta,
            balance_after=balance_after,
        )
        self.session.add(ledger)
        await self.session.flush()
        return ledger

    async def get_current_balance(self, *, user_id) -> int:
        statement = (
            select(UsageLedger.balance_after)
            .where(UsageLedger.user_id == user_id)
            .where(UsageLedger.balance_after.is_not(None))
            .order_by(UsageLedger.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(statement)
        balance = result.scalar_one_or_none()
        return int(balance or 0)
