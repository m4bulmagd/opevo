import logging
import pytest
from types import SimpleNamespace

from app.providers.telephony.base import (
    TelephonyProvisioningPending,
    TelephonyProvisioningReviewRequired,
)
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
    responses: list[list[SimpleNamespace]] = []
    calls: list[dict] = []

    @classmethod
    def list(cls, api_key=None, **params):
        cls.calls.append(params)
        data = cls.responses.pop(0) if cls.responses else [SimpleNamespace(phone_number="+33123456789")]
        return SimpleNamespace(data=data)


class FakePhoneNumberOrderResource:
    calls: list[dict] = []
    list_calls: list[dict] = []
    orders: list[SimpleNamespace] = []

    @classmethod
    def list(cls, api_key=None, **params):
        cls.list_calls.append(params)
        customer_reference = params.get("filter[customer_reference]")
        return SimpleNamespace(
            data=[
                order
                for order in cls.orders
                if order.customer_reference == customer_reference
            ]
        )

    @classmethod
    def create(cls, api_key=None, **params):
        cls.calls.append(params)
        order = SimpleNamespace(
            id="order_123",
            customer_reference=params.get("customer_reference"),
            requirements_met=True,
            phone_numbers=[
                SimpleNamespace(
                    phone_number=item["phone_number"],
                    status="success",
                )
                for item in params["phone_numbers"]
            ],
        )
        cls.orders.append(order)
        return order


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


class EmptyPhoneNumberResource:
    @classmethod
    def list(cls, api_key=None, **params):
        return SimpleNamespace(data=[])

    @classmethod
    def modify(cls, sid, **params):
        raise AssertionError("modify should not be called")


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
    FakeAvailablePhoneNumberResource.responses = [
        [
            SimpleNamespace(
                phone_number="+33123456789",
                cost_information={"currency": "USD", "upfront_cost": "1.00000", "monthly_cost": "0.50000"},
            )
        ]
    ]
    FakeAvailablePhoneNumberResource.calls = []
    FakePhoneNumberOrderResource.calls = []
    FakePhoneNumberOrderResource.list_calls = []
    FakePhoneNumberOrderResource.orders = []
    FakePhoneNumberResource.modify_calls = []
    provider = TelephonyTelnyx(
        api_key="key_123",
        active_connection_id="conn_active",
        disabled_connection_id="conn_disabled",
        ordering_enabled=True,
        available_phone_number_resource=FakeAvailablePhoneNumberResource,
        phone_number_order_resource=FakePhoneNumberOrderResource,
        phone_number_resource=FakePhoneNumberResource,
    )

    result = await provider.provision_number(
        country_code="FR",
        operation_key="outbox:phone-provision:evt_123",
    )

    assert result["e164"] == "+33123456789"
    assert result["provider_number_id"] == "pn_123"
    assert result["provider_connection_name"] == "app-disabled"
    assert FakePhoneNumberOrderResource.calls[0]["phone_numbers"] == [{"phone_number": "+33123456789"}]
    assert (
        FakePhoneNumberOrderResource.calls[0]["customer_reference"]
        == "outbox:phone-provision:evt_123"
    )
    assert FakePhoneNumberOrderResource.list_calls == [
        {"filter[customer_reference]": "outbox:phone-provision:evt_123"}
    ]
    assert FakePhoneNumberResource.modify_calls[0]["connection_id"] == "conn_disabled"


@pytest.mark.anyio
async def test_telnyx_provider_reconciles_same_operation_before_buying_again() -> None:
    FakeAvailablePhoneNumberResource.responses = [
        [
            SimpleNamespace(
                phone_number="+33123456789",
                cost_information={
                    "currency": "USD",
                    "upfront_cost": "1.00000",
                    "monthly_cost": "0.50000",
                },
            )
        ]
    ]
    FakeAvailablePhoneNumberResource.calls = []
    FakePhoneNumberOrderResource.calls = []
    FakePhoneNumberOrderResource.list_calls = []
    FakePhoneNumberOrderResource.orders = []
    FakePhoneNumberResource.list_calls = []
    FakePhoneNumberResource.modify_calls = []
    provider = TelephonyTelnyx(
        api_key="key_123",
        active_connection_id="conn_active",
        disabled_connection_id="conn_disabled",
        ordering_enabled=True,
        available_phone_number_resource=FakeAvailablePhoneNumberResource,
        phone_number_order_resource=FakePhoneNumberOrderResource,
        phone_number_resource=FakePhoneNumberResource,
    )

    first = await provider.provision_number(
        country_code="FR",
        operation_key="outbox:phone-provision:stable",
    )
    replay = await provider.provision_number(
        country_code="FR",
        operation_key="outbox:phone-provision:stable",
    )

    assert replay == first
    assert len(FakePhoneNumberOrderResource.calls) == 1
    assert len(FakeAvailablePhoneNumberResource.calls) == 1


@pytest.mark.anyio
async def test_telnyx_provider_reports_existing_pending_order_for_outbox_retry() -> None:
    FakeAvailablePhoneNumberResource.calls = []
    FakePhoneNumberOrderResource.calls = []
    FakePhoneNumberOrderResource.list_calls = []
    FakePhoneNumberOrderResource.orders = [
        SimpleNamespace(
            id="order_pending",
            customer_reference="outbox:phone-provision:stable",
            requirements_met=False,
            phone_numbers=[
                SimpleNamespace(
                    phone_number="+33123456789",
                    status="pending",
                )
            ],
        )
    ]
    provider = TelephonyTelnyx(
        api_key="key_123",
        active_connection_id="conn_active",
        disabled_connection_id="conn_disabled",
        ordering_enabled=True,
        available_phone_number_resource=FakeAvailablePhoneNumberResource,
        phone_number_order_resource=FakePhoneNumberOrderResource,
        phone_number_resource=FakePhoneNumberResource,
    )

    with pytest.raises(TelephonyProvisioningPending) as exc_info:
        await provider.provision_number(
            country_code="FR",
            operation_key="outbox:phone-provision:stable",
        )

    assert exc_info.value.reason == "existing_order_pending"
    assert not FakePhoneNumberOrderResource.calls
    assert not FakeAvailablePhoneNumberResource.calls


@pytest.mark.anyio
async def test_telnyx_provider_requires_manual_review_for_unmet_success_order() -> None:
    FakeAvailablePhoneNumberResource.calls = []
    FakePhoneNumberOrderResource.calls = []
    FakePhoneNumberOrderResource.orders = [
        SimpleNamespace(
            id="order_unmet",
            customer_reference="outbox:phone-provision:stable",
            requirements_met=False,
            phone_numbers=[
                SimpleNamespace(
                    phone_number="+33123456789",
                    status="success",
                )
            ],
        )
    ]
    provider = TelephonyTelnyx(
        api_key="key_123",
        active_connection_id="conn_active",
        disabled_connection_id="conn_disabled",
        ordering_enabled=True,
        available_phone_number_resource=FakeAvailablePhoneNumberResource,
        phone_number_order_resource=FakePhoneNumberOrderResource,
        phone_number_resource=FakePhoneNumberResource,
    )

    with pytest.raises(TelephonyProvisioningReviewRequired) as exc_info:
        await provider.provision_number(
            country_code="FR",
            operation_key="outbox:phone-provision:stable",
        )

    assert exc_info.value.reason == "existing_order_requires_review"
    assert exc_info.value.payload["manual_review_required"] is True
    assert not FakePhoneNumberOrderResource.calls
    assert not FakeAvailablePhoneNumberResource.calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("orders", "expected_reason"),
    [
        (
            [
                SimpleNamespace(
                    id="order_one",
                    customer_reference="outbox:phone-provision:stable",
                    requirements_met=True,
                    phone_numbers=[
                        SimpleNamespace(
                            phone_number="+33123456789",
                            status="success",
                        )
                    ],
                ),
                SimpleNamespace(
                    id="order_two",
                    customer_reference="outbox:phone-provision:stable",
                    requirements_met=True,
                    phone_numbers=[
                        SimpleNamespace(
                            phone_number="+33123456780",
                            status="success",
                        )
                    ],
                ),
            ],
            "existing_order_conflict",
        ),
    ],
)
async def test_telnyx_provider_does_not_reorder_when_prior_order_is_unsafe(
    orders: list[SimpleNamespace],
    expected_reason: str,
) -> None:
    FakeAvailablePhoneNumberResource.calls = []
    FakePhoneNumberOrderResource.calls = []
    FakePhoneNumberOrderResource.list_calls = []
    FakePhoneNumberOrderResource.orders = orders
    provider = TelephonyTelnyx(
        api_key="key_123",
        active_connection_id="conn_active",
        disabled_connection_id="conn_disabled",
        ordering_enabled=True,
        available_phone_number_resource=FakeAvailablePhoneNumberResource,
        phone_number_order_resource=FakePhoneNumberOrderResource,
        phone_number_resource=FakePhoneNumberResource,
    )

    with pytest.raises(TelephonyProvisioningReviewRequired) as exc_info:
        await provider.provision_number(
            country_code="FR",
            operation_key="outbox:phone-provision:stable",
        )

    assert exc_info.value.reason == expected_reason
    assert exc_info.value.payload["manual_review_required"] is True
    assert not FakePhoneNumberOrderResource.calls
    assert not FakeAvailablePhoneNumberResource.calls


@pytest.mark.anyio
async def test_telnyx_provider_tries_national_then_local_and_stops_before_ordering_when_disabled() -> None:
    FakeAvailablePhoneNumberResource.responses = [
        [
            SimpleNamespace(
                phone_number="+33800000000",
                cost_information={"currency": "USD", "upfront_cost": "30.00000", "monthly_cost": "0.00000"},
            )
        ],
        [
            SimpleNamespace(
                phone_number="+33111111111",
                cost_information={"currency": "USD", "upfront_cost": "1.00000", "monthly_cost": "0.50000"},
            )
        ],
    ]
    FakeAvailablePhoneNumberResource.calls = []
    FakePhoneNumberOrderResource.calls = []

    provider = TelephonyTelnyx(
        api_key="key_123",
        active_connection_id="conn_active",
        disabled_connection_id="conn_disabled",
        ordering_enabled=False,
        available_phone_number_resource=FakeAvailablePhoneNumberResource,
        phone_number_order_resource=FakePhoneNumberOrderResource,
        phone_number_resource=FakePhoneNumberResource,
    )

    with pytest.raises(TelephonyProvisioningReviewRequired) as exc_info:
        await provider.provision_number(country_code="FR")

    assert exc_info.value.reason == "ordering_disabled"
    assert exc_info.value.payload["selected_candidate"]["e164"] == "+33111111111"
    assert FakeAvailablePhoneNumberResource.calls[0]["filter[phone_number_type]"] == "national"
    assert FakeAvailablePhoneNumberResource.calls[1]["filter[phone_number_type]"] == "local"
    assert not FakePhoneNumberOrderResource.calls


@pytest.mark.anyio
async def test_telnyx_provider_logs_selected_candidate_before_review_required(caplog) -> None:
    FakeAvailablePhoneNumberResource.responses = [
        [
            SimpleNamespace(
                phone_number="+33111111111",
                cost_information={"currency": "USD", "upfront_cost": "1.00000", "monthly_cost": "0.50000"},
            )
        ],
    ]

    provider = TelephonyTelnyx(
        api_key="key_123",
        active_connection_id="conn_active",
        disabled_connection_id="conn_disabled",
        ordering_enabled=False,
        available_phone_number_resource=FakeAvailablePhoneNumberResource,
        phone_number_order_resource=FakePhoneNumberOrderResource,
        phone_number_resource=FakePhoneNumberResource,
    )

    with caplog.at_level(logging.INFO):
        with pytest.raises(TelephonyProvisioningReviewRequired):
            await provider.provision_number(country_code="FR")

    assert "Selected Telnyx number +33******11 for provisioning (country_code=FR)" in caplog.text
    assert "+33111111111" not in caplog.text


@pytest.mark.anyio
async def test_telnyx_provider_error_does_not_expose_selected_number() -> None:
    selected_number = "+33111111111"
    FakeAvailablePhoneNumberResource.responses = [
        [
            SimpleNamespace(
                phone_number=selected_number,
                cost_information={
                    "currency": "USD",
                    "upfront_cost": "1.00000",
                    "monthly_cost": "0.50000",
                },
            )
        ],
    ]
    provider = TelephonyTelnyx(
        api_key="key_123",
        active_connection_id="conn_active",
        disabled_connection_id="conn_disabled",
        ordering_enabled=True,
        available_phone_number_resource=FakeAvailablePhoneNumberResource,
        phone_number_order_resource=FakePhoneNumberOrderResource,
        phone_number_resource=EmptyPhoneNumberResource,
    )

    with pytest.raises(ValueError) as exc_info:
        await provider.provision_number(
            country_code="FR",
            operation_key="outbox:phone-provision:error-path",
        )

    assert selected_number not in str(exc_info.value)


@pytest.mark.anyio
async def test_telnyx_provider_stops_after_three_unaffordable_candidates() -> None:
    FakeAvailablePhoneNumberResource.responses = [
        [
            SimpleNamespace(
                phone_number="+33800000000",
                cost_information={"currency": "USD", "upfront_cost": "30.00000", "monthly_cost": "0.00000"},
            ),
            SimpleNamespace(
                phone_number="+33800000001",
                cost_information={"currency": "USD", "upfront_cost": "10.00000", "monthly_cost": "0.00000"},
            ),
        ],
        [
            SimpleNamespace(
                phone_number="+33111111111",
                cost_information={"currency": "USD", "upfront_cost": "1.80000", "monthly_cost": "0.50000"},
            )
        ],
    ]
    FakeAvailablePhoneNumberResource.calls = []
    FakePhoneNumberOrderResource.calls = []

    provider = TelephonyTelnyx(
        api_key="key_123",
        active_connection_id="conn_active",
        disabled_connection_id="conn_disabled",
        ordering_enabled=False,
        available_phone_number_resource=FakeAvailablePhoneNumberResource,
        phone_number_order_resource=FakePhoneNumberOrderResource,
        phone_number_resource=FakePhoneNumberResource,
    )

    with pytest.raises(TelephonyProvisioningReviewRequired) as exc_info:
        await provider.provision_number(country_code="FR")

    assert exc_info.value.reason == "no_affordable_number"
    assert exc_info.value.payload["attempts"] == 3
    assert exc_info.value.payload["contact_support"] is True
    assert not FakePhoneNumberOrderResource.calls
