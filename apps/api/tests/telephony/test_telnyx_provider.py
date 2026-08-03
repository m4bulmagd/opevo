import asyncio
import logging
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import app.providers.telephony.telnyx as telnyx_module
import telnyx
import telnyx.util as telnyx_util
from app.core.provider_failures import ProviderFailure
from app.providers.telephony.base import (
    TelephonyProvisioningPending,
    TelephonyProvisioningReviewRequired,
)
from app.providers.telephony.telnyx import TelephonyTelnyx
from app.services.telephony_service import TelephonyService


class _ProviderTelemetry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    @asynccontextmanager
    async def provider_operation(self, provider: str, operation: str, **_kwargs):
        try:
            yield
        except Exception:
            self.calls.append((provider, operation, "error"))
            raise
        else:
            self.calls.append((provider, operation, "success"))


class _TelnyxReleaseHTTPClient:
    name = "provider-free-telnyx-stub"
    _timeout = (5, 30)

    def __init__(self, *, response_body: str, status: int = 200) -> None:
        self.response_body = response_body
        self.status = status
        self.calls: list[dict[str, object]] = []

    def request_with_retries(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        post_data: object,
    ) -> tuple[str, int, dict[str, str]]:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "post_data": post_data,
            }
        )
        return self.response_body, self.status, {}


@pytest.mark.anyio
async def test_telnyx_provider_operation_is_observed_once() -> None:
    telemetry = _ProviderTelemetry()
    provider = TelephonyTelnyx(
        api_key="key",
        active_connection_id="active",
        disabled_connection_id="disabled",
        phone_number_resource=FakePhoneNumberResource,
        observability=telemetry,
    )

    assert await provider.enable_number(provider_number_id="pn_123") == "app-active"
    assert telemetry.calls == [("telnyx", "enable_number", "success")]


@pytest.mark.anyio
async def test_telnyx_disable_treats_exact_missing_number_as_satisfied() -> None:
    phone_number_resource = MagicMock()
    phone_number_resource.modify.side_effect = telnyx.error.ResourceNotFoundError(
        [{"title": "private provider response"}],
        http_status=404,
    )
    provider = TelephonyTelnyx(
        api_key="KEY",
        disabled_connection_id="disabled",
        phone_number_resource=phone_number_resource,
    )

    assert (
        await provider.disable_number(provider_number_id="123456789") == "app-disabled"
    )
    phone_number_resource.modify.assert_called_once_with(
        "123456789",
        api_key="KEY",
        connection_id="disabled",
    )


@pytest.mark.anyio
async def test_telnyx_release_uses_pinned_sdk_instance_delete_without_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_number_id = "123456789"
    http_client = _TelnyxReleaseHTTPClient(
        response_body=(
            '{"data":{"id":"123456789","record_type":"phone_number",'
            '"status":"deleted"}}'
        )
    )
    monkeypatch.setattr(telnyx, "default_http_client", http_client)
    provider = TelephonyTelnyx(api_key="KEY")

    await provider.release_number(provider_number_id=provider_number_id)

    assert [call["method"] for call in http_client.calls] == ["delete"]
    assert http_client.calls[0]["url"] == (
        f"{telnyx.api_base}/v2/phone_numbers/{provider_number_id}"
    )
    assert http_client.calls[0]["post_data"] is None


@pytest.mark.anyio
async def test_telnyx_release_deletes_exact_provider_number_and_confirms_response() -> (
    None
):
    phone_number = MagicMock()
    phone_number.delete.return_value = {
        "data": {"id": "123456789", "status": "deleted"}
    }
    phone_number_resource = MagicMock()
    phone_number_resource.return_value = phone_number
    provider = TelephonyTelnyx(
        api_key="KEY",
        phone_number_resource=phone_number_resource,
    )

    await provider.release_number(provider_number_id="123456789")

    phone_number_resource.assert_called_once_with("123456789", api_key="KEY")
    phone_number.delete.assert_called_once_with()


@pytest.mark.anyio
async def test_telnyx_release_accepts_supported_nested_response_object() -> None:
    phone_number = MagicMock()
    phone_number.delete.return_value = SimpleNamespace(
        data=SimpleNamespace(id="123456789", status="deleted")
    )
    phone_number_resource = MagicMock()
    phone_number_resource.return_value = phone_number
    provider = TelephonyTelnyx(
        api_key="KEY",
        phone_number_resource=phone_number_resource,
    )

    await provider.release_number(provider_number_id="123456789")


@pytest.mark.anyio
async def test_telnyx_release_treats_missing_exact_provider_number_as_success() -> None:
    phone_number = MagicMock()
    phone_number.delete.side_effect = [
        telnyx.error.ResourceNotFoundError(
            [{"title": "private provider response"}],
            http_status=404,
        ),
        telnyx.error.ResourceNotFoundError(
            [{"title": "private provider response"}],
            http_status=404,
        ),
    ]
    phone_number_resource = MagicMock()
    phone_number_resource.return_value = phone_number
    provider = TelephonyTelnyx(
        api_key="KEY",
        phone_number_resource=phone_number_resource,
    )

    await provider.release_number(provider_number_id="123456789")
    await provider.release_number(provider_number_id="123456789")

    phone_number_resource.assert_called_with("123456789", api_key="KEY")
    assert phone_number_resource.call_count == 2
    assert phone_number.delete.call_count == 2


@pytest.mark.anyio
async def test_telnyx_release_rejects_none_provider_response() -> None:
    class NoneReturningPhoneNumber:
        def __init__(self, provider_number_id: str, *, api_key: str) -> None:
            pass

        def delete(self) -> None:
            return None

    provider = TelephonyTelnyx(
        api_key="KEY",
        phone_number_resource=NoneReturningPhoneNumber,
    )

    with pytest.raises(ProviderFailure) as exc_info:
        await provider.release_number(provider_number_id="123456789")

    assert (
        exc_info.value.operation,
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("release_number", "terminal", "validation")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("result", "expected_error_class"),
    [
        ({"data": {"id": "other-provider-id", "status": "deleted"}}, "conflict"),
        ({"data": {"id": "123456789", "status": "active"}}, "validation"),
        ({"data": {"id": "123456789"}}, "validation"),
        ({"data": {"status": "deleted"}}, "validation"),
        ({"not_data": {}}, "validation"),
    ],
)
async def test_telnyx_release_requires_exact_deleted_response(
    result: dict,
    expected_error_class: str,
) -> None:
    phone_number = MagicMock()
    phone_number.delete.return_value = result
    phone_number_resource = MagicMock()
    phone_number_resource.return_value = phone_number
    provider = TelephonyTelnyx(
        api_key="KEY",
        phone_number_resource=phone_number_resource,
    )

    with pytest.raises(ProviderFailure) as exc_info:
        await provider.release_number(provider_number_id="123456789")

    assert exc_info.value.operation == "release_number"
    assert exc_info.value.disposition == "terminal"
    assert exc_info.value.error_class == expected_error_class
    assert exc_info.value.__cause__ is None


@pytest.mark.anyio
async def test_telnyx_sdk_release_error_parsing_cannot_log_private_details(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_number_id = "PROVIDER_NUMBER_ID_SENTINEL"
    private_sentinels = (
        provider_number_id,
        "RAW_TELNYX_CODE_SENTINEL",
        "RAW_TELNYX_TITLE_SENTINEL",
        "RAW_TELNYX_DETAIL_SENTINEL",
        "RAW_TELNYX_SOURCE_SENTINEL",
        "+33123456789",
    )
    http_client = _TelnyxReleaseHTTPClient(
        response_body=(
            '{"errors":[{"code":"RAW_TELNYX_CODE_SENTINEL",'
            '"title":"RAW_TELNYX_TITLE_SENTINEL",'
            '"detail":"RAW_TELNYX_DETAIL_SENTINEL +33123456789",'
            '"source":{"pointer":"RAW_TELNYX_SOURCE_SENTINEL"}}]}'
        ),
        status=422,
    )
    monkeypatch.setattr(telnyx, "default_http_client", http_client)
    monkeypatch.setattr(telnyx, "log", "debug")
    monkeypatch.setattr(telnyx_util, "TELNYX_LOG", "debug")
    provider = TelephonyTelnyx(api_key="CREDENTIAL_SENTINEL")

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ProviderFailure) as exc_info:
            logging.getLogger("app.unrelated").info(
                "unrelated application log remains visible"
            )
            await provider.release_number(provider_number_id=provider_number_id)

    captured = capsys.readouterr()
    assert exc_info.value.disposition == "terminal"
    assert exc_info.value.error_class == "validation"
    assert len(http_client.calls) == 1
    for sentinel in private_sentinels:
        assert sentinel not in caplog.text
        assert sentinel not in captured.err
    assert "CREDENTIAL_SENTINEL" not in caplog.text
    assert "CREDENTIAL_SENTINEL" not in captured.err
    assert "unrelated application log remains visible" in caplog.text
    assert "Telnyx API provider details suppressed" in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_error", "expected_disposition", "expected_error_class"),
    [
        (
            telnyx.error.TimeoutError(
                [{"title": "timeout response with private credential"}],
                http_status=408,
            ),
            "retryable",
            "timeout",
        ),
        (
            telnyx.error.RateLimitError(
                [{"title": "rate limit response with private credential"}],
                http_status=429,
            ),
            "retryable",
            "rate_limited",
        ),
        (
            telnyx.error.APIError(
                [{"title": "service failure with private credential"}],
                http_status=503,
            ),
            "retryable",
            "unavailable",
        ),
        (
            telnyx.error.AuthenticationError(
                [{"title": "authentication response with private credential"}],
            ),
            "terminal",
            "authentication",
        ),
        (
            telnyx.error.APIError(
                [{"title": "deletion lock with private provider response"}],
                http_status=422,
            ),
            "terminal",
            "validation",
        ),
        (
            telnyx.error.APIError(
                [{"title": "identity conflict with private provider response"}],
                http_status=409,
            ),
            "terminal",
            "conflict",
        ),
    ],
)
async def test_telnyx_release_uses_safe_provider_error_categories(
    provider_error: Exception,
    expected_disposition: str,
    expected_error_class: str,
) -> None:
    phone_number = MagicMock()
    phone_number.delete.side_effect = provider_error
    phone_number_resource = MagicMock()
    phone_number_resource.return_value = phone_number
    provider = TelephonyTelnyx(
        api_key="KEY",
        phone_number_resource=phone_number_resource,
    )

    with pytest.raises(ProviderFailure) as exc_info:
        await provider.release_number(provider_number_id="123456789")

    assert (
        exc_info.value.provider,
        exc_info.value.operation,
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("telnyx", "release_number", expected_disposition, expected_error_class)
    assert exc_info.value.__cause__ is provider_error


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("06 12 34 56 78", "+33612345678"),
        ("0033 6 12 34 56 78", "+33612345678"),
        ("+33 6 12 34 56 78", "+33612345678"),
    ],
)
def test_normalize_french_number(raw: str, expected: str) -> None:
    assert telnyx_module.normalize_french_number(raw) == expected


def test_normalize_french_number_rejects_valid_non_french_number() -> None:
    with pytest.raises(ValueError, match="valid French phone number"):
        telnyx_module.normalize_french_number("+44 20 7946 0958")


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


class FakeLocalFormatTelephonyProvider(FakeTelephonyProvider):
    async def provision_number(self, *, country_code: str) -> dict:
        result = await super().provision_number(country_code=country_code)
        return {**result, "e164": "06 12 34 56 78"}


class FakeAvailablePhoneNumberResource:
    responses: list[list[SimpleNamespace]] = []
    calls: list[dict] = []

    @classmethod
    def list(cls, api_key=None, **params):
        cls.calls.append(params)
        data = (
            cls.responses.pop(0)
            if cls.responses
            else [SimpleNamespace(phone_number="+33123456789")]
        )
        return SimpleNamespace(data=data)


class _AvailableNumberListSubclass(list[object]):
    pass


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
        return SimpleNamespace(
            data=[SimpleNamespace(id="pn_123", phone_number="+33123456789")]
        )

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


class BlockingAvailablePhoneNumberResource:
    @classmethod
    def list(cls, api_key=None, **params):
        time.sleep(0.1)
        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    phone_number="+33123456789",
                    cost_information={
                        "currency": "USD",
                        "upfront_cost": "1.00000",
                        "monthly_cost": "0.50000",
                    },
                )
            ]
        )


class DisagreeingPhoneNumberResource(FakePhoneNumberResource):
    @classmethod
    def modify(cls, sid, **params):
        return SimpleNamespace(id=sid, connection_id="different-connection")


class FailingPhoneNumberResource(FakePhoneNumberResource):
    error: Exception

    @classmethod
    def modify(cls, sid, **params):
        raise cls.error


class RetryPolicyAssertingNumberOrderResource(FakePhoneNumberOrderResource):
    create_calls = 0

    @classmethod
    def create(cls, api_key=None, **params):
        cls.create_calls += 1
        assert telnyx.max_network_retries == 0
        return super().create(api_key=api_key, **params)


class PendingThenSuccessfulNumberOrderResource:
    calls: list[dict] = []
    list_calls: list[dict] = []
    orders: list[SimpleNamespace] = []

    @classmethod
    def list(cls, api_key=None, **params):
        cls.list_calls.append(params)
        if len(cls.list_calls) >= 3 and cls.orders:
            cls.orders[0].requirements_met = True
            cls.orders[0].phone_numbers[0].status = "success"
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
            id="order_pending_then_successful",
            customer_reference=params["customer_reference"],
            requirements_met=False,
            phone_numbers=[
                SimpleNamespace(
                    phone_number=params["phone_numbers"][0]["phone_number"],
                    status="pending",
                )
            ],
        )
        cls.orders.append(order)
        return order


@pytest.mark.anyio
async def test_provision_number_assigns_e164_to_user(db_session, active_user) -> None:
    service = TelephonyService(db_session, provider=FakeTelephonyProvider())

    phone_number = await service.provision_number(active_user.id, country_code="FR")

    assert phone_number.e164.startswith("+33")


@pytest.mark.anyio
async def test_provisioned_number_is_normalized_before_persistence(
    db_session,
    active_user,
) -> None:
    service = TelephonyService(
        db_session,
        provider=FakeLocalFormatTelephonyProvider(),
    )

    phone_number = await service.provision_number(active_user.id, country_code="FR")

    assert phone_number.e164 == "+33612345678"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "result",
    [
        {
            "e164": "+442079460958",
            "provider_number_id": "tnx-number-123",
            "provider_connection_name": "app-disabled",
        },
        {
            "e164": "+33123456789",
            "provider_connection_name": "app-disabled",
        },
        {
            "e164": "+33123456789",
            "provider_number_id": "tnx-number-123",
            "provider_connection_name": "unexpected-connection",
        },
    ],
)
async def test_provisioned_provider_result_must_be_valid_and_complete(
    db_session,
    active_user,
    result: dict,
) -> None:
    class MalformedProvider(FakeTelephonyProvider):
        async def provision_number(self, *, country_code: str) -> dict:
            return result

    service = TelephonyService(db_session, provider=MalformedProvider())

    with pytest.raises(ProviderFailure) as exc_info:
        await service.provision_number(active_user.id, country_code="FR")

    assert (
        exc_info.value.operation,
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("provision_number", "terminal", "validation")


@pytest.mark.anyio
async def test_disable_number_switches_to_disabled_app(db_session, active_user) -> None:
    service = TelephonyService(db_session, provider=FakeTelephonyProvider())
    assigned_number = await service.provision_number(active_user.id, country_code="FR")
    await db_session.commit()

    updated = await service.disable_number(assigned_number.user_id)

    assert updated.provider_connection_name == "app-disabled"


@pytest.mark.anyio
async def test_telephony_service_ends_business_transaction_before_provider_io(
    db_session,
    active_user,
) -> None:
    class TransactionCheckingProvider(FakeTelephonyProvider):
        calls: list[str]

        def __init__(self) -> None:
            self.calls = []

        def _assert_no_business_transaction(self, operation: str) -> None:
            assert db_session.in_transaction() is False
            self.calls.append(operation)

        async def provision_number(self, *, country_code: str) -> dict:
            self._assert_no_business_transaction("provision")
            return {
                "e164": "+33123456789",
                "provider_number_id": "tnx-number-transaction",
                "provider_connection_name": "app-disabled",
            }

        async def enable_number(self, *, provider_number_id: str) -> str:
            self._assert_no_business_transaction("enable")
            return "app-active"

        async def disable_number(self, *, provider_number_id: str) -> str:
            self._assert_no_business_transaction("disable")
            return "app-disabled"

    provider = TransactionCheckingProvider()
    service = TelephonyService(db_session, provider=provider)
    user_id = active_user.id

    await service.provision_number(user_id, country_code="FR")
    await db_session.commit()
    enabled = await service.enable_number(user_id)
    assert enabled.provider_connection_name == "app-active"
    await db_session.commit()
    disabled = await service.disable_number(user_id)

    assert disabled.provider_connection_name == "app-disabled"
    assert provider.calls == ["provision", "enable", "disable"]


@pytest.mark.anyio
async def test_telnyx_provider_orders_number_and_sets_connection() -> None:
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
    assert FakePhoneNumberOrderResource.calls[0]["phone_numbers"] == [
        {"phone_number": "+33123456789"}
    ]
    assert (
        FakePhoneNumberOrderResource.calls[0]["customer_reference"]
        == "outbox:phone-provision:evt_123"
    )
    assert FakePhoneNumberOrderResource.list_calls == [
        {"filter[customer_reference]": "outbox:phone-provision:evt_123"},
        {"filter[customer_reference]": "outbox:phone-provision:evt_123"},
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
async def test_telnyx_recovery_is_lookup_only_and_returns_exact_provider_identity() -> (
    None
):
    operation_key = "outbox:phone-provision:crash-recovery"
    FakeAvailablePhoneNumberResource.calls = []
    FakePhoneNumberOrderResource.calls = []
    FakePhoneNumberOrderResource.list_calls = []
    FakePhoneNumberOrderResource.orders = [
        SimpleNamespace(
            id="order_recovered",
            customer_reference=operation_key,
            requirements_met=True,
            phone_numbers=[
                SimpleNamespace(
                    phone_number="+33123456789",
                    status="success",
                )
            ],
        )
    ]
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

    recovered = await provider.recover_provisioned_number(
        country_code="FR",
        operation_key=operation_key,
    )

    assert recovered == {
        "e164": "+33123456789",
        "provider_number_id": "pn_123",
        "provider_connection_name": "app-disabled",
    }
    assert FakePhoneNumberOrderResource.list_calls == [
        {"filter[customer_reference]": operation_key}
    ]
    assert FakePhoneNumberResource.list_calls == [
        {"filter[phone_number]": "+33123456789"}
    ]
    assert FakePhoneNumberOrderResource.calls == []
    assert FakeAvailablePhoneNumberResource.calls == []
    assert FakePhoneNumberResource.modify_calls == []


@pytest.mark.anyio
async def test_telnyx_pending_order_replay_activates_without_duplicate_purchase() -> (
    None
):
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
    PendingThenSuccessfulNumberOrderResource.calls = []
    PendingThenSuccessfulNumberOrderResource.list_calls = []
    PendingThenSuccessfulNumberOrderResource.orders = []
    FakePhoneNumberResource.list_calls = []
    FakePhoneNumberResource.modify_calls = []
    provider = TelephonyTelnyx(
        api_key="key_123",
        active_connection_id="conn_active",
        disabled_connection_id="conn_disabled",
        ordering_enabled=True,
        available_phone_number_resource=FakeAvailablePhoneNumberResource,
        phone_number_order_resource=PendingThenSuccessfulNumberOrderResource,
        phone_number_resource=FakePhoneNumberResource,
    )
    operation_key = "outbox:phone-provision:pending-replay"

    with pytest.raises(TelephonyProvisioningPending) as exc_info:
        await provider.provision_number(
            country_code="FR",
            operation_key=operation_key,
        )

    assert exc_info.value.reason == "existing_order_pending"
    result = await provider.provision_number(
        country_code="FR",
        operation_key=operation_key,
    )

    assert result == {
        "e164": "+33123456789",
        "provider_number_id": "pn_123",
        "provider_connection_name": "app-disabled",
    }
    assert len(PendingThenSuccessfulNumberOrderResource.calls) == 1
    assert (
        PendingThenSuccessfulNumberOrderResource.calls[0]["customer_reference"]
        == operation_key
    )
    assert len(FakeAvailablePhoneNumberResource.calls) == 1


@pytest.mark.anyio
async def test_telnyx_number_order_post_runs_with_sdk_retries_disabled() -> None:
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
    FakePhoneNumberOrderResource.calls = []
    FakePhoneNumberOrderResource.orders = []
    RetryPolicyAssertingNumberOrderResource.create_calls = 0
    provider = TelephonyTelnyx(
        api_key="key_123",
        active_connection_id="conn_active",
        disabled_connection_id="conn_disabled",
        ordering_enabled=True,
        available_phone_number_resource=FakeAvailablePhoneNumberResource,
        phone_number_order_resource=RetryPolicyAssertingNumberOrderResource,
        phone_number_resource=FakePhoneNumberResource,
    )

    await provider.provision_number(
        country_code="FR",
        operation_key="outbox:phone-provision:no-sdk-post-retry",
    )

    assert RetryPolicyAssertingNumberOrderResource.create_calls == 1


@pytest.mark.anyio
async def test_telnyx_provider_reports_existing_pending_order_for_outbox_retry() -> (
    None
):
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
async def test_telnyx_provider_tries_national_then_local_and_stops_before_ordering_when_disabled() -> (
    None
):
    FakeAvailablePhoneNumberResource.responses = [
        [
            SimpleNamespace(
                phone_number="+33800000000",
                cost_information={
                    "currency": "USD",
                    "upfront_cost": "30.00000",
                    "monthly_cost": "0.00000",
                },
            )
        ],
        [
            SimpleNamespace(
                phone_number="+33111111111",
                cost_information={
                    "currency": "USD",
                    "upfront_cost": "1.00000",
                    "monthly_cost": "0.50000",
                },
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
    assert exc_info.value.payload["selected_candidate"]["phone_number_type"] == "local"
    assert "e164" not in exc_info.value.payload["selected_candidate"]
    assert (
        FakeAvailablePhoneNumberResource.calls[0]["filter[phone_number_type]"]
        == "national"
    )
    assert (
        FakeAvailablePhoneNumberResource.calls[1]["filter[phone_number_type]"]
        == "local"
    )
    assert not FakePhoneNumberOrderResource.calls


@pytest.mark.anyio
async def test_telnyx_provider_logs_selected_candidate_before_review_required(
    caplog,
) -> None:
    FakeAvailablePhoneNumberResource.responses = [
        [
            SimpleNamespace(
                phone_number="+33111111111",
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
        ordering_enabled=False,
        available_phone_number_resource=FakeAvailablePhoneNumberResource,
        phone_number_order_resource=FakePhoneNumberOrderResource,
        phone_number_resource=FakePhoneNumberResource,
    )

    with caplog.at_level(logging.INFO):
        with pytest.raises(TelephonyProvisioningReviewRequired):
            await provider.provision_number(country_code="FR")

    assert (
        "Selected Telnyx number +33******11 for provisioning (country_code=FR)"
        in caplog.text
    )
    assert "+33111111111" not in caplog.text


@pytest.mark.anyio
async def test_telnyx_review_payload_does_not_persist_candidate_number() -> None:
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
        ]
    ]
    provider = TelephonyTelnyx(
        api_key="key_123",
        ordering_enabled=False,
        available_phone_number_resource=FakeAvailablePhoneNumberResource,
    )

    with pytest.raises(TelephonyProvisioningReviewRequired) as exc_info:
        await provider.provision_number(country_code="FR")

    assert selected_number not in str(exc_info.value.payload)


@pytest.mark.anyio
async def test_telnyx_known_order_without_phone_row_is_pending_and_private() -> None:
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

    with pytest.raises(TelephonyProvisioningPending) as exc_info:
        await provider.provision_number(
            country_code="FR",
            operation_key="outbox:phone-provision:error-path",
        )

    assert exc_info.value.reason == "existing_order_pending"
    assert selected_number not in str(exc_info.value)


@pytest.mark.anyio
async def test_telnyx_provider_stops_after_three_unaffordable_candidates() -> None:
    FakeAvailablePhoneNumberResource.responses = [
        [
            SimpleNamespace(
                phone_number="+33800000000",
                cost_information={
                    "currency": "USD",
                    "upfront_cost": "30.00000",
                    "monthly_cost": "0.00000",
                },
            ),
            SimpleNamespace(
                phone_number="+33800000001",
                cost_information={
                    "currency": "USD",
                    "upfront_cost": "10.00000",
                    "monthly_cost": "0.00000",
                },
            ),
        ],
        [
            SimpleNamespace(
                phone_number="+33111111111",
                cost_information={
                    "currency": "USD",
                    "upfront_cost": "1.80000",
                    "monthly_cost": "0.50000",
                },
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


@pytest.mark.anyio
@pytest.mark.parametrize(
    "data",
    [
        None,
        1,
        "AVAILABLE_NUMBER_PRIVATE_SENTINEL",
        {"private": "AVAILABLE_NUMBER_PRIVATE_SENTINEL"},
        (),
        iter(()),
        _AvailableNumberListSubclass(),
        [object(), object(), object(), object()],
    ],
)
async def test_telnyx_provider_rejects_malformed_available_number_data(
    data: object,
) -> None:
    class MalformedAvailablePhoneNumberResource:
        @classmethod
        def list(cls, api_key=None, **params):
            return SimpleNamespace(data=data)

    provider = TelephonyTelnyx(
        api_key="key_123",
        ordering_enabled=False,
        available_phone_number_resource=MalformedAvailablePhoneNumberResource,
    )

    with pytest.raises(ProviderFailure) as exc_info:
        await provider.provision_number(country_code="FR")

    assert (
        exc_info.value.provider,
        exc_info.value.operation,
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("telnyx", "provision_number", "terminal", "validation")
    assert "AVAILABLE_NUMBER_PRIVATE_SENTINEL" not in str(exc_info.value)


@pytest.mark.anyio
@pytest.mark.parametrize("defect_type", [TypeError, RuntimeError, AttributeError])
async def test_telnyx_available_number_data_accessor_defects_propagate_identity(
    defect_type: type[Exception],
) -> None:
    defect = defect_type("AVAILABLE_DATA_ACCESSOR_DEFECT_SENTINEL")

    class HostileAvailableNumberResponse:
        @property
        def data(self) -> object:
            raise defect

    class HostileAvailablePhoneNumberResource:
        @classmethod
        def list(cls, api_key=None, **params):
            return HostileAvailableNumberResponse()

    provider = TelephonyTelnyx(
        api_key="key_123",
        ordering_enabled=False,
        available_phone_number_resource=HostileAvailablePhoneNumberResource,
    )

    with pytest.raises(defect_type) as exc_info:
        await provider.provision_number(country_code="FR")

    assert exc_info.value is defect


@pytest.mark.anyio
async def test_telnyx_empty_available_number_data_keeps_no_candidate_review() -> None:
    class EmptyAvailablePhoneNumberResource:
        calls = 0

        @classmethod
        def list(cls, api_key=None, **params):
            cls.calls += 1
            return SimpleNamespace(data=[])

    provider = TelephonyTelnyx(
        api_key="key_123",
        ordering_enabled=False,
        available_phone_number_resource=EmptyAvailablePhoneNumberResource,
    )

    with pytest.raises(TelephonyProvisioningReviewRequired) as exc_info:
        await provider.provision_number(country_code="FR")

    assert exc_info.value.reason == "no_affordable_number"
    assert exc_info.value.payload["attempts"] == 0
    assert EmptyAvailablePhoneNumberResource.calls == 2


@pytest.mark.anyio
async def test_telnyx_provider_rejects_valid_non_french_candidates() -> None:
    british_candidate = SimpleNamespace(
        phone_number="+442079460958",
        cost_information={
            "currency": "USD",
            "upfront_cost": "1.00000",
            "monthly_cost": "0.50000",
        },
    )
    FakeAvailablePhoneNumberResource.responses = [
        [british_candidate],
        [british_candidate],
    ]
    FakePhoneNumberOrderResource.calls = []
    provider = TelephonyTelnyx(
        api_key="key_123",
        ordering_enabled=False,
        available_phone_number_resource=FakeAvailablePhoneNumberResource,
        phone_number_order_resource=FakePhoneNumberOrderResource,
        phone_number_resource=FakePhoneNumberResource,
    )

    with pytest.raises(TelephonyProvisioningReviewRequired) as exc_info:
        await provider.provision_number(country_code="FR")

    assert exc_info.value.reason == "no_affordable_number"
    assert not FakePhoneNumberOrderResource.calls


@pytest.mark.anyio
async def test_telnyx_resource_calls_do_not_block_the_event_loop() -> None:
    provider = TelephonyTelnyx(
        api_key="key_123",
        ordering_enabled=False,
        available_phone_number_resource=BlockingAvailablePhoneNumberResource,
        phone_number_order_resource=FakePhoneNumberOrderResource,
        phone_number_resource=FakePhoneNumberResource,
    )
    heartbeat = asyncio.create_task(asyncio.sleep(0.02))

    with pytest.raises(TelephonyProvisioningReviewRequired):
        await provider.provision_number(country_code="FR")

    assert heartbeat.done()


@pytest.mark.anyio
async def test_telnyx_connection_change_requires_matching_provider_response() -> None:
    provider = TelephonyTelnyx(
        api_key="key_123",
        active_connection_id="conn_active",
        phone_number_resource=DisagreeingPhoneNumberResource,
    )

    with pytest.raises(ProviderFailure) as exc_info:
        await provider.enable_number(provider_number_id="pn_123")

    assert type(exc_info.value) is ProviderFailure
    assert exc_info.value.disposition == "retryable"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_error", "expected_disposition", "expected_error_class"),
    [
        (
            telnyx.error.APIConnectionError(
                "retryable TLS secret +33123456789",
                should_retry=True,
            ),
            "retryable",
            "unavailable",
        ),
        (
            telnyx.error.APIConnectionError(
                "terminal TLS secret +33123456789",
                should_retry=False,
            ),
            "terminal",
            "unavailable",
        ),
        (
            telnyx.error.TimeoutError(
                [{"title": "timeout secret +33123456789"}],
                http_status=408,
            ),
            "retryable",
            "timeout",
        ),
        (
            telnyx.error.RateLimitError(
                [{"title": "rate limited secret +33123456789"}],
                http_status=429,
            ),
            "retryable",
            "rate_limited",
        ),
        (
            telnyx.error.ServiceUnavailableError(
                [{"title": "service secret +33123456789"}],
                http_status=503,
            ),
            "retryable",
            "unavailable",
        ),
        (
            telnyx.error.AuthenticationError(
                [{"title": "invalid credential sk-secret"}],
            ),
            "terminal",
            "authentication",
        ),
        (
            telnyx.error.PermissionError(
                [{"title": "permission secret +33123456789"}],
            ),
            "terminal",
            "authentication",
        ),
        (
            telnyx.error.ResourceNotFoundError(
                [{"title": "missing secret +33123456789"}],
                http_status=404,
            ),
            "terminal",
            "not_found",
        ),
        (
            telnyx.error.InvalidRequestError(
                [{"title": "invalid request secret +33123456789"}],
            ),
            "terminal",
            "validation",
        ),
        (
            telnyx.error.InvalidParametersError(
                [{"title": "invalid parameters secret +33123456789"}],
            ),
            "terminal",
            "validation",
        ),
        (
            telnyx.error.APIError(
                [{"title": "conflict secret +33123456789"}],
                http_status=409,
            ),
            "terminal",
            "conflict",
        ),
        (
            telnyx.error.APIError(
                [{"title": "other client status secret +33123456789"}],
                http_status=418,
            ),
            "terminal",
            "validation",
        ),
        (
            telnyx.error.APIError(
                [{"title": "generic server secret"}],
                http_status=503,
            ),
            "retryable",
            "unavailable",
        ),
        (
            telnyx.error.TelnyxError(
                [{"title": "unknown SDK secret +33123456789"}],
                http_status=418,
            ),
            "terminal",
            "unknown",
        ),
    ],
)
async def test_telnyx_errors_use_shared_safe_failure_fields(
    provider_error: Exception,
    expected_disposition: str,
    expected_error_class: str,
) -> None:
    FailingPhoneNumberResource.error = provider_error
    provider = TelephonyTelnyx(
        api_key="key_123",
        active_connection_id="conn_active",
        phone_number_resource=FailingPhoneNumberResource,
    )

    with pytest.raises(ProviderFailure) as exc_info:
        await provider.enable_number(provider_number_id="pn_123")

    assert type(exc_info.value) is ProviderFailure
    assert (
        exc_info.value.provider,
        exc_info.value.operation,
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("telnyx", "enable_number", expected_disposition, expected_error_class)
    assert exc_info.value.__cause__ is provider_error
    assert "secret" not in str(exc_info.value).casefold()
    assert "secret" not in repr(exc_info.value).casefold()
    assert all("secret" not in str(value).casefold() for value in exc_info.value.args)


def test_telnyx_sdk_uses_bounded_network_policy() -> None:
    TelephonyTelnyx(api_key="key_123", ordering_enabled=False)

    # Telnyx 2.1.6 retries POST requests as well as reads. Durable outbox retry
    # and customer_reference reconciliation own replay safety instead.
    assert telnyx.max_network_retries == 0
    assert telnyx.default_http_client._timeout == (5, 30)
