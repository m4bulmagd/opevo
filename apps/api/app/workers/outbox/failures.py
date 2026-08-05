from app.core.provider_failures import ProviderFailure
from app.services.outbox_service import OutboxPayloadError


SAFE_OUTBOX_ERROR_CODES = frozenset(
    {
        "provider_retryable",
        "provider_terminal",
        "internal_defect",
        "unsupported_topic",
        "invalid_payload",
        "handler_configuration",
        "dispatch_ineligible",
        "dispatch_conflict",
        "dispatch_configuration",
        "summary_stale",
        "recording_unresolved",
        "recording_provider_unavailable",
        "recording_storage_unavailable",
        "recording_identity_mismatch",
        "recording_identity_conflict",
        "recording_legacy_incomplete",
        "account_call_draining",
        "subscription_authentication",
        "subscription_contract",
        "telephony_authentication",
        "telephony_release_conflict",
        "provider_contract",
    }
)


class OutboxDeliveryError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        *,
        retryable: bool,
        exhaustible: bool = True,
    ) -> None:
        if error_code not in SAFE_OUTBOX_ERROR_CODES:
            raise ValueError("Unsafe outbox error code")
        if not retryable and not exhaustible:
            raise ValueError("Non-retryable outbox errors must be exhaustible")
        super().__init__(error_code)
        self.error_code = error_code
        self.retryable = retryable
        self.exhaustible = exhaustible


def provider_failure_delivery_error(error: ProviderFailure) -> OutboxDeliveryError:
    return OutboxDeliveryError(
        "provider_retryable" if error.retryable else "provider_terminal",
        retryable=error.retryable,
    )


def _classify_error(error: Exception) -> tuple[str, bool, bool]:
    if isinstance(error, OutboxDeliveryError):
        return error.error_code, error.retryable, error.exhaustible
    if isinstance(error, OutboxPayloadError):
        return "invalid_payload", False, True
    if isinstance(error, ProviderFailure):
        delivery_error = provider_failure_delivery_error(error)
        return (
            delivery_error.error_code,
            delivery_error.retryable,
            delivery_error.exhaustible,
        )
    return "internal_defect", False, True


def _outbox_error_class(error_code: str) -> str:
    return {
        "provider_retryable": "unavailable",
        "provider_terminal": "unknown",
        "internal_defect": "unknown",
        "unsupported_topic": "validation",
        "invalid_payload": "validation",
        "handler_configuration": "validation",
        "dispatch_ineligible": "validation",
        "dispatch_conflict": "conflict",
        "dispatch_configuration": "validation",
        "summary_stale": "conflict",
        "recording_unresolved": "unknown",
        "recording_provider_unavailable": "unavailable",
        "recording_storage_unavailable": "unavailable",
        "recording_identity_mismatch": "validation",
        "recording_identity_conflict": "conflict",
        "recording_legacy_incomplete": "validation",
        "account_call_draining": "unavailable",
        "subscription_authentication": "authentication",
        "subscription_contract": "validation",
        "telephony_authentication": "authentication",
        "telephony_release_conflict": "conflict",
        "provider_contract": "validation",
    }.get(error_code, "unknown")
