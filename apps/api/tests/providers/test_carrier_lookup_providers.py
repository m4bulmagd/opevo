import asyncio
from types import SimpleNamespace

import pytest
import telnyx

from app.providers.carrier_lookup.base import (
    CarrierLookupError,
    normalize_carrier_name,
)
from app.providers.carrier_lookup.factory import build_carrier_lookup_provider
from app.providers.carrier_lookup.fake import FakeCarrierLookupProvider
from app.providers.carrier_lookup.telnyx import TelnyxCarrierLookupProvider


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        ("Orange France", "orange"),
        ("ORANGE S.A.", "orange"),
        ("SFR", "sfr"),
        ("Societe Francaise du Radiotelephone SFR", "sfr"),
        ("Bouygues Telecom", "bouygues"),
        ("Free Mobile", "free"),
        ("Iliad Free", "free"),
        ("Freedom Mobile", "other"),
        ("Unlisted MVNO", "other"),
        (None, "other"),
    ],
)
def test_carrier_brand_normalization_is_central_and_deterministic(
    raw_name: str | None,
    expected: str,
) -> None:
    assert normalize_carrier_name(raw_name) == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("number", "number_type"),
    [
        ("+33 6 12 34 56 78", "mobile"),
        ("+33144556677", "fixed"),
    ],
)
async def test_fake_lookup_is_french_deterministic_and_provider_free(
    number: str,
    number_type: str,
) -> None:
    result = await FakeCarrierLookupProvider(carrier="bouygues").lookup(number)

    assert result.country_code == "FR"
    assert result.normalized_number.startswith("+33")
    assert result.carrier_name == "Bouygues"
    assert result.normalized_carrier == "bouygues"
    assert result.number_type == number_type


def test_factory_defaults_to_deterministic_fake(settings) -> None:
    provider = build_carrier_lookup_provider(settings=settings)

    assert isinstance(provider, FakeCarrierLookupProvider)


@pytest.mark.anyio
async def test_telnyx_lookup_uses_pinned_resource_contract_without_global_key_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeNumberLookupResource:
        calls: list[tuple[str, str]] = []

        @classmethod
        def retrieve(cls, phone_number, /, *, api_key):
            cls.calls.append((phone_number, api_key))
            return SimpleNamespace(
                data=SimpleNamespace(
                    phone_number="+33612345678",
                    country_code="FR",
                    carrier=SimpleNamespace(name="Orange France", type="mobile"),
                )
            )

    monkeypatch.setattr(telnyx, "api_key", "unchanged-global-key")
    provider = TelnyxCarrierLookupProvider(
        api_key="lookup-key",
        number_lookup_resource=FakeNumberLookupResource,
    )

    result = await provider.lookup("+33612345678")

    assert FakeNumberLookupResource.calls == [("+33612345678", "lookup-key")]
    assert telnyx.api_key == "unchanged-global-key"
    assert result.normalized_number == "+33612345678"
    assert result.country_code == "FR"
    assert result.carrier_name == "Orange France"
    assert result.normalized_carrier == "orange"
    assert result.number_type == "mobile"


@pytest.mark.anyio
async def test_telnyx_lookup_runs_blocking_sdk_resource_off_event_loop() -> None:
    class BlockingNumberLookupResource:
        @classmethod
        def retrieve(cls, phone_number, /, *, api_key):
            import time

            time.sleep(0.04)
            return {
                "data": {
                    "phone_number": phone_number,
                    "country_code": "FR",
                    "carrier": {"name": "SFR", "type": "mobile"},
                }
            }

    provider = TelnyxCarrierLookupProvider(
        api_key="lookup-key",
        number_lookup_resource=BlockingNumberLookupResource,
    )
    heartbeat = asyncio.create_task(asyncio.sleep(0.01))

    await provider.lookup("+33612345678")

    assert heartbeat.done()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_error", "expected_code"),
    [
        (telnyx.error.APIConnectionError("network secret"), "retryable"),
        (telnyx.error.TimeoutError([{"title": "timeout secret"}]), "retryable"),
        (telnyx.error.RateLimitError([{"title": "rate secret"}]), "retryable"),
        (
            telnyx.error.ServiceUnavailableError([{"title": "service secret"}]),
            "retryable",
        ),
        (
            telnyx.error.AuthenticationError([{"title": "credential secret"}]),
            "terminal",
        ),
        (telnyx.error.PermissionError([{"title": "permission secret"}]), "terminal"),
        (
            telnyx.error.InvalidRequestError([{"title": "request secret"}]),
            "terminal",
        ),
        (
            telnyx.error.InvalidParametersError([{"title": "parameter secret"}]),
            "terminal",
        ),
        (
            telnyx.error.ResourceNotFoundError([{"title": "resource secret"}]),
            "terminal",
        ),
    ],
)
async def test_telnyx_errors_map_to_safe_contract_codes(
    provider_error: Exception,
    expected_code: str,
) -> None:
    class FailingNumberLookupResource:
        @classmethod
        def retrieve(cls, phone_number, /, *, api_key):
            raise provider_error

    provider = TelnyxCarrierLookupProvider(
        api_key="lookup-key",
        number_lookup_resource=FailingNumberLookupResource,
    )

    with pytest.raises(CarrierLookupError) as exc_info:
        await provider.lookup("+33612345678")

    assert exc_info.value.code == expected_code
    assert exc_info.value.retryable is (expected_code == "retryable")
    assert str(exc_info.value) == expected_code
    assert "secret" not in str(exc_info.value)
