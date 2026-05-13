import logging
from decimal import Decimal, InvalidOperation

import telnyx

from app.core.config import get_settings
from app.providers.telephony.base import TelephonyProvider, TelephonyProvisioningReviewRequired


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

    async def provision_number(self, *, country_code: str) -> dict:
        logger.info(f"Attempting to provision number with Telnyx for country_code={country_code}")
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
        logger.info("Selected Telnyx number %s for provisioning (country_code=%s)", selected_number, country_code)
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
            phone_numbers=[{"phone_number": selected_number}],
        )

        phone_numbers = self.phone_number_resource.list(
            api_key=self.api_key,
            **{"filter[phone_number]": selected_number},
        )
        if not getattr(phone_numbers, "data", None):
            raise ValueError(f"Ordered Telnyx number {selected_number} was not retrievable")

        provider_number = phone_numbers.data[0]
        self.phone_number_resource.modify(
            provider_number.id,
            api_key=self.api_key,
            connection_id=self.active_connection_id,
        )

        return {
            "e164": selected_number,
            "provider_number_id": provider_number.id,
            "provider_connection_name": "app-active",
        }

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
