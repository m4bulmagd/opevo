import telnyx

from app.core.config import get_settings
from app.providers.telephony.base import TelephonyProvider


class TelephonyTelnyx(TelephonyProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        active_connection_id: str | None = None,
        disabled_connection_id: str | None = None,
        available_phone_number_resource=telnyx.AvailablePhoneNumber,
        phone_number_order_resource=telnyx.PhoneNumberOrder,
        phone_number_resource=telnyx.PhoneNumber,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.telnyx_api_key
        self.active_connection_id = active_connection_id or settings.telnyx_active_connection_id
        self.disabled_connection_id = disabled_connection_id or settings.telnyx_disabled_connection_id
        self.available_phone_number_resource = available_phone_number_resource
        self.phone_number_order_resource = phone_number_order_resource
        self.phone_number_resource = phone_number_resource

    async def provision_number(self, *, country_code: str) -> dict:
        available_numbers = self.available_phone_number_resource.list(
            api_key=self.api_key,
            **{
                "filter[country_code]": country_code,
                "filter[features]": ["voice"],
                "page[size]": 1,
            },
        )
        if not getattr(available_numbers, "data", None):
            raise ValueError(f"No available Telnyx numbers for {country_code}")

        selected_number = available_numbers.data[0].phone_number
        self.phone_number_order_resource.create(
            api_key=self.api_key,
            phone_numbers=[selected_number],
        )

        phone_numbers = self.phone_number_resource.list(
            api_key=self.api_key,
            **{"filter[phone_number]": selected_number},
        )
        if not getattr(phone_numbers, "data", None):
            raise ValueError(f"Ordered Telnyx number {selected_number} was not retrievable")

        provider_number = phone_numbers.data[0]
        self.phone_number_resource.modify(
            provider_number.id,
            api_key=self.api_key,
            connection_id=self.active_connection_id,
        )

        return {
            "e164": selected_number,
            "provider_number_id": provider_number.id,
            "provider_connection_name": "app-active",
        }

    async def enable_number(self, *, provider_number_id: str) -> str:
        self.phone_number_resource.modify(
            provider_number_id,
            api_key=self.api_key,
            connection_id=self.active_connection_id,
        )
        return "app-active"

    async def disable_number(self, *, provider_number_id: str) -> str:
        self.phone_number_resource.modify(
            provider_number_id,
            api_key=self.api_key,
            connection_id=self.disabled_connection_id,
        )
        return "app-disabled"


def get_telephony_provider() -> TelephonyProvider:
    return TelephonyTelnyx()
