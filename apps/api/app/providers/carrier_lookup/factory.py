from app.core.config import Settings, get_settings
from app.providers.carrier_lookup.base import CarrierLookupProvider
from app.providers.carrier_lookup.fake import FakeCarrierLookupProvider
from app.providers.carrier_lookup.telnyx import TelnyxCarrierLookupProvider


def build_carrier_lookup_provider(
    *,
    settings: Settings | None = None,
) -> CarrierLookupProvider:
    selected_settings = settings or get_settings()
    if selected_settings.carrier_lookup_mode == "telnyx":
        return TelnyxCarrierLookupProvider(api_key=selected_settings.telnyx_api_key)
    return FakeCarrierLookupProvider()
