import asyncio
from datetime import UTC, datetime
from typing import Any

import telnyx

from app.core.config import get_settings
from app.providers.carrier_lookup.base import (
    CarrierLookupError,
    CarrierLookupResult,
    normalize_carrier_name,
    normalize_number_type,
)
from app.providers.telephony.telnyx import normalize_french_number


class TelnyxCarrierLookupProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        number_lookup_resource=telnyx.NumberLookup,
    ) -> None:
        self.api_key = api_key or get_settings().telnyx_api_key
        self.number_lookup_resource = number_lookup_resource

    async def lookup(self, e164: str) -> CarrierLookupResult:
        normalized = normalize_french_number(e164)
        try:
            response = await asyncio.to_thread(
                self.number_lookup_resource.retrieve,
                normalized,
                api_key=self.api_key,
            )
        except (
            telnyx.error.APIConnectionError,
            telnyx.error.TimeoutError,
            telnyx.error.RateLimitError,
            telnyx.error.ServiceUnavailableError,
        ):
            raise CarrierLookupError("retryable") from None
        except (
            telnyx.error.AuthenticationError,
            telnyx.error.PermissionError,
            telnyx.error.InvalidRequestError,
            telnyx.error.InvalidParametersError,
            telnyx.error.ResourceNotFoundError,
        ):
            raise CarrierLookupError("terminal") from None

        payload = self._read(response, "data") or response
        result_number = self._read(payload, "phone_number")
        country_code = self._read(payload, "country_code")
        carrier = self._read(payload, "carrier")
        carrier_name = self._safe_carrier_name(self._read(carrier, "name"))
        number_type = normalize_number_type(self._read(carrier, "type"))
        try:
            result_number = normalize_french_number(result_number)
        except (TypeError, ValueError):
            raise CarrierLookupError("terminal") from None
        if country_code != "FR" or result_number != normalized:
            raise CarrierLookupError("terminal")
        return CarrierLookupResult(
            normalized_number=result_number,
            country_code="FR",
            carrier_name=carrier_name,
            normalized_carrier=normalize_carrier_name(carrier_name),
            number_type=number_type,
            looked_up_at=datetime.now(UTC),
        )

    @staticmethod
    def _read(value: Any, field: str) -> Any:
        if isinstance(value, dict):
            return value.get(field)
        return getattr(value, field, None)

    @staticmethod
    def _safe_carrier_name(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized[:100] or None
