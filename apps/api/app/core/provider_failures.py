from types import MappingProxyType
from typing import Literal, Mapping


ProviderFailureDisposition = Literal["retryable", "terminal"]
ProviderFailureClass = Literal[
    "timeout",
    "rate_limited",
    "unavailable",
    "authentication",
    "validation",
    "conflict",
    "not_found",
    "unknown",
]
ProviderName = Literal["telnyx", "stripe", "s3", "livekit", "gemini", "fake"]
ProviderOperation = Literal[
    "provision_number",
    "recover_provisioned_number",
    "enable_number",
    "disable_number",
    "release_number",
    "lookup_carrier",
    "cancel_subscription",
    "create_checkout_session",
    "create_portal_session",
    "upload_bytes",
    "get_download_url",
    "delete_object",
    "get_bucket_lifecycle",
    "list_dispatches",
    "create_dispatch",
    "start_recording",
    "stop_recording",
    "ensure_recording_not_running",
    "ensure_recording_stopped",
    "list_recording_egresses",
    "generate_summary",
    "validate",
]


SAFE_PROVIDER_NAMES = frozenset({"telnyx", "stripe", "s3", "livekit", "gemini", "fake"})
_SAFE_PROVIDER_DISPOSITIONS = frozenset({"retryable", "terminal"})
_SAFE_PROVIDER_CLASSES = frozenset(
    {
        "timeout",
        "rate_limited",
        "unavailable",
        "authentication",
        "validation",
        "conflict",
        "not_found",
        "unknown",
    }
)
_SAFE_PROVIDER_OPERATIONS = {
    "telnyx": frozenset(
        {
            "provision_number",
            "recover_provisioned_number",
            "enable_number",
            "disable_number",
            "release_number",
            "lookup_carrier",
        }
    ),
    "stripe": frozenset(
        {
            "cancel_subscription",
            "create_checkout_session",
            "create_portal_session",
        }
    ),
    "s3": frozenset(
        {
            "upload_bytes",
            "get_download_url",
            "delete_object",
            "get_bucket_lifecycle",
        }
    ),
    "livekit": frozenset(
        {
            "list_dispatches",
            "create_dispatch",
            "start_recording",
            "stop_recording",
            "ensure_recording_not_running",
            "ensure_recording_stopped",
            "list_recording_egresses",
        }
    ),
    "gemini": frozenset({"generate_summary"}),
    "fake": frozenset({"validate"}),
}
_SAFE_CONTEXT_VALUES = {"start_outcome": frozenset({"not_started", "unknown"})}


class ProviderFailure(RuntimeError):
    def __init__(
        self,
        *,
        provider: ProviderName,
        operation: ProviderOperation,
        disposition: ProviderFailureDisposition,
        error_class: ProviderFailureClass,
        context: Mapping[str, str] | None = None,
    ) -> None:
        if provider not in SAFE_PROVIDER_NAMES:
            raise ValueError("Unsafe provider name")
        if operation not in _SAFE_PROVIDER_OPERATIONS[provider]:
            raise ValueError("Unsafe provider operation")
        if disposition not in _SAFE_PROVIDER_DISPOSITIONS:
            raise ValueError("Unsafe provider failure disposition")
        if error_class not in _SAFE_PROVIDER_CLASSES:
            raise ValueError("Unsafe provider failure class")

        safe_context = dict(context or {})
        for key, value in safe_context.items():
            allowed_values = _SAFE_CONTEXT_VALUES.get(key)
            if allowed_values is None or value not in allowed_values:
                raise ValueError("Unsafe provider failure context")

        super().__init__("provider operation failed")
        self._provider = provider
        self._operation = operation
        self._disposition = disposition
        self._error_class = error_class
        self._context = MappingProxyType(safe_context)

    @property
    def provider(self) -> ProviderName:
        return self._provider

    @property
    def operation(self) -> ProviderOperation:
        return self._operation

    @property
    def disposition(self) -> ProviderFailureDisposition:
        return self._disposition

    @property
    def error_class(self) -> ProviderFailureClass:
        return self._error_class

    @property
    def context(self) -> Mapping[str, str]:
        return self._context

    @property
    def retryable(self) -> bool:
        return self.disposition == "retryable"


def provider_failure_from_http_status(
    *,
    provider: ProviderName,
    operation: ProviderOperation,
    status: int | None,
) -> ProviderFailure:
    if status == 408:
        disposition: ProviderFailureDisposition = "retryable"
        error_class: ProviderFailureClass = "timeout"
    elif status == 429:
        disposition = "retryable"
        error_class = "rate_limited"
    elif status == 503:
        disposition = "retryable"
        error_class = "unavailable"
    elif status in {401, 403}:
        disposition = "terminal"
        error_class = "authentication"
    elif status == 404:
        disposition = "terminal"
        error_class = "not_found"
    elif status == 409:
        disposition = "terminal"
        error_class = "conflict"
    elif isinstance(status, int) and 400 <= status < 500:
        disposition = "terminal"
        error_class = "validation"
    elif isinstance(status, int) and 500 <= status < 600:
        disposition = "retryable"
        error_class = "unavailable"
    else:
        disposition = "terminal"
        error_class = "unknown"

    return ProviderFailure(
        provider=provider,
        operation=operation,
        disposition=disposition,
        error_class=error_class,
    )
