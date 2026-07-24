from typing import Literal

from app.models.user import User


AccountBlockerCode = Literal["account_deactivating", "account_inactive"]


class AccountStateBlockedError(RuntimeError):
    def __init__(self, code: AccountBlockerCode) -> None:
        super().__init__(code)
        self.code = code


def require_active_account(user: User) -> None:
    if user.status == "deactivating":
        raise AccountStateBlockedError("account_deactivating")
    if user.status == "inactive":
        raise AccountStateBlockedError("account_inactive")
    if user.status != "active":
        raise AccountStateBlockedError("account_inactive")
