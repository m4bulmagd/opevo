from app.core.config import Settings, get_settings
from app.providers.telephony.base import TelephonyProvider
from app.providers.telephony.fake import FakeTelephonyProvider
from app.providers.telephony.telnyx import TelephonyTelnyx


def create_telephony_provider(
    settings: Settings | None = None,
) -> TelephonyProvider:
    selected_settings = settings or get_settings()
    if selected_settings.telephony_mode == "telnyx":
        return TelephonyTelnyx(
            api_key=selected_settings.telnyx_api_key,
            active_connection_id=selected_settings.telnyx_active_connection_id,
            disabled_connection_id=selected_settings.telnyx_disabled_connection_id,
            ordering_enabled=selected_settings.telnyx_ordering_enabled,
        )
    return FakeTelephonyProvider()

