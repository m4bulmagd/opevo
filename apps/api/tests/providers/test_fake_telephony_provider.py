import pytest

from app.core.config import Settings
from app.core.provider_failures import ProviderFailure
from app.providers.telephony.factory import create_telephony_provider
from app.providers.telephony.fake import FakeTelephonyProvider


@pytest.mark.anyio
async def test_fake_telephony_is_deterministic_per_operation_key() -> None:
    provider = FakeTelephonyProvider()

    first = await provider.provision_number(
        country_code="FR",
        operation_key="activation-1",
    )
    second = await provider.provision_number(
        country_code="FR",
        operation_key="activation-1",
    )

    assert first == second
    assert first["e164"].startswith("+339")
    assert first["provider_connection_name"] == "app-disabled"


@pytest.mark.anyio
async def test_fake_telephony_keys_do_not_collide_in_representative_sample() -> None:
    provider = FakeTelephonyProvider()

    numbers = [
        await provider.provision_number(
            country_code="FR",
            operation_key=f"activation-{index}",
        )
        for index in range(100)
    ]

    assert len({result["e164"] for result in numbers}) == len(numbers)
    assert len({result["provider_number_id"] for result in numbers}) == len(numbers)
    assert all(result["e164"].startswith("+339") for result in numbers)
    assert all(result["provider_connection_name"] == "app-disabled" for result in numbers)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("country_code", "operation_key"),
    [
        ("IE", "activation-1"),
        ("FR", None),
        ("FR", ""),
        ("FR", "   "),
    ],
)
async def test_fake_telephony_rejects_invalid_provisioning_inputs_safely(
    country_code: str,
    operation_key: str | None,
) -> None:
    provider = FakeTelephonyProvider()

    with pytest.raises(ProviderFailure) as exc_info:
        await provider.provision_number(
            country_code=country_code,
            operation_key=operation_key,
        )

    assert (
        exc_info.value.provider,
        exc_info.value.operation,
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("fake", "validate", "terminal", "validation")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("operation", "provider_number_id"),
    [
        ("enable_number", "pn_real_provider_id"),
        ("disable_number", "pn_real_provider_id"),
        ("release_number", "pn_real_provider_id"),
        ("release_number", ""),
    ],
)
async def test_fake_telephony_rejects_non_fake_provider_ids_safely(
    operation: str,
    provider_number_id: str,
) -> None:
    provider = FakeTelephonyProvider()

    with pytest.raises(ProviderFailure) as exc_info:
        await getattr(provider, operation)(provider_number_id=provider_number_id)

    assert (
        exc_info.value.provider,
        exc_info.value.operation,
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("fake", "validate", "terminal", "validation")


@pytest.mark.anyio
async def test_fake_telephony_releases_its_deterministic_number_id_idempotently() -> None:
    provider = FakeTelephonyProvider()
    provisioned = await provider.provision_number(
        country_code="FR",
        operation_key="deactivation-release",
    )

    await provider.release_number(
        provider_number_id=provisioned["provider_number_id"],
    )
    await provider.release_number(
        provider_number_id=provisioned["provider_number_id"],
    )


@pytest.mark.anyio
async def test_fake_telephony_supports_every_routing_operation() -> None:
    provider = FakeTelephonyProvider()
    provisioned = await provider.provision_number(
        country_code="FR",
        operation_key="activation-routing",
    )

    assert (
        await provider.enable_number(
            provider_number_id=provisioned["provider_number_id"],
        )
        == "app-active"
    )
    assert (
        await provider.disable_number(
            provider_number_id=provisioned["provider_number_id"],
        )
        == "app-disabled"
    )


def test_telephony_factory_defaults_to_fake_for_local_settings() -> None:
    settings = Settings(
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
    )

    observability = object()
    provider = create_telephony_provider(settings, observability=observability)

    assert settings.telephony_mode == "fake"
    assert settings.billing_mode == "fake"
    assert isinstance(provider, FakeTelephonyProvider)


def test_telephony_factory_builds_telnyx_from_selected_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.telephony import factory as factory_module

    observed: dict[str, object] = {}

    class StubTelnyx:
        def __init__(self, **kwargs) -> None:
            observed.update(kwargs)

    monkeypatch.setattr(factory_module, "TelephonyTelnyx", StubTelnyx)
    settings = Settings(
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
        telephony_mode="telnyx",
        telnyx_api_key="test-key",
        telnyx_active_connection_id="active-connection",
        telnyx_disabled_connection_id="disabled-connection",
        telnyx_ordering_enabled=True,
    )

    observability = object()
    provider = create_telephony_provider(settings, observability=observability)

    assert isinstance(provider, StubTelnyx)
    assert observed == {
        "api_key": "test-key",
        "active_connection_id": "active-connection",
        "disabled_connection_id": "disabled-connection",
        "ordering_enabled": True,
        "observability": observability,
    }
