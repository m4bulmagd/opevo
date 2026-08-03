import asyncio
import inspect
from datetime import UTC, datetime
from typing import Any

import telnyx

from app.core.config import get_settings
from app.core.provider_failures import (
    ProviderFailure,
)
from app.providers.carrier_lookup.base import (
    CarrierLookupResult,
    normalize_carrier_name,
    normalize_number_type,
)
from app.providers.telephony.telnyx import normalize_french_number
from app.providers.telnyx_failures import classify_telnyx_exception


_MISSING = object()


class _MalformedCarrierLookupResponse(ValueError):
    pass


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
        except Exception as exc:
            failure = classify_telnyx_exception(
                exc,
                operation="lookup_carrier",
            )
            if failure is None:
                raise
            raise failure from exc

        try:
            payload_data = self._read(response, "data")
            payload = response if payload_data is None else payload_data
            result_number = self._required_french_number(
                self._read(payload, "phone_number"),
                field="phone_number",
            )
            country_code = self._required_string(
                self._read(payload, "country_code"),
                field="country_code",
            )
            carrier = self._carrier_details(self._read(payload, "carrier"))
            carrier_name = self._optional_string(
                self._read(carrier, "name"),
                field="carrier.name",
            )
            raw_number_type = self._optional_string(
                self._read(carrier, "type"),
                field="carrier.type",
            )
            number_type = normalize_number_type(raw_number_type)
        except _MalformedCarrierLookupResponse as exc:
            raise ProviderFailure(
                provider="telnyx",
                operation="lookup_carrier",
                disposition="terminal",
                error_class="validation",
            ) from exc
        if country_code != "FR" or result_number != normalized:
            raise ProviderFailure(
                provider="telnyx",
                operation="lookup_carrier",
                disposition="terminal",
                error_class="validation",
            ) from None
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
        if value is None:
            return None
        if inspect.getattr_static(value, field, _MISSING) is _MISSING:
            return None
        return getattr(value, field)

    @staticmethod
    def _optional_string(value: Any, *, field: str) -> str | None:
        if value is None:
            return None
        if type(value) is not str:
            raise _MalformedCarrierLookupResponse(f"Malformed {field}")
        normalized = value.strip()
        return normalized[:100] or None

    @classmethod
    def _required_string(cls, value: Any, *, field: str) -> str:
        normalized = cls._optional_string(value, field=field)
        if normalized is None:
            raise _MalformedCarrierLookupResponse(f"Missing {field}")
        return normalized

    @classmethod
    def _required_french_number(cls, value: Any, *, field: str) -> str:
        number = cls._required_string(value, field=field)
        try:
            return normalize_french_number(number)
        except ValueError as exc:
            raise _MalformedCarrierLookupResponse(f"Malformed {field}") from exc

    @staticmethod
    def _carrier_details(value: Any) -> Any:
        if value is None or isinstance(value, dict):
            return value
        if (
            inspect.getattr_static(value, "name", _MISSING) is not _MISSING
            or inspect.getattr_static(value, "type", _MISSING) is not _MISSING
        ):
            return value
        raise _MalformedCarrierLookupResponse("Malformed carrier")
