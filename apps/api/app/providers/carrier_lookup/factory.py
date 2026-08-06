from app.core.config import Settings
from app.core.observability import Observability
from app.core.provider_failures import ProviderFailure
from app.providers.carrier_lookup.base import CarrierLookupProvider
from app.providers.carrier_lookup.fake import FakeCarrierLookupProvider
from app.providers.carrier_lookup.telnyx import TelnyxCarrierLookupProvider


def build_carrier_lookup_provider(
    settings: Settings,
    *,
    observability: Observability,
) -> CarrierLookupProvider:
    if settings.carrier_lookup_mode == "telnyx":
        if not settings.telnyx_api_key:
            raise ProviderFailure(
                provider="telnyx",
                operation="lookup_carrier",
                disposition="terminal",
                error_class="authentication",
            )
        return TelnyxCarrierLookupProvider(
            api_key=settings.telnyx_api_key,
            observability=observability,
        )
    return FakeCarrierLookupProvider()
