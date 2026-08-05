import pytest

from app.core.provider_failures import ProviderFailure
from app.services.outbox_service import OutboxPayloadError
from app.workers.outbox.failures import (
    OutboxDeliveryError,
    _classify_error,
    _outbox_error_class,
    provider_failure_delivery_error,
)


def test_outbox_delivery_error_rejects_unsafe_codes() -> None:
    with pytest.raises(ValueError, match="Unsafe outbox error code"):
        OutboxDeliveryError("private-provider-message", retryable=False)


def test_non_exhausting_delivery_error_must_be_retryable() -> None:
    with pytest.raises(ValueError, match="must be exhaustible"):
        OutboxDeliveryError(
            "provider_terminal",
            retryable=False,
            exhaustible=False,
        )


def test_provider_failure_mapping_preserves_retryability() -> None:
    failure = ProviderFailure(
        provider="livekit",
        operation="list_dispatches",
        disposition="retryable",
        error_class="timeout",
    )

    mapped = provider_failure_delivery_error(failure)

    assert mapped.error_code == "provider_retryable"
    assert mapped.retryable is True
    assert mapped.exhaustible is True


def test_payload_and_unknown_failures_remain_distinct() -> None:
    assert _classify_error(OutboxPayloadError("opaque")) == (
        "invalid_payload",
        False,
        True,
    )
    assert _classify_error(RuntimeError("private")) == (
        "internal_defect",
        False,
        True,
    )
    assert _outbox_error_class("provider_retryable") == "unavailable"
    assert _outbox_error_class("dispatch_conflict") == "conflict"
