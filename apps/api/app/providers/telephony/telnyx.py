from app.providers.telephony.base import TelephonyProvider


class TelephonyTelnyx(TelephonyProvider):
    async def provision_number(self, *, country_code: str) -> dict:
        raise NotImplementedError("Telnyx provisioning is wired later in this branch")

    async def enable_number(self, *, provider_number_id: str) -> str:
        raise NotImplementedError("Telnyx enablement is wired later in this branch")

    async def disable_number(self, *, provider_number_id: str) -> str:
        raise NotImplementedError("Telnyx disablement is wired later in this branch")
