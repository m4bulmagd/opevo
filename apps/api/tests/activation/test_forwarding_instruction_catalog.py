import pytest

from app.providers.carrier_lookup.base import CarrierCode
from app.schemas.forwarding import ForwardingGuide
from app.services.forwarding_instruction_catalog import (
    BOUYGUES_TARIFF_GUIDE_URL,
    CATALOG_VERSION,
    FREE_BUSY_URL,
    FREE_MOBILE_URL,
    FREE_UNANSWERED_URL,
    ORANGE_CONDITIONAL_URL,
    ORANGE_VOICE_PORTAL_URL,
    SFR_FIXED_OPTIONS_URL,
    ForwardingInstructionCatalog,
)


# ARCEP reserves the 09 99 range for technical/internal use rather than subscribers.
OPEVO_NUMBER = "+33999000000"
CONDITIONS = ["unanswered", "busy", "unreachable"]
BANNED_UNCONDITIONAL_ACTIONS = ("unconditional", "*21*", "#21#")


def build_guide(
    carrier: CarrierCode,
    number_type: str | None = "fixed",
) -> ForwardingGuide:
    return ForwardingInstructionCatalog().for_profile(
        carrier=carrier,
        number_type=number_type,
        opevo_number=OPEVO_NUMBER,
    )


@pytest.mark.parametrize("carrier", ["orange", "sfr", "bouygues", "free", "other"])
def test_every_carrier_has_versioned_conditional_sections(
    carrier: CarrierCode,
) -> None:
    guide = build_guide(carrier)

    assert guide.version == "fr-forwarding-2026-07-17" == CATALOG_VERSION
    assert [step.condition for step in guide.steps] == CONDITIONS
    assert guide.carrier == carrier
    assert guide.number_type == "fixed"
    assert guide.opevo_number == OPEVO_NUMBER
    assert "may charge forwarded-call minutes" in guide.warning
    assert "availability depends on your plan" in guide.warning

    rendered = guide.model_dump_json().lower()
    assert all(action not in rendered for action in BANNED_UNCONDITIONAL_ACTIONS)


def test_sfr_fixed_uses_only_verified_copyable_codes() -> None:
    guide = build_guide("sfr")

    assert guide.step("unanswered").dial_code == "*61*0999000000#"
    assert guide.step("unanswered").disable_code == "#61#"
    assert str(guide.step("unanswered").source_url) == SFR_FIXED_OPTIONS_URL
    assert guide.step("busy").dial_code == "*69*0999000000#"
    assert guide.step("busy").disable_code == "#69#"
    assert str(guide.step("busy").source_url) == SFR_FIXED_OPTIONS_URL
    assert guide.step("unreachable").dial_code is None
    assert guide.step("unreachable").disable_code is None


def test_free_fixed_uses_only_verified_copyable_codes() -> None:
    guide = build_guide("free")

    assert guide.step("unanswered").dial_code == "*61*0999000000*20#"
    assert guide.step("unanswered").disable_code == "#61#"
    assert str(guide.step("unanswered").source_url) == FREE_UNANSWERED_URL
    assert guide.step("busy").dial_code == "*69*0999000000#"
    assert guide.step("busy").disable_code == "#69#"
    assert str(guide.step("busy").source_url) == FREE_BUSY_URL
    assert guide.step("unreachable").dial_code is None
    assert guide.step("unreachable").disable_code is None


@pytest.mark.parametrize("carrier", ["sfr", "free"])
def test_mobile_guides_never_reuse_fixed_line_codes(carrier: CarrierCode) -> None:
    guide = build_guide(carrier, "mobile")

    assert all(step.dial_code is None for step in guide.steps)
    assert all(step.disable_code is None for step in guide.steps)
    if carrier == "free":
        assert {str(step.source_url) for step in guide.steps if step.source_url} == {
            FREE_MOBILE_URL
        }


@pytest.mark.parametrize("carrier", ["orange", "bouygues", "other"])
def test_safe_fallbacks_do_not_guess_codes(carrier: CarrierCode) -> None:
    guide = build_guide(carrier)

    assert all(step.dial_code is None for step in guide.steps)
    assert all(step.disable_code is None for step in guide.steps)
    assert all(step.instructions for step in guide.steps)


def test_orange_uses_public_voice_portal_and_customer_area_sources() -> None:
    guide = build_guide("orange")

    assert str(guide.step("unanswered").source_url) == ORANGE_CONDITIONAL_URL
    assert str(guide.step("busy").source_url) == ORANGE_VOICE_PORTAL_URL
    assert guide.step("unreachable").source_url is None


def test_bouygues_uses_public_tariff_source_without_account_data() -> None:
    guide = build_guide("bouygues")

    assert {str(step.source_url) for step in guide.steps if step.source_url} == {
        BOUYGUES_TARIFF_GUIDE_URL
    }
    assert OPEVO_NUMBER not in BOUYGUES_TARIFF_GUIDE_URL


def test_other_carrier_has_no_invented_provider_source() -> None:
    guide = build_guide("other")

    assert all(step.source_url is None for step in guide.steps)


@pytest.mark.parametrize("carrier", ["orange", "sfr", "bouygues", "free", "other"])
def test_unreachable_never_has_a_copyable_code(carrier: CarrierCode) -> None:
    step = build_guide(carrier).step("unreachable")

    assert step.dial_code is None
    assert step.disable_code is None
    assert step.source_url is None
    assert "if" in " ".join(step.instructions).lower()


def test_catalog_rejects_non_french_assigned_destination() -> None:
    # NANPA reserves 555-0100 through 555-0199 for fictional use.
    with pytest.raises(ValueError, match="French"):
        ForwardingInstructionCatalog().for_profile(
            carrier="sfr",
            number_type="fixed",
            opevo_number="+12025550123",
        )
