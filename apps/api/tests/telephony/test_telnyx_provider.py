import pytest

from app.services.telephony_service import TelephonyService


class FakeTelephonyProvider:
    async def provision_number(self, *, country_code: str) -> dict:
        return {
            "e164": "+33123456789",
            "provider_number_id": "tnx-number-123",
            "provider_connection_name": "app-active",
        }

    async def enable_number(self, *, provider_number_id: str) -> str:
        return "app-active"

    async def disable_number(self, *, provider_number_id: str) -> str:
        return "app-disabled"


@pytest.mark.anyio
async def test_provision_number_assigns_e164_to_user(db_session, active_user) -> None:
    service = TelephonyService(db_session, provider=FakeTelephonyProvider())

    phone_number = await service.provision_number(active_user.id, country_code="FR")

    assert phone_number.e164.startswith("+33")


@pytest.mark.anyio
async def test_disable_number_switches_to_disabled_app(db_session, active_user) -> None:
    service = TelephonyService(db_session, provider=FakeTelephonyProvider())
    assigned_number = await service.provision_number(active_user.id, country_code="FR")

    updated = await service.disable_number(assigned_number.user_id)

    assert updated.provider_connection_name == "app-disabled"
