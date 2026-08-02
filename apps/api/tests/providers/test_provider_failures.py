import asyncio

import pytest

from app.core.provider_failures import (
    ProviderFailure,
    provider_failure_from_http_status,
)


@pytest.mark.parametrize("disposition", ["retryable", "terminal"])
@pytest.mark.parametrize(
    "error_class",
    [
        "timeout",
        "rate_limited",
        "unavailable",
        "authentication",
        "validation",
        "conflict",
        "not_found",
        "unknown",
    ],
)
def test_provider_failure_exposes_only_bounded_safe_fields(
    disposition: str,
    error_class: str,
) -> None:
    failure = ProviderFailure(
        provider="stripe",
        operation="cancel_subscription",
        disposition=disposition,
        error_class=error_class,
    )

    assert failure.provider == "stripe"
    assert failure.operation == "cancel_subscription"
    assert failure.disposition == disposition
    assert failure.error_class == error_class
    assert failure.retryable is (disposition == "retryable")
    assert "secret" not in str(failure).casefold()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "private_provider"),
        ("operation", "private_operation"),
        ("disposition", "later"),
        ("error_class", "private_error"),
    ],
)
def test_provider_failure_rejects_unbounded_safe_labels(
    field: str,
    value: str,
) -> None:
    fields = {
        "provider": "stripe",
        "operation": "cancel_subscription",
        "disposition": "terminal",
        "error_class": "unknown",
    }
    fields[field] = value

    with pytest.raises(ValueError):
        ProviderFailure(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "context",
    [
        {"authorization": "Bearer TOKEN_PRIVATE_SENTINEL"},
        {"phone": "+33612345678"},
        {"body": "RAW_BODY_PRIVATE_SENTINEL"},
        {"start_outcome": "started"},
    ],
)
def test_provider_failure_rejects_unsafe_context(context: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        ProviderFailure(
            provider="livekit",
            operation="start_recording",
            disposition="retryable",
            error_class="timeout",
            context=context,
        )


def test_provider_failure_context_is_bounded_and_immutable() -> None:
    failure = ProviderFailure(
        provider="livekit",
        operation="start_recording",
        disposition="retryable",
        error_class="timeout",
        context={"start_outcome": "unknown"},
    )

    assert failure.context == {"start_outcome": "unknown"}
    with pytest.raises(TypeError):
        failure.context["start_outcome"] = "not_started"  # type: ignore[index]


def test_provider_failure_keeps_a_chained_cause_without_rendering_it() -> None:
    cause = RuntimeError("RAW_CAUSE_SECRET")

    try:
        raise cause
    except RuntimeError as captured_cause:
        with pytest.raises(ProviderFailure) as captured_failure:
            raise ProviderFailure(
                provider="stripe",
                operation="cancel_subscription",
                disposition="terminal",
                error_class="unknown",
            ) from captured_cause

    failure = captured_failure.value
    assert failure.__cause__ is cause
    assert "RAW_CAUSE_SECRET" not in str(failure)
    assert "RAW_CAUSE_SECRET" not in repr(failure)
    assert "RAW_CAUSE_SECRET" not in repr(failure.args)


def test_provider_failure_does_not_turn_cancellation_into_a_provider_failure() -> None:
    cancellation = asyncio.CancelledError("CANCELLATION_PRIVATE_SENTINEL")

    assert not isinstance(cancellation, ProviderFailure)
    assert not issubclass(ProviderFailure, asyncio.CancelledError)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (408, ("retryable", "timeout")),
        (429, ("retryable", "rate_limited")),
        (503, ("retryable", "unavailable")),
        (401, ("terminal", "authentication")),
        (403, ("terminal", "authentication")),
        (404, ("terminal", "not_found")),
        (409, ("terminal", "conflict")),
        (422, ("terminal", "validation")),
        (499, ("terminal", "validation")),
        (599, ("retryable", "unavailable")),
        (None, ("terminal", "unknown")),
        (302, ("terminal", "unknown")),
    ],
)
def test_http_status_mapping_is_literal_and_provider_independent(
    status: int | None,
    expected: tuple[str, str],
) -> None:
    failure = provider_failure_from_http_status(
        provider="stripe",
        operation="cancel_subscription",
        status=status,
    )

    assert (failure.disposition, failure.error_class) == expected
