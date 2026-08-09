from dataclasses import fields

import pytest

from app.auth.domain import ExternalIdentity, ExternalUserProfile
from app.auth.failures import UserNotProvisioned
from app.auth.providers.base import AuthProvider
from app.models.user import User
from app.services.authentication_service import AuthenticationService


class StaticIdentityProvider(AuthProvider):
    def __init__(self, identity: ExternalIdentity) -> None:
        self.identity = identity
        self.tokens: list[str] = []

    async def verify_token(self, token: str) -> ExternalIdentity:
        self.tokens.append(token)
        return self.identity


@pytest.mark.anyio
async def test_authentication_resolves_an_existing_user_to_only_the_internal_id(
    db_session,
) -> None:
    user = User(
        external_user_id="provider-existing-user",
        email="existing@example.com",
    )
    db_session.add(user)
    await db_session.commit()
    provider = StaticIdentityProvider(
        ExternalIdentity(external_user_id="provider-existing-user")
    )

    authenticated_user = await AuthenticationService(
        session=db_session,
        auth_provider=provider,
    ).authenticate("verified-access-token")

    assert authenticated_user.internal_user_id == user.id
    assert [field.name for field in fields(authenticated_user)] == [
        "internal_user_id"
    ]
    assert provider.tokens == ["verified-access-token"]


@pytest.mark.anyio
async def test_authentication_rejects_an_unprovisioned_external_identity(
    db_session,
) -> None:
    provider = StaticIdentityProvider(
        ExternalIdentity(external_user_id="provider-unprovisioned-user")
    )

    with pytest.raises(UserNotProvisioned):
        await AuthenticationService(
            session=db_session,
            auth_provider=provider,
        ).authenticate("verified-access-token")


@pytest.mark.anyio
async def test_authentication_provisions_only_an_adapter_trusted_profile(
    db_session,
) -> None:
    profile = ExternalUserProfile(
        external_user_id="provider-bootstrap-user",
        email="bootstrap@example.com",
    )
    provider = StaticIdentityProvider(
        ExternalIdentity(
            external_user_id=profile.external_user_id,
            bootstrap_profile=profile,
        )
    )

    authenticated_user = await AuthenticationService(
        session=db_session,
        auth_provider=provider,
    ).authenticate("verified-access-token")

    provisioned_user = await db_session.get(User, authenticated_user.internal_user_id)
    assert provisioned_user is not None
    assert provisioned_user.external_user_id == profile.external_user_id
    assert provisioned_user.email == profile.email
