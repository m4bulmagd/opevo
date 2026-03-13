from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.phone_number import PhoneNumber


def normalize_phone_number(raw_number: str) -> str:
    digits = "".join(ch for ch in raw_number if ch.isdigit())
    return f"+{digits}" if digits else raw_number


class PhoneNumberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_e164(self, e164: str) -> PhoneNumber | None:
        result = await self.session.execute(select(PhoneNumber).where(PhoneNumber.e164 == e164))
        return result.scalar_one_or_none()

    async def get_by_any_format(self, raw_number: str) -> PhoneNumber | None:
        normalized = normalize_phone_number(raw_number)
        if normalized == raw_number:
            return await self.get_by_e164(normalized)

        result = await self.session.execute(select(PhoneNumber).where(PhoneNumber.e164 == normalized))
        return result.scalar_one_or_none()

    async def get_by_user_id(self, user_id) -> PhoneNumber | None:
        result = await self.session.execute(
            select(PhoneNumber).where(PhoneNumber.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        user_id,
        e164: str,
        country_code: str,
        provider_number_id: str,
        provider_connection_name: str,
        is_active: bool,
    ) -> PhoneNumber:
        phone_number = PhoneNumber(
            user_id=user_id,
            e164=e164,
            country_code=country_code,
            provider="telnyx",
            provider_number_id=provider_number_id,
            provider_connection_name=provider_connection_name,
            is_active=is_active,
        )
        self.session.add(phone_number)
        await self.session.flush()
        return phone_number
