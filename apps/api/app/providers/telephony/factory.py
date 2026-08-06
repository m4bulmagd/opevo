from app.core.config import Settings
from app.core.observability import Observability
from app.core.provider_failures import ProviderFailure
from app.providers.telephony.base import TelephonyProvider
from app.providers.telephony.fake import FakeTelephonyProvider
from app.providers.telephony.telnyx import TelephonyTelnyx


def create_telephony_provider(
    settings: Settings,
    *,
    observability: Observability,
) -> TelephonyProvider:
    if settings.telephony_mode == "telnyx":
        if not settings.telnyx_api_key:
            raise ProviderFailure(
                provider="telnyx",
                operation="provision_number",
                disposition="terminal",
                error_class="authentication",
            )
        return TelephonyTelnyx(
            api_key=settings.telnyx_api_key,
            active_connection_id=settings.telnyx_active_connection_id,
            disabled_connection_id=settings.telnyx_disabled_connection_id,
            ordering_enabled=settings.telnyx_ordering_enabled,
            observability=observability,
        )
    return FakeTelephonyProvider()
