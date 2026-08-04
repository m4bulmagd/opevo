from uuid import uuid4

import pytest

from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.services.number_provisioning_facts import number_is_provisioned


def build_case(
    case: str,
) -> tuple[PhoneNumberProvisioning | None, PhoneNumber | None]:
    user_id = uuid4()
    phone_number = PhoneNumber(
        user_id=user_id,
        e164="+33999000000",
        country_code="FR",
        provider="telnyx",
        provider_number_id="pn_readiness",
        provider_connection_name="app-disabled",
        is_active=False,
    )
    provisioning = PhoneNumberProvisioning(
        user_id=user_id,
        phone_number_id=phone_number.id,
        target_country_code="FR",
        status="succeeded",
        attempt_count=1,
        can_retry=False,
    )

    if case == "missing_provisioning":
        return None, phone_number
    if case == "running":
        provisioning.status = "running"
    elif case == "failed":
        provisioning.status = "failed"
    elif case == "missing_phone":
        return provisioning, None
    elif case == "mismatched_phone":
        provisioning.phone_number_id = uuid4()
    elif case == "missing_provider_id":
        phone_number.provider_number_id = None
    elif case == "empty_provider_id":
        phone_number.provider_number_id = ""
    elif case == "whitespace_provider_id":
        phone_number.provider_number_id = "   "
    elif case != "valid":
        raise AssertionError(f"unknown test case: {case}")
    return provisioning, phone_number


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("valid", True),
        ("missing_provisioning", False),
        ("running", False),
        ("failed", False),
        ("missing_phone", False),
        ("mismatched_phone", False),
        ("missing_provider_id", False),
        ("empty_provider_id", False),
        ("whitespace_provider_id", False),
    ],
)
def test_number_is_provisioned_requires_one_exact_completed_assignment(
    case: str,
    expected: bool,
) -> None:
    provisioning, phone_number = build_case(case)

    assert number_is_provisioned(
        provisioning=provisioning,
        phone_number=phone_number,
    ) is expected
