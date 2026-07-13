import logging
from decimal import Decimal, InvalidOperation

import telnyx

from app.core.config import get_settings
from app.core.redaction import redact_phone
from app.providers.telephony.base import (
    TelephonyProvider,
    TelephonyProvisioningPending,
    TelephonyProvisioningReviewRequired,
)


MAX_SELECTION_ATTEMPTS = 3
MAX_TOTAL_COST_USD = Decimal("2.00")
ALLOWED_NUMBER_TYPES = ("national", "local")

logger = logging.getLogger(__name__)


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
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.telnyx_api_key
        self.active_connection_id = active_connection_id or settings.telnyx_active_connection_id
        self.disabled_connection_id = disabled_connection_id or settings.telnyx_disabled_connection_id
        self.ordering_enabled = settings.telnyx_ordering_enabled if ordering_enabled is None else ordering_enabled
        self.available_phone_number_resource = available_phone_number_resource
        self.phone_number_order_resource = phone_number_order_resource
        self.phone_number_resource = phone_number_resource

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
            existing_number = self._reconcile_existing_order(
                customer_reference=customer_reference,
                country_code=country_code,
            )
            if existing_number is not None:
                logger.info(
                    "Reconciled Telnyx number %s for provisioning (country_code=%s)",
                    redact_phone(existing_number),
                    country_code,
                )
                return self._activate_ordered_number(existing_number)

        inspected_candidates: list[dict] = []
        selected_candidate: dict | None = None

        for phone_number_type in ALLOWED_NUMBER_TYPES:
            remaining_attempts = MAX_SELECTION_ATTEMPTS - len(inspected_candidates)
            if remaining_attempts <= 0:
                break

            available_numbers = self.available_phone_number_resource.list(
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
                    "candidates": inspected_candidates,
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
                    "selected_candidate": selected_candidate,
                    "max_total_cost_usd": str(MAX_TOTAL_COST_USD),
                    "contact_support": False,
                    "manual_review_required": True,
                },
            )

        self.phone_number_order_resource.create(
            api_key=self.api_key,
            customer_reference=customer_reference,
            phone_numbers=[{"phone_number": selected_number}],
        )

        return self._activate_ordered_number(selected_number)

    def _reconcile_existing_order(
        self,
        *,
        customer_reference: str,
        country_code: str,
    ) -> str | None:
        response = self.phone_number_order_resource.list(
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
        if not isinstance(selected_number, str) or not selected_number.startswith("+"):
            self._raise_existing_order_review(
                reason="existing_order_requires_review",
                country_code=country_code,
                order_count=1,
            )
        return selected_number

    def _activate_ordered_number(self, selected_number: str) -> dict:
        phone_numbers = self.phone_number_resource.list(
            api_key=self.api_key,
            **{"filter[phone_number]": selected_number},
        )
        if not getattr(phone_numbers, "data", None):
            raise ValueError("Ordered Telnyx number was not retrievable")

        provider_number = phone_numbers.data[0]
        self.phone_number_resource.modify(
            provider_number.id,
            api_key=self.api_key,
            connection_id=self.disabled_connection_id,
        )

        return {
            "e164": selected_number,
            "provider_number_id": provider_number.id,
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
    ) -> None:
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

    @staticmethod
    def _extract_candidate_details(candidate, *, phone_number_type: str) -> dict:
        cost_information = getattr(candidate, "cost_information", None) or {}
        return {
            "e164": getattr(candidate, "phone_number", None),
            "phone_number_type": phone_number_type,
            "currency": cost_information.get("currency"),
            "upfront_cost": cost_information.get("upfront_cost"),
            "monthly_cost": cost_information.get("monthly_cost"),
        }

    @staticmethod
    def _is_candidate_affordable(candidate: dict) -> bool:
        if candidate.get("currency") != "USD":
            return False
        try:
            upfront_cost = Decimal(str(candidate.get("upfront_cost")))
            monthly_cost = Decimal(str(candidate.get("monthly_cost")))
        except (InvalidOperation, TypeError):
            return False
        return upfront_cost + monthly_cost <= MAX_TOTAL_COST_USD


def get_telephony_provider() -> TelephonyProvider:
    return TelephonyTelnyx()
