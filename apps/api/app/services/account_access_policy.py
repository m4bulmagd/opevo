from typing import Literal

from app.models.user import User


AccountBlockerCode = Literal["account_deactivating", "account_inactive"]


class AccountStateBlockedError(RuntimeError):
    def __init__(self, code: AccountBlockerCode) -> None:
        super().__init__(code)
        self.code = code


class AccountLifecycleGenerationMismatchError(RuntimeError):
    pass


def require_active_account(user: User) -> None:
    if user.status == "deactivating":
        raise AccountStateBlockedError("account_deactivating")
    if user.status == "inactive":
        raise AccountStateBlockedError("account_inactive")
    if user.status != "active":
        raise AccountStateBlockedError("account_inactive")


def require_current_account_lifecycle(
    user: User,
    *,
    lifecycle_generation: int,
) -> None:
    require_active_account(user)
    if user.lifecycle_generation != lifecycle_generation:
        raise AccountLifecycleGenerationMismatchError
