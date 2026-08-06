import inspect

import pytest

from app.core.provider_failures import ProviderFailure
from app.providers.carrier_lookup.factory import build_carrier_lookup_provider
from app.providers.carrier_lookup.fake import FakeCarrierLookupProvider
from app.providers.carrier_lookup.telnyx import TelnyxCarrierLookupProvider
from app.providers.livekit_dispatch.livekit import LiveKitDispatchAPIProvider
from app.providers.livekit_recording.livekit import LiveKitRecordingProvider
from app.providers.subscriptions.stripe import StripeSubscriptionProvider
from app.providers.summaries.gemini import GeminiSummaryProvider
from app.providers.telephony.factory import create_telephony_provider
from app.providers.telephony.fake import FakeTelephonyProvider
from app.providers.telephony.telnyx import TelephonyTelnyx
from app.services.billing_service import BillingService
from app.services.billing_session_service import BillingSessionService
from app.services.livekit_recording_service import LiveKitRecordingService


def _assert_required(callable_object: object, *parameter_names: str) -> None:
    parameters = inspect.signature(callable_object).parameters
    for name in parameter_names:
        assert parameters[name].default is inspect.Parameter.empty


def test_provider_and_service_boundaries_require_runtime_dependencies() -> None:
    _assert_required(build_carrier_lookup_provider, "settings", "observability")
    _assert_required(create_telephony_provider, "settings", "observability")
    _assert_required(TelnyxCarrierLookupProvider, "api_key", "observability")
    _assert_required(
        TelephonyTelnyx,
        "api_key",
        "active_connection_id",
        "disabled_connection_id",
        "ordering_enabled",
        "observability",
    )
    _assert_required(StripeSubscriptionProvider, "secret_key")
    _assert_required(
        GeminiSummaryProvider,
        "api_key",
        "model",
        "observability",
    )
    _assert_required(LiveKitDispatchAPIProvider, "livekit_api", "observability")
    _assert_required(LiveKitRecordingProvider, "observability")
    _assert_required(BillingSessionService, "settings", "observability")
    _assert_required(BillingService, "settings")
    _assert_required(LiveKitRecordingService, "provider")


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
) -> None:
    class ForbiddenLookupResource:
        @staticmethod
        def retrieve(*_args, **_kwargs):
            raise AssertionError("missing credentials contacted Telnyx")

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

    assert selection_error.value.error_class == "authentication"
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
) -> None:
    with pytest.raises(ProviderFailure) as exc_info:
        create_telephony_provider(
            settings.model_copy(
                update={"telephony_mode": "telnyx", "telnyx_api_key": None}
            ),
            observability=object(),
        )

    assert (
        exc_info.value.provider,
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("telnyx", "terminal", "authentication")
