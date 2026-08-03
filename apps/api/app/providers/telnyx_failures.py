import telnyx

from app.core.provider_failures import (
    ProviderFailure,
    ProviderOperation,
    provider_failure_from_http_status,
)


def classify_telnyx_exception(
    error: Exception,
    *,
    operation: ProviderOperation,
) -> ProviderFailure | None:
    if isinstance(error, telnyx.error.APIConnectionError):
        return ProviderFailure(
            provider="telnyx",
            operation=operation,
            disposition=(
                "retryable"
                if getattr(error, "should_retry", False) is True
                else "terminal"
            ),
            error_class="unavailable",
        )
    if isinstance(error, telnyx.error.TimeoutError):
        return ProviderFailure(
            provider="telnyx",
            operation=operation,
            disposition="retryable",
            error_class="timeout",
        )
    if isinstance(error, telnyx.error.RateLimitError):
        return ProviderFailure(
            provider="telnyx",
            operation=operation,
            disposition="retryable",
            error_class="rate_limited",
        )
    if isinstance(error, telnyx.error.ServiceUnavailableError):
        return ProviderFailure(
            provider="telnyx",
            operation=operation,
            disposition="retryable",
            error_class="unavailable",
        )
    if isinstance(
        error,
        (telnyx.error.AuthenticationError, telnyx.error.PermissionError),
    ):
        return ProviderFailure(
            provider="telnyx",
            operation=operation,
            disposition="terminal",
            error_class="authentication",
        )
    if isinstance(error, telnyx.error.ResourceNotFoundError):
        return ProviderFailure(
            provider="telnyx",
            operation=operation,
            disposition="terminal",
            error_class="not_found",
        )
    if isinstance(
        error,
        (
            telnyx.error.InvalidRequestError,
            telnyx.error.MethodNotSupportedError,
            telnyx.error.UnsupportedMediaTypeError,
            telnyx.error.InvalidParametersError,
        ),
    ):
        return ProviderFailure(
            provider="telnyx",
            operation=operation,
            disposition="terminal",
            error_class="validation",
        )
    if isinstance(error, telnyx.error.APIError):
        return provider_failure_from_http_status(
            provider="telnyx",
            operation=operation,
            status=error.http_status,
        )
    if isinstance(error, telnyx.error.TelnyxError):
        return ProviderFailure(
            provider="telnyx",
            operation=operation,
            disposition="terminal",
            error_class="unknown",
        )
    return None
