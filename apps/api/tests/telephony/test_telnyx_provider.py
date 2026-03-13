import pytest
from types import SimpleNamespace

from app.providers.telephony.telnyx import TelephonyTelnyx
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


class FakeAvailablePhoneNumberResource:
    @classmethod
    def list(cls, api_key=None, **params):
        return SimpleNamespace(data=[SimpleNamespace(phone_number="+33123456789")])


class FakePhoneNumberOrderResource:
    calls: list[dict] = []

    @classmethod
    def create(cls, api_key=None, **params):
        cls.calls.append(params)
        return SimpleNamespace(id="order_123")


class FakePhoneNumberResource:
    list_calls: list[dict] = []
    modify_calls: list[dict] = []

    @classmethod
    def list(cls, api_key=None, **params):
        cls.list_calls.append(params)
        return SimpleNamespace(data=[SimpleNamespace(id="pn_123", phone_number="+33123456789")])

    @classmethod
    def modify(cls, sid, **params):
        cls.modify_calls.append({"sid": sid, **params})
        return SimpleNamespace(id=sid, connection_id=params.get("connection_id"))


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


@pytest.mark.anyio
async def test_telnyx_provider_orders_number_and_sets_connection() -> None:
    provider = TelephonyTelnyx(
        api_key="key_123",
        active_connection_id="conn_active",
        disabled_connection_id="conn_disabled",
        available_phone_number_resource=FakeAvailablePhoneNumberResource,
        phone_number_order_resource=FakePhoneNumberOrderResource,
        phone_number_resource=FakePhoneNumberResource,
    )

    result = await provider.provision_number(country_code="FR")

    assert result["e164"] == "+33123456789"
    assert result["provider_number_id"] == "pn_123"
    assert result["provider_connection_name"] == "app-active"
    assert FakePhoneNumberOrderResource.calls[0]["phone_numbers"] == ["+33123456789"]
    assert FakePhoneNumberResource.modify_calls[0]["connection_id"] == "conn_active"
