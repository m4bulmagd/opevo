from datetime import UTC, datetime

from app.providers.carrier_lookup.base import (
    CarrierCode,
    CarrierLookupResult,
)
from app.providers.telephony.telnyx import normalize_french_number


class FakeCarrierLookupProvider:
    def __init__(self, carrier: CarrierCode = "orange") -> None:
        self.carrier = carrier

    async def lookup(self, e164: str) -> CarrierLookupResult:
        normalized = normalize_french_number(e164)
        return CarrierLookupResult(
            normalized_number=normalized,
            country_code="FR",
            carrier_name=self.carrier.title(),
            normalized_carrier=self.carrier,
            number_type=(
                "mobile" if normalized.startswith(("+336", "+337")) else "fixed"
            ),
            looked_up_at=datetime.now(UTC),
        )
