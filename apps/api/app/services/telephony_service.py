from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.provider_failures import ProviderFailure, ProviderOperation
from app.models.phone_number import PhoneNumber
from app.providers.telephony.base import TelephonyProvider
from app.providers.telephony.factory import create_telephony_provider
from app.providers.telephony.telnyx import normalize_french_number
from app.repositories.phone_number_repository import PhoneNumberRepository


@dataclass(frozen=True)
class AcquiredPhoneNumber:
    e164: str
    provider_number_id: str
    provider_connection_name: str


class TelephonyService:
    def __init__(self, session: AsyncSession, provider: TelephonyProvider | None = None) -> None:
        self.session = session
        self.provider = (
            provider
            if provider is not None
            else create_telephony_provider(get_settings())
        )
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

        acquired = await self.acquire_number(
            country_code=country_code,
            operation_key=operation_key,
        )
        existing_number = await self.phone_number_repository.get_by_user_id(user_id)
        if existing_number is not None:
            return existing_number
        phone_number = await self.phone_number_repository.create(
            user_id=user_id,
            e164=acquired.e164,
            country_code=country_code,
            provider_number_id=acquired.provider_number_id,
            provider_connection_name=acquired.provider_connection_name,
            is_active=acquired.provider_connection_name == "app-active",
        )
        await self.session.flush()
        return phone_number

    async def acquire_number(
        self,
        *,
        country_code: str,
        operation_key: str | None = None,
    ) -> AcquiredPhoneNumber:
        if self.session.in_transaction():
            await self.session.rollback()
        provider_kwargs = {"country_code": country_code}
        if operation_key is not None:
            provider_kwargs["operation_key"] = operation_key
        provisioned = await self.provider.provision_number(**provider_kwargs)
        e164, provider_number_id, provider_connection_name = (
            self._validate_provisioned_result(provisioned, operation="provision_number")
        )
        return AcquiredPhoneNumber(
            e164=e164,
            provider_number_id=provider_number_id,
            provider_connection_name=provider_connection_name,
        )

    async def recover_acquired_number(
        self,
        *,
        country_code: str,
        operation_key: str,
    ) -> AcquiredPhoneNumber | None:
        if self.session.in_transaction():
            await self.session.rollback()
        recovered = await self.provider.recover_provisioned_number(
            country_code=country_code,
            operation_key=operation_key,
        )
        if recovered is None:
            return None
        e164, provider_number_id, provider_connection_name = (
            self._validate_provisioned_result(
                recovered, operation="recover_provisioned_number"
            )
        )
        return AcquiredPhoneNumber(
            e164=e164,
            provider_number_id=provider_number_id,
            provider_connection_name=provider_connection_name,
        )

    @staticmethod
    def _validate_provisioned_result(
        provisioned, *, operation: ProviderOperation
    ) -> tuple[str, str, str]:
        if not isinstance(provisioned, dict):
            raise TelephonyService._contract_failure(operation) from None
        e164 = provisioned.get("e164")
        provider_number_id = provisioned.get("provider_number_id")
        provider_connection_name = provisioned.get("provider_connection_name")
        if (
            not isinstance(e164, str)
            or not isinstance(provider_number_id, str)
            or not provider_number_id
            or provider_connection_name not in {"app-active", "app-disabled"}
        ):
            raise TelephonyService._contract_failure(operation) from None
        try:
            normalized_e164 = normalize_french_number(e164)
        except ValueError:
            raise TelephonyService._contract_failure(operation) from None
        return normalized_e164, provider_number_id, provider_connection_name

    async def enable_number(self, user_id):
        phone_number = await self.phone_number_repository.get_by_user_id(user_id)
        if phone_number is None:
            raise ValueError("Phone number not found")

        phone_number_id = phone_number.id
        provider_number_id = phone_number.provider_number_id
        if not isinstance(provider_number_id, str) or not provider_number_id:
            raise self._contract_failure("enable_number") from None
        await self.session.rollback()
        provider_connection_name = await self.provider.enable_number(
            provider_number_id=provider_number_id
        )
        self._validate_connection_name(
            provider_connection_name, expected="app-active", operation="enable_number"
        )
        phone_number = await self.phone_number_repository.get_by_id_for_update(
            phone_number_id
        )
        phone_number = self._revalidate_phone_number(
            phone_number,
            user_id=user_id,
            provider_number_id=provider_number_id,
            operation="enable_number",
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
            raise self._contract_failure("disable_number") from None
        await self.session.rollback()
        provider_connection_name = await self.provider.disable_number(
            provider_number_id=provider_number_id
        )
        self._validate_connection_name(
            provider_connection_name,
            expected="app-disabled",
            operation="disable_number",
        )
        phone_number = await self.phone_number_repository.get_by_id_for_update(
            phone_number_id
        )
        phone_number = self._revalidate_phone_number(
            phone_number,
            user_id=user_id,
            provider_number_id=provider_number_id,
            operation="disable_number",
        )
        phone_number.provider_connection_name = provider_connection_name
        phone_number.is_active = False
        await self.session.flush()
        return phone_number

    @staticmethod
    def _validate_connection_name(
        value, *, expected: str, operation: ProviderOperation
    ) -> None:
        if value != expected:
            raise TelephonyService._contract_failure(operation) from None

    @staticmethod
    def _revalidate_phone_number(
        phone_number: PhoneNumber | None,
        *,
        user_id,
        provider_number_id: str,
        operation: ProviderOperation,
    ) -> PhoneNumber:
        if (
            phone_number is None
            or phone_number.user_id != user_id
            or phone_number.provider_number_id != provider_number_id
        ):
            raise ProviderFailure(
                provider="telnyx",
                operation=operation,
                disposition="retryable",
                error_class="unavailable",
            ) from None
        return phone_number

    @staticmethod
    def _contract_failure(operation: ProviderOperation) -> ProviderFailure:
        return ProviderFailure(
            provider="telnyx",
            operation=operation,
            disposition="terminal",
            error_class="validation",
        )
