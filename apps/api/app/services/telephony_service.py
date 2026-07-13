from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.telephony.base import TelephonyProvider
from app.providers.telephony.telnyx import TelephonyTelnyx
from app.repositories.phone_number_repository import PhoneNumberRepository


class TelephonyService:
    def __init__(self, session: AsyncSession, provider: TelephonyProvider | None = None) -> None:
        self.session = session
        self.provider = provider or TelephonyTelnyx()
        self.phone_number_repository = PhoneNumberRepository(session)

    async def provision_number(
        self,
        user_id,
        *,
        country_code: str,
        operation_key: str | None = None,
    ):
        existing_number = await self.phone_number_repository.get_by_user_id(user_id)
        if existing_number is not None:
            return existing_number

        provider_kwargs = {"country_code": country_code}
        if operation_key is not None:
            provider_kwargs["operation_key"] = operation_key
        provisioned = await self.provider.provision_number(**provider_kwargs)
        phone_number = await self.phone_number_repository.create(
            user_id=user_id,
            e164=provisioned["e164"],
            country_code=country_code,
            provider_number_id=provisioned["provider_number_id"],
            provider_connection_name=provisioned["provider_connection_name"],
            is_active=provisioned["provider_connection_name"] == "app-active",
        )
        await self.session.flush()
        return phone_number

    async def enable_number(self, user_id):
        phone_number = await self.phone_number_repository.get_by_user_id(user_id)
        if phone_number is None:
            raise ValueError("Phone number not found")

        phone_number.provider_connection_name = await self.provider.enable_number(
            provider_number_id=phone_number.provider_number_id
        )
        phone_number.is_active = phone_number.provider_connection_name == "app-active"
        await self.session.flush()
        return phone_number

    async def disable_number(self, user_id):
        phone_number = await self.phone_number_repository.get_by_user_id(user_id)
        if phone_number is None:
            raise ValueError("Phone number not found")

        phone_number.provider_connection_name = await self.provider.disable_number(
            provider_number_id=phone_number.provider_number_id
        )
        phone_number.is_active = False
        await self.session.flush()
        return phone_number
