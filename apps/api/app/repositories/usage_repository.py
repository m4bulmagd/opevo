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
