from sqlalchemy.ext.asyncio import AsyncSession

from app.models.phone_number import PhoneNumber
from app.providers.telephony.base import TelephonyProvider, TelephonyProviderError
from app.providers.telephony.telnyx import TelephonyTelnyx, normalize_french_number
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

        # Repository reads autobegin a transaction. End it before any provider
        # await so no business transaction spans external I/O.
        await self.session.rollback()

        provider_kwargs = {"country_code": country_code}
        if operation_key is not None:
            provider_kwargs["operation_key"] = operation_key
        provisioned = await self.provider.provision_number(**provider_kwargs)
        e164, provider_number_id, provider_connection_name = (
            self._validate_provisioned_result(provisioned)
        )
        existing_number = await self.phone_number_repository.get_by_user_id(user_id)
        if existing_number is not None:
            return existing_number
        phone_number = await self.phone_number_repository.create(
            user_id=user_id,
            e164=e164,
            country_code=country_code,
            provider_number_id=provider_number_id,
            provider_connection_name=provider_connection_name,
            is_active=provider_connection_name == "app-active",
        )
        await self.session.flush()
        return phone_number

    @staticmethod
    def _validate_provisioned_result(provisioned) -> tuple[str, str, str]:
        if not isinstance(provisioned, dict):
            raise TelephonyProviderError("provider_terminal") from None
        e164 = provisioned.get("e164")
        provider_number_id = provisioned.get("provider_number_id")
        provider_connection_name = provisioned.get("provider_connection_name")
        if (
            not isinstance(e164, str)
            or not isinstance(provider_number_id, str)
            or not provider_number_id
            or provider_connection_name not in {"app-active", "app-disabled"}
        ):
            raise TelephonyProviderError("provider_terminal") from None
        try:
            normalized_e164 = normalize_french_number(e164)
        except ValueError:
            raise TelephonyProviderError("provider_terminal") from None
        return normalized_e164, provider_number_id, provider_connection_name

    async def enable_number(self, user_id):
        phone_number = await self.phone_number_repository.get_by_user_id(user_id)
        if phone_number is None:
            raise ValueError("Phone number not found")

        phone_number_id = phone_number.id
        provider_number_id = phone_number.provider_number_id
        if not isinstance(provider_number_id, str) or not provider_number_id:
            raise TelephonyProviderError("provider_terminal") from None
        await self.session.rollback()
        provider_connection_name = await self.provider.enable_number(
            provider_number_id=provider_number_id
        )
        self._validate_connection_name(provider_connection_name, expected="app-active")
        phone_number = await self.phone_number_repository.get_by_id_for_update(
            phone_number_id
        )
        phone_number = self._revalidate_phone_number(
            phone_number,
            user_id=user_id,
            provider_number_id=provider_number_id,
        )
        phone_number.provider_connection_name = provider_connection_name
        phone_number.is_active = True
        await self.session.flush()
        return phone_number

    async def disable_number(self, user_id):
        phone_number = await self.phone_number_repository.get_by_user_id(user_id)
        if phone_number is None:
            raise ValueError("Phone number not found")

        phone_number_id = phone_number.id
        provider_number_id = phone_number.provider_number_id
        if not isinstance(provider_number_id, str) or not provider_number_id:
            raise TelephonyProviderError("provider_terminal") from None
        await self.session.rollback()
        provider_connection_name = await self.provider.disable_number(
            provider_number_id=provider_number_id
        )
        self._validate_connection_name(provider_connection_name, expected="app-disabled")
        phone_number = await self.phone_number_repository.get_by_id_for_update(
            phone_number_id
        )
        phone_number = self._revalidate_phone_number(
            phone_number,
            user_id=user_id,
            provider_number_id=provider_number_id,
        )
        phone_number.provider_connection_name = provider_connection_name
        phone_number.is_active = False
        await self.session.flush()
        return phone_number

    @staticmethod
    def _validate_connection_name(value, *, expected: str) -> None:
        if value != expected:
            raise TelephonyProviderError("provider_terminal") from None

    @staticmethod
    def _revalidate_phone_number(
        phone_number: PhoneNumber | None,
        *,
        user_id,
        provider_number_id: str,
    ) -> PhoneNumber:
        if (
            phone_number is None
            or phone_number.user_id != user_id
            or phone_number.provider_number_id != provider_number_id
        ):
            raise TelephonyProviderError("provider_retryable") from None
        return phone_number
