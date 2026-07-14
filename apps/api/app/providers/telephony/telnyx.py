import asyncio
import logging
from decimal import Decimal, InvalidOperation
from typing import NoReturn

import phonenumbers
import telnyx
from telnyx.http_client import new_default_http_client

from app.core.config import get_settings
from app.core.observability import (
    get_observability,
    instrument_provider,
)
from app.core.redaction import redact_phone
from app.providers.telephony.base import (
    TelephonyProvider,
    TelephonyProviderError,
    TelephonyProvisioningPending,
    TelephonyProvisioningReviewRequired,
)


MAX_SELECTION_ATTEMPTS = 3
MAX_TOTAL_COST_USD = Decimal("2.00")
ALLOWED_NUMBER_TYPES = ("national", "local")

logger = logging.getLogger(__name__)


def _configure_telnyx_network_policy() -> None:
    # Telnyx 2.1.6 retries every HTTP method, including NumberOrder.create
    # POSTs. Durable outbox retry plus customer_reference reconciliation owns
    # replay safety, so the SDK must never retry an order POST internally.
    telnyx.max_network_retries = 0
    if getattr(telnyx.default_http_client, "_timeout", None) != (5, 30):
        telnyx.default_http_client = new_default_http_client(timeout=(5, 30))


def normalize_french_number(value: str) -> str:
    try:
        parsed = phonenumbers.parse(value, "FR")
    except phonenumbers.NumberParseException:
        raise ValueError("A valid French phone number is required") from None
    if not phonenumbers.is_valid_number_for_region(parsed, "FR"):
        raise ValueError("A valid French phone number is required")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


class TelephonyTelnyx(TelephonyProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        active_connection_id: str | None = None,
        disabled_connection_id: str | None = None,
        ordering_enabled: bool | None = None,
        available_phone_number_resource=telnyx.AvailablePhoneNumber,
        phone_number_order_resource=telnyx.NumberOrder,
        phone_number_resource=telnyx.PhoneNumber,
        observability=None,
    ) -> None:
        _configure_telnyx_network_policy()
        settings = get_settings()
        self.api_key = api_key or settings.telnyx_api_key
        self.active_connection_id = active_connection_id or settings.telnyx_active_connection_id
        self.disabled_connection_id = disabled_connection_id or settings.telnyx_disabled_connection_id
        self.ordering_enabled = settings.telnyx_ordering_enabled if ordering_enabled is None else ordering_enabled
        self.available_phone_number_resource = available_phone_number_resource
        self.phone_number_order_resource = phone_number_order_resource
        self.phone_number_resource = phone_number_resource
        self.observability = observability or get_observability()

    @instrument_provider("telnyx", "provision_number")
    async def provision_number(
        self,
        *,
        country_code: str,
        operation_key: str | None = None,
    ) -> dict:
        logger.info(f"Attempting to provision number with Telnyx for country_code={country_code}")

        if self.ordering_enabled:
            customer_reference = self._require_operation_key(
                operation_key,
                country_code=country_code,
            )
            existing_number = await self._reconcile_existing_order(
                customer_reference=customer_reference,
                country_code=country_code,
            )
            if existing_number is not None:
                logger.info(
                    "Reconciled Telnyx number %s for provisioning (country_code=%s)",
                    redact_phone(existing_number),
                    country_code,
                )
                return await self._activate_ordered_number(existing_number)

        inspected_candidates: list[dict] = []
        selected_candidate: dict | None = None

        for phone_number_type in ALLOWED_NUMBER_TYPES:
            remaining_attempts = MAX_SELECTION_ATTEMPTS - len(inspected_candidates)
            if remaining_attempts <= 0:
                break

            available_numbers = await self._run_resource_call(
                self.available_phone_number_resource.list,
                api_key=self.api_key,
                **{
                    "filter[country_code]": country_code,
                    "filter[phone_number_type]": phone_number_type,
                    "filter[features]": ["voice"],
                    "filter[limit]": remaining_attempts,
                    "filter[reservable]": True,
                    "filter[exclude_held_numbers]": True,
                },
            )

            for candidate in getattr(available_numbers, "data", [])[:remaining_attempts]:
                candidate_details = self._extract_candidate_details(candidate, phone_number_type=phone_number_type)
                inspected_candidates.append(candidate_details)
                if self._is_candidate_affordable(candidate_details):
                    selected_candidate = candidate_details
                    break

            if selected_candidate is not None:
                break

        if selected_candidate is None:
            raise TelephonyProvisioningReviewRequired(
                reason="no_affordable_number",
                payload={
                    "event": "phone_number_provisioning_review_required",
                    "country_code": country_code,
                    "max_total_cost_usd": str(MAX_TOTAL_COST_USD),
                    "attempts": len(inspected_candidates),
                    "candidates": [
                        self._candidate_review_details(candidate)
                        for candidate in inspected_candidates
                    ],
                    "contact_support": True,
                },
            )

        selected_number = selected_candidate["e164"]
        logger.info(
            "Selected Telnyx number %s for provisioning (country_code=%s)",
            redact_phone(selected_number),
            country_code,
        )
        if not self.ordering_enabled:
            raise TelephonyProvisioningReviewRequired(
                reason="ordering_disabled",
                payload={
                    "event": "phone_number_provisioning_review_required",
                    "country_code": country_code,
                    "selected_candidate": self._candidate_review_details(
                        selected_candidate
                    ),
                    "max_total_cost_usd": str(MAX_TOTAL_COST_USD),
                    "contact_support": False,
                    "manual_review_required": True,
                },
            )

        await self._run_resource_call(
            self.phone_number_order_resource.create,
            api_key=self.api_key,
            customer_reference=customer_reference,
            phone_numbers=[{"phone_number": selected_number}],
        )
        ordered_number = await self._reconcile_existing_order(
            customer_reference=customer_reference,
            country_code=country_code,
        )
        if ordered_number is None:
            raise TelephonyProvisioningPending(reason="existing_order_pending")
        return await self._activate_ordered_number(ordered_number)

    async def _reconcile_existing_order(
        self,
        *,
        customer_reference: str,
        country_code: str,
    ) -> str | None:
        response = await self._run_resource_call(
            self.phone_number_order_resource.list,
            api_key=self.api_key,
            **{"filter[customer_reference]": customer_reference},
        )
        orders = list(getattr(response, "data", None) or [])
        if not orders:
            return None
        if len(orders) != 1:
            self._raise_existing_order_review(
                reason="existing_order_conflict",
                country_code=country_code,
                order_count=len(orders),
            )

        order = orders[0]
        if self._read_field(order, "customer_reference") != customer_reference:
            self._raise_existing_order_review(
                reason="existing_order_conflict",
                country_code=country_code,
                order_count=1,
            )

        ordered_numbers = list(self._read_field(order, "phone_numbers") or [])
        if len(ordered_numbers) != 1:
            self._raise_existing_order_review(
                reason="existing_order_requires_review",
                country_code=country_code,
                order_count=1,
            )
        ordered_number = ordered_numbers[0]
        status = self._read_field(ordered_number, "status")
        status = getattr(status, "value", status)
        if status in {"pending", "in_progress"}:
            raise TelephonyProvisioningPending(reason="existing_order_pending")
        if (
            status != "success"
            or self._read_field(order, "requirements_met") is not True
        ):
            self._raise_existing_order_review(
                reason="existing_order_requires_review",
                country_code=country_code,
                order_count=1,
            )
        selected_number = self._read_field(ordered_number, "phone_number")
        if not isinstance(selected_number, str):
            self._raise_existing_order_review(
                reason="existing_order_requires_review",
                country_code=country_code,
                order_count=1,
            )
        try:
            return normalize_french_number(selected_number)
        except ValueError:
            self._raise_existing_order_review(
                reason="existing_order_requires_review",
                country_code=country_code,
                order_count=1,
            )

    async def _activate_ordered_number(self, selected_number: str) -> dict:
        try:
            normalized_number = normalize_french_number(selected_number)
        except (TypeError, ValueError):
            raise TelephonyProviderError("provider_terminal") from None
        phone_numbers = await self._run_resource_call(
            self.phone_number_resource.list,
            api_key=self.api_key,
            **{"filter[phone_number]": normalized_number},
        )
        provider_numbers = list(getattr(phone_numbers, "data", None) or [])
        if not provider_numbers:
            raise TelephonyProvisioningPending(reason="existing_order_pending")
        if len(provider_numbers) != 1:
            raise TelephonyProviderError("provider_terminal") from None

        provider_number = provider_numbers[0]
        provider_number_id = self._read_field(provider_number, "id")
        if not isinstance(provider_number_id, str) or not provider_number_id:
            raise TelephonyProviderError("provider_terminal") from None
        response = await self._run_resource_call(
            self.phone_number_resource.modify,
            provider_number_id,
            api_key=self.api_key,
            connection_id=self.disabled_connection_id,
        )
        self._confirm_connection(response, self.disabled_connection_id)

        return {
            "e164": normalized_number,
            "provider_number_id": provider_number_id,
            "provider_connection_name": "app-disabled",
        }

    @staticmethod
    def _require_operation_key(
        operation_key: str | None,
        *,
        country_code: str,
    ) -> str:
        if isinstance(operation_key, str) and operation_key.strip():
            return operation_key.strip()
        raise TelephonyProvisioningReviewRequired(
            reason="missing_operation_key",
            payload={
                "event": "phone_number_provisioning_review_required",
                "country_code": country_code,
                "contact_support": True,
                "manual_review_required": True,
            },
        )

    @staticmethod
    def _raise_existing_order_review(
        *,
        reason: str,
        country_code: str,
        order_count: int,
    ) -> NoReturn:
        raise TelephonyProvisioningReviewRequired(
            reason=reason,
            payload={
                "event": "phone_number_provisioning_review_required",
                "country_code": country_code,
                "existing_order_count": order_count,
                "contact_support": True,
                "manual_review_required": True,
            },
        )

    @staticmethod
    def _read_field(value, field: str):
        if isinstance(value, dict):
            return value.get(field)
        return getattr(value, field, None)

    @instrument_provider("telnyx", "enable_number")
    async def enable_number(self, *, provider_number_id: str) -> str:
        response = await self._run_resource_call(
            self.phone_number_resource.modify,
            provider_number_id,
            api_key=self.api_key,
            connection_id=self.active_connection_id,
        )
        self._confirm_connection(response, self.active_connection_id)
        return "app-active"

    @instrument_provider("telnyx", "disable_number")
    async def disable_number(self, *, provider_number_id: str) -> str:
        response = await self._run_resource_call(
            self.phone_number_resource.modify,
            provider_number_id,
            api_key=self.api_key,
            connection_id=self.disabled_connection_id,
        )
        self._confirm_connection(response, self.disabled_connection_id)
        return "app-disabled"

    async def _run_resource_call(self, operation, *args, **kwargs):
        try:
            return await asyncio.to_thread(operation, *args, **kwargs)
        except telnyx.error.APIConnectionError as exc:
            category = (
                "provider_retryable"
                if getattr(exc, "should_retry", False) is True
                else "provider_terminal"
            )
            raise TelephonyProviderError(
                category,
                error_class="unavailable",
            ) from None
        except telnyx.error.TimeoutError:
            raise TelephonyProviderError(
                "provider_retryable",
                error_class="timeout",
            ) from None
        except telnyx.error.RateLimitError:
            raise TelephonyProviderError(
                "provider_retryable",
                error_class="rate_limited",
            ) from None
        except telnyx.error.ServiceUnavailableError:
            raise TelephonyProviderError(
                "provider_retryable",
                error_class="unavailable",
            ) from None
        except (
            telnyx.error.AuthenticationError,
            telnyx.error.PermissionError,
        ):
            raise TelephonyProviderError(
                "provider_terminal",
                error_class="authentication",
            ) from None
        except (
            telnyx.error.InvalidRequestError,
            telnyx.error.ResourceNotFoundError,
            telnyx.error.MethodNotSupportedError,
            telnyx.error.UnsupportedMediaTypeError,
            telnyx.error.InvalidParametersError,
        ):
            raise TelephonyProviderError(
                "provider_terminal",
                error_class="validation",
            ) from None
        except telnyx.error.APIError as exc:
            category, error_class = self._telnyx_http_error_details(
                exc.http_status
            )
            raise TelephonyProviderError(
                category,
                error_class=error_class,
            ) from None
        except telnyx.error.TelnyxError as exc:
            category, error_class = self._telnyx_http_error_details(
                exc.http_status
            )
            raise TelephonyProviderError(
                category,
                error_class=error_class,
            ) from None

    @staticmethod
    def _telnyx_http_error_details(status: int | None) -> tuple[str, str]:
        if status == 429:
            return "provider_retryable", "rate_limited"
        if status in {408, 504}:
            return "provider_retryable", "timeout"
        if status is not None and status >= 500:
            return "provider_retryable", "unavailable"
        if status in {401, 403}:
            return "provider_terminal", "authentication"
        if status == 409:
            return "provider_terminal", "conflict"
        if status in {400, 404, 405, 415, 422}:
            return "provider_terminal", "validation"
        return "provider_terminal", "unknown"

    @staticmethod
    def _confirm_connection(response, requested_connection_id: str | None) -> None:
        if requested_connection_id is None:
            raise TelephonyProviderError("provider_terminal")
        connection_id = TelephonyTelnyx._read_field(response, "connection_id")
        if connection_id != requested_connection_id:
            raise TelephonyProviderError("provider_retryable")

    @staticmethod
    def _extract_candidate_details(candidate, *, phone_number_type: str) -> dict:
        cost_information = getattr(candidate, "cost_information", None) or {}
        try:
            e164 = normalize_french_number(getattr(candidate, "phone_number", ""))
        except ValueError:
            e164 = None
        return {
            "e164": e164,
            "phone_number_type": phone_number_type,
            "currency": cost_information.get("currency"),
            "upfront_cost": cost_information.get("upfront_cost"),
            "monthly_cost": cost_information.get("monthly_cost"),
        }

    @staticmethod
    def _is_candidate_affordable(candidate: dict) -> bool:
        if not candidate.get("e164") or candidate.get("currency") != "USD":
            return False
        try:
            upfront_cost = Decimal(str(candidate.get("upfront_cost")))
            monthly_cost = Decimal(str(candidate.get("monthly_cost")))
        except (InvalidOperation, TypeError):
            return False
        return upfront_cost + monthly_cost <= MAX_TOTAL_COST_USD

    @staticmethod
    def _candidate_review_details(candidate: dict) -> dict:
        return {
            key: candidate.get(key)
            for key in (
                "phone_number_type",
                "currency",
                "upfront_cost",
                "monthly_cost",
            )
        }


def get_telephony_provider() -> TelephonyProvider:
    return TelephonyTelnyx()
