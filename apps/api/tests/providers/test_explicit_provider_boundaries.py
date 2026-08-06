import inspect
from contextlib import asynccontextmanager

import pytest

from app.core.provider_failures import ProviderFailure
from app.providers.carrier_lookup.factory import build_carrier_lookup_provider
from app.providers.carrier_lookup.fake import FakeCarrierLookupProvider
from app.providers.carrier_lookup.telnyx import TelnyxCarrierLookupProvider
from app.providers.livekit_dispatch.livekit import LiveKitDispatchAPIProvider
from app.providers.livekit_recording.livekit import LiveKitRecordingProvider
from app.providers.storage.s3 import S3Storage
from app.providers.subscriptions.factory import build_subscription_provider
from app.providers.subscriptions.fake import FakeSubscriptionProvider
from app.providers.subscriptions.stripe import StripeSubscriptionProvider
from app.providers.summaries.gemini import GeminiSummaryProvider
from app.providers.telephony.factory import create_telephony_provider
from app.providers.telephony.fake import FakeTelephonyProvider
from app.providers.telephony.telnyx import TelephonyTelnyx
from app.services.billing_service import BillingService
from app.services.billing_session_service import BillingSessionService
from app.services.livekit_recording_service import LiveKitRecordingService
from app.services.summary_service import SummaryService


class _Observability:
    @asynccontextmanager
    async def provider_operation(self, *_args, **_kwargs):
        yield


def _assert_signature(
    callable_object: object,
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
    keyword_only: tuple[str, ...] = (),
) -> None:
    parameters = inspect.signature(callable_object).parameters
    assert tuple(parameters) == required + optional
    for name in required:
        assert parameters[name].default is inspect.Parameter.empty
    for name in optional:
        assert parameters[name].default is not inspect.Parameter.empty
    for name in keyword_only:
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY


def test_provider_and_service_boundaries_require_runtime_dependencies() -> None:
    _assert_signature(
        build_carrier_lookup_provider,
        required=("settings", "observability"),
        keyword_only=("observability",),
    )
    _assert_signature(
        create_telephony_provider,
        required=("settings", "observability"),
        keyword_only=("observability",),
    )
    _assert_signature(
        TelnyxCarrierLookupProvider,
        required=("api_key", "observability"),
        optional=("http_client",),
        keyword_only=("api_key", "observability", "http_client"),
    )
    _assert_signature(
        TelephonyTelnyx,
        required=(
            "api_key",
            "active_connection_id",
            "disabled_connection_id",
            "ordering_enabled",
            "observability",
        ),
        optional=(
            "available_phone_number_resource",
            "phone_number_order_resource",
            "phone_number_resource",
        ),
        keyword_only=(
            "api_key",
            "active_connection_id",
            "disabled_connection_id",
            "ordering_enabled",
            "observability",
            "available_phone_number_resource",
            "phone_number_order_resource",
            "phone_number_resource",
        ),
    )
    _assert_signature(
        StripeSubscriptionProvider,
        required=("secret_key",),
        optional=("stripe_client",),
        keyword_only=("secret_key", "stripe_client"),
    )
    _assert_signature(
        GeminiSummaryProvider,
        required=("api_key", "model", "observability"),
        optional=("client",),
        keyword_only=("api_key", "model", "observability", "client"),
    )
    _assert_signature(
        S3Storage,
        required=(
            "bucket_name",
            "endpoint_url",
            "access_key",
            "secret_key",
            "region",
            "observability",
        ),
        optional=("client",),
        keyword_only=(
            "bucket_name",
            "endpoint_url",
            "access_key",
            "secret_key",
            "region",
            "observability",
            "client",
        ),
    )
    _assert_signature(
        LiveKitDispatchAPIProvider,
        required=("livekit_api", "observability"),
        keyword_only=("livekit_api", "observability"),
    )
    _assert_signature(
        LiveKitRecordingProvider,
        required=(
            "egress_client",
            "bucket_name",
            "endpoint_url",
            "access_key",
            "secret_key",
            "region",
            "observability",
        ),
        keyword_only=(
            "egress_client",
            "bucket_name",
            "endpoint_url",
            "access_key",
            "secret_key",
            "region",
            "observability",
        ),
    )
    _assert_signature(
        BillingSessionService,
        required=("settings", "observability"),
        optional=("stripe_module",),
        keyword_only=("settings", "observability", "stripe_module"),
    )
    _assert_signature(
        BillingService,
        required=("session", "settings"),
        optional=("arq_pool",),
        keyword_only=("settings", "arq_pool"),
    )
    _assert_signature(LiveKitRecordingService, required=("provider",))
    _assert_signature(SummaryService, required=("provider",))


def test_locked_optional_injection_points_default_only_to_none() -> None:
    for callable_object, names in (
        (TelnyxCarrierLookupProvider, ("http_client",)),
        (StripeSubscriptionProvider, ("stripe_client",)),
        (GeminiSummaryProvider, ("client",)),
        (S3Storage, ("client",)),
        (BillingSessionService, ("stripe_module",)),
        (BillingService, ("arq_pool",)),
    ):
        parameters = inspect.signature(callable_object).parameters
        assert all(parameters[name].default is None for name in names)


def test_explicit_fake_modes_select_only_named_fake_adapters(settings) -> None:
    observability = object()

    carrier = build_carrier_lookup_provider(
        settings,
        observability=observability,
    )
    telephony = create_telephony_provider(
        settings,
        observability=observability,
    )

    assert type(carrier) is FakeCarrierLookupProvider
    assert type(telephony) is FakeTelephonyProvider


@pytest.mark.anyio
async def test_real_carrier_mode_missing_api_key_never_falls_back_to_fake(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ForbiddenLookupResource:
        @staticmethod
        def retrieve(*_args, **_kwargs):
            raise AssertionError("missing credentials contacted Telnyx")

    from app.providers.carrier_lookup import factory as carrier_factory

    monkeypatch.setattr(
        carrier_factory,
        "FakeCarrierLookupProvider",
        lambda: (_ for _ in ()).throw(
            AssertionError("real carrier mode instantiated fake provider")
        ),
    )

    with pytest.raises(ProviderFailure) as selection_error:
        build_carrier_lookup_provider(
            settings.model_copy(
                update={"carrier_lookup_mode": "telnyx", "telnyx_api_key": None}
            ),
            observability=object(),
        )
    provider = TelnyxCarrierLookupProvider(
        api_key=None,
        observability=object(),
        http_client=ForbiddenLookupResource,
    )

    assert (
        selection_error.value.provider,
        selection_error.value.operation,
        selection_error.value.disposition,
        selection_error.value.error_class,
    ) == ("telnyx", "lookup_carrier", "terminal", "authentication")
    with pytest.raises(ProviderFailure) as exc_info:
        await provider.lookup("+33123456789")
    assert (
        exc_info.value.provider,
        exc_info.value.operation,
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("telnyx", "lookup_carrier", "terminal", "authentication")


def test_real_telephony_mode_missing_api_key_never_falls_back_to_fake(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.telephony import factory as telephony_factory

    monkeypatch.setattr(
        telephony_factory,
        "FakeTelephonyProvider",
        lambda: (_ for _ in ()).throw(
            AssertionError("real telephony mode instantiated fake provider")
        ),
    )

    with pytest.raises(ProviderFailure) as exc_info:
        create_telephony_provider(
            settings.model_copy(
                update={"telephony_mode": "telnyx", "telnyx_api_key": None}
            ),
            observability=object(),
        )

    assert (
        exc_info.value.provider,
        exc_info.value.operation,
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("telnyx", "provision_number", "terminal", "authentication")


@pytest.mark.anyio
async def test_real_billing_mode_missing_secret_never_falls_back_to_fake(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.subscriptions import factory as subscription_factory

    monkeypatch.setattr(
        subscription_factory,
        "FakeSubscriptionProvider",
        lambda: (_ for _ in ()).throw(
            AssertionError("real billing mode instantiated fake provider")
        ),
    )
    provider = build_subscription_provider(
        settings.model_copy(
            update={"billing_mode": "stripe", "stripe_secret_key": None}
        )
    )

    assert type(provider) is StripeSubscriptionProvider
    assert not isinstance(provider, FakeSubscriptionProvider)
    with pytest.raises(ProviderFailure) as caught:
        await provider.cancel_immediately("sub_missing_credentials")
    assert (
        caught.value.provider,
        caught.value.operation,
        caught.value.disposition,
        caught.value.error_class,
    ) == ("stripe", "cancel_subscription", "terminal", "validation")


@pytest.mark.anyio
async def test_real_gemini_missing_api_key_fails_before_client_construction() -> None:
    provider = GeminiSummaryProvider(
        api_key=None,
        model="gemini-test",
        observability=_Observability(),
    )

    with pytest.raises(ProviderFailure) as caught:
        await provider.generate_summary([{"speaker": "CALLER", "text": "Hello"}])

    assert (
        caught.value.provider,
        caught.value.operation,
        caught.value.disposition,
        caught.value.error_class,
    ) == ("gemini", "generate_summary", "terminal", "authentication")
