import asyncio
from types import SimpleNamespace

import pytest
import telnyx

from app.core.provider_failures import ProviderFailure
from app.providers.carrier_lookup.base import (
    normalize_carrier_name,
    normalize_number_type,
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


def test_number_type_normalization_rejects_malformed_non_string() -> None:
    with pytest.raises(ValueError, match="Malformed number type"):
        normalize_number_type(object())  # type: ignore[arg-type]


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
    ("provider_error", "expected_disposition", "expected_error_class"),
    [
        (telnyx.error.APIConnectionError("network secret", should_retry=True), "retryable", "unavailable"),
        (telnyx.error.APIConnectionError("network secret", should_retry=False), "terminal", "unavailable"),
        (telnyx.error.TimeoutError([{"title": "timeout secret"}]), "retryable", "timeout"),
        (telnyx.error.RateLimitError([{"title": "rate secret"}]), "retryable", "rate_limited"),
        (
            telnyx.error.ServiceUnavailableError([{"title": "service secret"}]),
            "retryable", "unavailable",
        ),
        (
            telnyx.error.AuthenticationError([{"title": "credential secret"}]),
            "terminal", "authentication",
        ),
        (telnyx.error.PermissionError([{"title": "permission secret"}]), "terminal", "authentication"),
        (
            telnyx.error.InvalidRequestError([{"title": "request secret"}]),
            "terminal", "validation",
        ),
        (
            telnyx.error.InvalidParametersError([{"title": "parameter secret"}]),
            "terminal", "validation",
        ),
        (
            telnyx.error.ResourceNotFoundError([{"title": "resource secret"}]),
            "terminal", "not_found",
        ),
        (
            telnyx.error.APIError([{"title": "conflict secret"}], http_status=409),
            "terminal", "conflict",
        ),
        (
            telnyx.error.APIError([{"title": "other client secret"}], http_status=418),
            "terminal", "validation",
        ),
        (
            telnyx.error.APIError(
                [{"title": "internal provider secret"}],
                http_status=500,
            ),
            "retryable", "unavailable",
        ),
        (
            telnyx.error.TelnyxError(
                [{"title": "unexpected sdk secret"}],
                http_status=418,
            ),
            "terminal", "unknown",
        ),
    ],
)
async def test_telnyx_errors_map_to_shared_safe_failure_fields(
    provider_error: Exception,
    expected_disposition: str,
    expected_error_class: str,
) -> None:
    class FailingNumberLookupResource:
        @classmethod
        def retrieve(cls, phone_number, /, *, api_key):
            raise provider_error

    provider = TelnyxCarrierLookupProvider(
        api_key="lookup-key",
        number_lookup_resource=FailingNumberLookupResource,
    )

    with pytest.raises(ProviderFailure) as exc_info:
        await provider.lookup("+33612345678")

    assert (
        exc_info.value.provider,
        exc_info.value.operation,
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("telnyx", "lookup_carrier", expected_disposition, expected_error_class)
    assert exc_info.value.retryable is (expected_disposition == "retryable")
    assert exc_info.value.__cause__ is provider_error
    assert "secret" not in str(exc_info.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "malformed_carrier",
    [
        {"name": "Orange France", "type": object()},
        {"name": object(), "type": "mobile"},
        object(),
    ],
)
async def test_telnyx_malformed_dynamic_carrier_fields_fail_safely(
    malformed_carrier: object,
) -> None:
    class MalformedNumberLookupResource:
        @classmethod
        def retrieve(cls, phone_number, /, *, api_key):
            return {
                "data": {
                    "phone_number": phone_number,
                    "country_code": "FR",
                    "carrier": malformed_carrier,
                }
            }

    provider = TelnyxCarrierLookupProvider(
        api_key="lookup-key",
        number_lookup_resource=MalformedNumberLookupResource,
    )

    with pytest.raises(ProviderFailure) as exc_info:
        await provider.lookup("+33612345678")

    assert (exc_info.value.disposition, exc_info.value.error_class) == (
        "terminal", "validation"
    )


@pytest.mark.anyio
async def test_telnyx_lookup_does_not_translate_injected_adapter_defects() -> None:
    class DefectiveLookupResource:
        @staticmethod
        def retrieve(*_args: object, **_kwargs: object) -> object:
            raise TypeError("INTERNAL_SENTINEL")

    provider = TelnyxCarrierLookupProvider(
        api_key="test-key",
        number_lookup_resource=DefectiveLookupResource,
    )

    with pytest.raises(TypeError, match="INTERNAL_SENTINEL"):
        await provider.lookup("+33612345678")


@pytest.mark.anyio
async def test_telnyx_lookup_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.providers.carrier_lookup.telnyx as carrier_telnyx

    async def cancel_to_thread(*_args: object, **_kwargs: object) -> object:
        raise asyncio.CancelledError

    monkeypatch.setattr(carrier_telnyx.asyncio, "to_thread", cancel_to_thread)
    provider = TelnyxCarrierLookupProvider(api_key="test-key")

    with pytest.raises(asyncio.CancelledError):
        await provider.lookup("+33612345678")
