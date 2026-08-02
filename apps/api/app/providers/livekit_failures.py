from collections.abc import Mapping

from livekit import api

from app.core.provider_failures import (
    ProviderFailure,
    ProviderFailureClass,
    ProviderFailureDisposition,
    ProviderOperation,
)


_TWIRP_CODE_DETAILS: dict[str, tuple[ProviderFailureDisposition, ProviderFailureClass]] = {
    "deadline_exceeded": ("retryable", "timeout"),
    "resource_exhausted": ("retryable", "rate_limited"),
    "unavailable": ("retryable", "unavailable"),
    "internal": ("retryable", "unavailable"),
    "unauthenticated": ("terminal", "authentication"),
    "permission_denied": ("terminal", "authentication"),
    "already_exists": ("terminal", "conflict"),
    "aborted": ("terminal", "conflict"),
    "not_found": ("terminal", "not_found"),
    "invalid_argument": ("terminal", "validation"),
    "malformed": ("terminal", "validation"),
    "failed_precondition": ("terminal", "validation"),
    "out_of_range": ("terminal", "validation"),
    "bad_route": ("terminal", "validation"),
    "unimplemented": ("terminal", "validation"),
    "unknown": ("retryable", "unknown"),
    "canceled": ("terminal", "unknown"),
    "dataloss": ("terminal", "unknown"),
}
_TWIRP_STATUS_DETAILS: dict[int, tuple[ProviderFailureDisposition, ProviderFailureClass]] = {
    408: ("retryable", "timeout"),
    504: ("retryable", "timeout"),
    429: ("retryable", "rate_limited"),
    401: ("terminal", "authentication"),
    403: ("terminal", "authentication"),
    404: ("terminal", "not_found"),
    409: ("terminal", "conflict"),
    400: ("terminal", "validation"),
    405: ("terminal", "validation"),
    415: ("terminal", "validation"),
    422: ("terminal", "validation"),
}
_DEFINITELY_NOT_STARTED_CODES = frozenset(
    {
        "invalid_argument",
        "malformed",
        "bad_route",
        "not_found",
        "failed_precondition",
        "out_of_range",
        "unimplemented",
        "unauthenticated",
        "permission_denied",
    }
)
_DEFINITELY_NOT_STARTED_STATUSES = frozenset({400, 401, 403, 404, 405, 415, 422})


def livekit_failure_from_exception(
    error: api.TwirpError | TimeoutError | ConnectionError | OSError,
    *,
    operation: ProviderOperation,
    context: Mapping[str, str] | None = None,
) -> ProviderFailure:
    """Translate only LiveKit SDK and transport failures into safe failures."""
    details: tuple[ProviderFailureDisposition, ProviderFailureClass] | None
    if isinstance(error, api.TwirpError):
        details = _TWIRP_CODE_DETAILS.get(error.code)
        if details is None:
            details = _TWIRP_STATUS_DETAILS.get(error.status)
        if details is None:
            if 500 <= error.status < 600:
                details = ("retryable", "unavailable")
            elif 400 <= error.status < 500:
                details = ("terminal", "validation")
            else:
                details = ("retryable", "unknown")
    elif isinstance(error, TimeoutError):
        details = ("retryable", "timeout")
    else:
        details = ("retryable", "unavailable")
    assert details is not None
    disposition, error_class = details
    return ProviderFailure(
        provider="livekit",
        operation=operation,
        disposition=disposition,
        error_class=error_class,
        context=context,
    )


def livekit_start_failure_context(
    error: api.TwirpError | TimeoutError | ConnectionError | OSError,
) -> dict[str, str]:
    """Return the bounded start result known before/after provider acceptance."""
    if isinstance(error, api.TwirpError) and (
        error.code in _DEFINITELY_NOT_STARTED_CODES
        or error.status in _DEFINITELY_NOT_STARTED_STATUSES
    ):
        return {"start_outcome": "not_started"}
    return {"start_outcome": "unknown"}
