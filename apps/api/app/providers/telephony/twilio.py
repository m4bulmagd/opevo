from app.providers.telephony.base import TelephonyProvider


class TelephonyTwilio(TelephonyProvider):
    async def provision_number(
        self,
        *,
        country_code: str,
        operation_key: str | None = None,
    ) -> dict:
        raise NotImplementedError

    async def enable_number(self, *, provider_number_id: str) -> str:
        raise NotImplementedError

    async def disable_number(self, *, provider_number_id: str) -> str:
        raise NotImplementedError

    async def release_number(self, *, provider_number_id: str) -> None:
        raise NotImplementedError
