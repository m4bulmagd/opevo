from dataclasses import fields
from uuid import uuid4

import pytest

from app.auth.domain import AuthenticatedUser, ExternalIdentity, ExternalUserProfile
from app.auth.providers.local import (
    LOCAL_USER_EMAIL,
    LOCAL_USER_EXTERNAL_ID,
    LocalAuthProvider,
)
from app.core.auth_failures import TokenRejected


LOCAL_TOKEN = "provider-neutral-local-token"


def test_authenticated_user_exposes_only_the_internal_user_id() -> None:
    internal_user_id = uuid4()

    authenticated_user = AuthenticatedUser(internal_user_id=internal_user_id)

    assert authenticated_user.internal_user_id == internal_user_id
    assert [field.name for field in fields(authenticated_user)] == [
        "internal_user_id"
    ]


@pytest.mark.parametrize(
    ("factory", "expected_message"),
    [
        (
            lambda: ExternalIdentity(external_user_id=""),
            "external_user_id must be non-empty",
        ),
        (
            lambda: ExternalUserProfile(external_user_id="", email="owner@example.com"),
            "external_user_id must be non-empty",
        ),
        (
            lambda: ExternalUserProfile(external_user_id="provider-user", email=""),
            "email must be non-empty",
        ),
        (
            lambda: ExternalIdentity(
                external_user_id="provider-user",
                bootstrap_profile=ExternalUserProfile(
                    external_user_id="different-provider-user",
                    email="owner@example.com",
                ),
            ),
            "bootstrap profile must describe the same external user",
        ),
    ],
)
def test_external_authentication_records_reject_empty_invariants(
    factory,
    expected_message: str,
) -> None:
    with pytest.raises(ValueError, match=expected_message):
        factory()


@pytest.mark.anyio
async def test_local_adapter_returns_a_trusted_normalized_bootstrap_profile() -> None:
    provider = LocalAuthProvider(token=LOCAL_TOKEN)

    identity = await provider.verify_token(LOCAL_TOKEN)

    assert identity == ExternalIdentity(
        external_user_id=LOCAL_USER_EXTERNAL_ID,
        bootstrap_profile=ExternalUserProfile(
            external_user_id=LOCAL_USER_EXTERNAL_ID,
            email=LOCAL_USER_EMAIL,
        ),
    )
    assert [field.name for field in fields(identity)] == [
        "external_user_id",
        "bootstrap_profile",
    ]


@pytest.mark.anyio
async def test_local_adapter_rejects_a_wrong_token_without_exposing_it() -> None:
    provider = LocalAuthProvider(token=LOCAL_TOKEN)
    rejected_token = "wrong-provider-neutral-token"

    with pytest.raises(TokenRejected) as error:
        await provider.verify_token(rejected_token)

    assert error.value.reason == "signature"
    assert LOCAL_TOKEN not in str(error.value)
    assert rejected_token not in str(error.value)
