from collections.abc import Sequence

from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.provider_cleanup_operation import ProviderCleanupOperation


class UnresolvedProviderWorkError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def unresolved_provider_work_blocker(
    *,
    cleanup_operations: Sequence[ProviderCleanupOperation],
    provisioning: PhoneNumberProvisioning | None,
    allowed_provisioning_operation_key: str | None = None,
    allow_matching_provisioning: bool = False,
) -> str | None:
    if any(
        operation.completed_at is None and operation.status == "attention_required"
        for operation in cleanup_operations
    ):
        return "deactivation_attention_required"
    if any(operation.completed_at is None for operation in cleanup_operations):
        return "reactivation_not_ready"
    if (
        provisioning is not None
        and provisioning.status == "running"
        and (
            not allow_matching_provisioning
            or provisioning.provider_operation_key != allowed_provisioning_operation_key
        )
    ):
        return "reactivation_not_ready"
    return None
