import asyncio
from datetime import UTC, datetime
from typing import Any

import telnyx

from app.core.config import get_settings
from app.core.provider_failures import (
    ProviderFailure,
    ProviderFailureClass,
    provider_failure_from_http_status,
)
from app.providers.carrier_lookup.base import (
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
        except telnyx.error.APIConnectionError as exc:
            raise ProviderFailure(
                provider="telnyx",
                operation="lookup_carrier",
                disposition=(
                    "retryable" if getattr(exc, "should_retry", False) is True else "terminal"
                ),
                error_class="unavailable",
            ) from exc
        except telnyx.error.TimeoutError as exc:
            raise ProviderFailure(
                provider="telnyx",
                operation="lookup_carrier",
                disposition="retryable",
                error_class="timeout",
            ) from exc
        except telnyx.error.RateLimitError as exc:
            raise ProviderFailure(
                provider="telnyx",
                operation="lookup_carrier",
                disposition="retryable",
                error_class="rate_limited",
            ) from exc
        except telnyx.error.ServiceUnavailableError as exc:
            raise ProviderFailure(
                provider="telnyx",
                operation="lookup_carrier",
                disposition="retryable",
                error_class="unavailable",
            ) from exc
        except (
            telnyx.error.AuthenticationError,
            telnyx.error.PermissionError,
            telnyx.error.InvalidRequestError,
            telnyx.error.InvalidParametersError,
            telnyx.error.ResourceNotFoundError,
        ) as exc:
            error_class: ProviderFailureClass = (
                "not_found"
                if isinstance(exc, telnyx.error.ResourceNotFoundError)
                else "authentication"
                if isinstance(
                    exc,
                    (telnyx.error.AuthenticationError, telnyx.error.PermissionError),
                )
                else "validation"
            )
            raise ProviderFailure(
                provider="telnyx",
                operation="lookup_carrier",
                disposition="terminal",
                error_class=error_class,
            ) from exc
        except telnyx.error.APIError as exc:
            raise provider_failure_from_http_status(
                provider="telnyx",
                operation="lookup_carrier",
                status=exc.http_status,
            ) from exc
        except telnyx.error.TelnyxError as exc:
            raise ProviderFailure(
                provider="telnyx",
                operation="lookup_carrier",
                disposition="terminal",
                error_class="unknown",
            ) from exc

        try:
            payload_data = self._read(response, "data")
            payload = response if payload_data is None else payload_data
            result_number = self._read(payload, "phone_number")
            country_code = self._required_string(
                self._read(payload, "country_code"),
                field="country_code",
            )
            carrier = self._carrier_details(self._read(payload, "carrier"))
            carrier_name = self._optional_string(
                self._read(carrier, "name"),
                field="carrier.name",
            )
            number_type = normalize_number_type(self._read(carrier, "type"))
            result_number = normalize_french_number(result_number)
        except (TypeError, ValueError) as exc:
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
        return getattr(value, field, None)

    @staticmethod
    def _optional_string(value: Any, *, field: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Malformed {field}")
        normalized = value.strip()
        return normalized[:100] or None

    @classmethod
    def _required_string(cls, value: Any, *, field: str) -> str:
        normalized = cls._optional_string(value, field=field)
        if normalized is None:
            raise ValueError(f"Missing {field}")
        return normalized

    @staticmethod
    def _carrier_details(value: Any) -> Any:
        if value is None or isinstance(value, dict):
            return value
        if hasattr(value, "name") or hasattr(value, "type"):
            return value
        raise ValueError("Malformed carrier")
