from sqlalchemy.ext.asyncio import AsyncSession


class SubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
