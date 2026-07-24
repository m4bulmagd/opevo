import pytest

from app.models.user import User
from app.services.account_access_policy import (
    AccountStateBlockedError,
    require_active_account,
)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("deactivating", "account_deactivating"),
        ("inactive", "account_inactive"),
    ],
)
def test_account_state_blocks_owner_mutations(status: str, code: str) -> None:
    user = User(
        clerk_user_id=f"user_{status}",
        email=f"{status}@example.invalid",
        status=status,
    )

    with pytest.raises(AccountStateBlockedError) as raised:
        require_active_account(user)

    assert raised.value.code == code


def test_active_account_allows_owner_mutations() -> None:
    user = User(
        clerk_user_id="user_active_policy",
        email="active-policy@example.invalid",
        status="active",
    )

    require_active_account(user)


def test_unknown_account_state_fails_closed_as_inactive() -> None:
    user = User(
        clerk_user_id="user_unknown_policy",
        email="unknown-policy@example.invalid",
        status="unknown",
    )

    with pytest.raises(AccountStateBlockedError) as raised:
        require_active_account(user)

    assert raised.value.code == "account_inactive"
