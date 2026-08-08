import re

import phonenumbers
from phonenumbers.phonenumberutil import NumberParseException

from app.providers.carrier_lookup.base import CarrierCode
from app.schemas.forwarding import (
    ForwardingCondition,
    ForwardingGuide,
    ForwardingStep,
)


CATALOG_VERSION = "fr-forwarding-2026-07-17"
ORANGE_VOICE_PORTAL_URL = "https://assistance.orange.fr/nid/38921"
ORANGE_CONDITIONAL_URL = "https://assistance.orange.fr/oid/20084"
SFR_FIXED_OPTIONS_URL = (
    "https://assistance.sfr.fr/internet-tel-fixe/tel-fixe/"
    "activer-desactiver-options.html"
)
FREE_BUSY_URL = "https://assistance.free.fr/articles/551"
FREE_UNANSWERED_URL = "https://assistance.free.fr/articles/552"
FREE_MOBILE_URL = "https://assistance.free.fr/articles/1755"
BOUYGUES_TARIFF_GUIDE_URL = (
    "https://www.bouyguestelecom.fr/static/cms/tarifs/"
    "20260330_RCBT_Guide-des-tarifs_BD.pdf"
)

WARNING = (
    "Your carrier may charge forwarded-call minutes, and exact availability "
    "depends on your plan."
)
CONDITIONS: tuple[ForwardingCondition, ...] = (
    "unanswered",
    "busy",
    "unreachable",
)
TITLES: dict[ForwardingCondition, str] = {
    "unanswered": "Forward unanswered calls",
    "busy": "Forward calls when your line is busy",
    "unreachable": "Forward calls when your line is unreachable",
}
VERIFIED_FIXED_CODES: dict[
    CarrierCode,
    dict[ForwardingCondition, tuple[str, str]],
] = {
    "sfr": {
        "busy": ("*69*{national_number}#", "#69#"),
        "unanswered": ("*61*{national_number}#", "#61#"),
    },
    "free": {
        "busy": ("*69*{national_number}#", "#69#"),
        "unanswered": ("*61*{national_number}*20#", "#61#"),
    },
}


class ForwardingInstructionCatalog:
    def for_profile(
        self,
        carrier: CarrierCode,
        number_type: str | None,
        opevo_number: str,
    ) -> ForwardingGuide:
        national_number = self._french_national_number(opevo_number)
        steps = [
            self._step(
                carrier=carrier,
                number_type=number_type,
                condition=condition,
                national_number=national_number,
            )
            for condition in CONDITIONS
        ]
        return ForwardingGuide(
            version=CATALOG_VERSION,
            carrier=carrier,
            number_type=number_type,
            opevo_number=opevo_number,
            warning=WARNING,
            steps=steps,
        )

    @staticmethod
    def _french_national_number(opevo_number: str) -> str:
        try:
            parsed = phonenumbers.parse(opevo_number, None)
        except NumberParseException as exc:
            raise ValueError(
                "Assigned destination must be a valid French number"
            ) from exc
        if not phonenumbers.is_valid_number_for_region(parsed, "FR"):
            raise ValueError("Assigned destination must be a valid French number")
        formatted = phonenumbers.format_number(
            parsed,
            phonenumbers.PhoneNumberFormat.NATIONAL,
        )
        return re.sub(r"\D", "", formatted)

    def _step(
        self,
        *,
        carrier: CarrierCode,
        number_type: str | None,
        condition: ForwardingCondition,
        national_number: str,
    ) -> ForwardingStep:
        instructions, source_url = self._safe_guidance(
            carrier=carrier,
            number_type=number_type,
            condition=condition,
        )
        dial_code = None
        disable_code = None
        if number_type == "fixed":
            templates = VERIFIED_FIXED_CODES.get(carrier, {})
            codes = templates.get(condition)
            if codes is not None:
                dial_code = codes[0].format(national_number=national_number)
                disable_code = codes[1]
                instructions = [
                    "From the fixed line you are forwarding, dial the code shown "
                    "below and follow the carrier confirmation."
                ]

        return ForwardingStep(
            condition=condition,
            title=TITLES[condition],
            instructions=instructions,
            dial_code=dial_code,
            disable_code=disable_code,
            source_url=source_url,
        )

    @staticmethod
    def _safe_guidance(
        *,
        carrier: CarrierCode,
        number_type: str | None,
        condition: ForwardingCondition,
    ) -> tuple[list[str], str | None]:
        if carrier == "orange":
            source_urls = {
                "unanswered": ORANGE_CONDITIONAL_URL,
                "busy": ORANGE_VOICE_PORTAL_URL,
                "unreachable": None,
            }
            return (
                [
                    ForwardingInstructionCatalog._fallback_instruction(
                        "the Orange customer area or Livebox voice portal",
                        condition,
                    )
                ],
                source_urls[condition],
            )
        if carrier == "bouygues":
            return (
                [
                    ForwardingInstructionCatalog._fallback_instruction(
                        "your Bouygues account or phone call settings",
                        condition,
                    )
                ],
                BOUYGUES_TARIFF_GUIDE_URL if condition != "unreachable" else None,
            )
        if carrier == "free" and number_type == "mobile":
            return (
                [
                    ForwardingInstructionCatalog._fallback_instruction(
                        "the Free Mobile forwarding guide and your phone settings",
                        condition,
                    )
                ],
                FREE_MOBILE_URL if condition != "unreachable" else None,
            )
        if carrier == "sfr":
            source_url = (
                SFR_FIXED_OPTIONS_URL
                if number_type == "fixed" and condition != "unreachable"
                else None
            )
            return (
                [
                    ForwardingInstructionCatalog._fallback_instruction(
                        "your SFR account or phone call settings",
                        condition,
                    )
                ],
                source_url,
            )
        if carrier == "free":
            source_urls = {
                "unanswered": FREE_UNANSWERED_URL,
                "busy": FREE_BUSY_URL,
                "unreachable": None,
            }
            return (
                [
                    ForwardingInstructionCatalog._fallback_instruction(
                        "your Free account or phone call settings",
                        condition,
                    )
                ],
                source_urls[condition] if number_type == "fixed" else None,
            )
        return (
            [
                ForwardingInstructionCatalog._fallback_instruction(
                    "your carrier account or phone call settings",
                    condition,
                )
            ],
            None,
        )

    @staticmethod
    def _fallback_instruction(settings_location: str, condition: str) -> str:
        return (
            f"Check {settings_location} to see if your plan or device offers "
            f"forwarding for {condition} calls. If offered, select only that condition."
        )
