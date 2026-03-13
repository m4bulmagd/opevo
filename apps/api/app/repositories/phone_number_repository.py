from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.phone_number import PhoneNumber


class PhoneNumberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_e164(self, e164: str) -> PhoneNumber | None:
        result = await self.session.execute(select(PhoneNumber).where(PhoneNumber.e164 == e164))
        return result.scalar_one_or_none()
