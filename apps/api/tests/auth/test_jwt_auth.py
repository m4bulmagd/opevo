import pytest
from cryptography.hazmat.primitives import serialization

from app.core.auth import ClerkAuthProvider


def test_clerk_auth_provider_accepts_valid_rs256_token(
    rs256_clerk_token_for,
) -> None:
    identity = ClerkAuthProvider().verify_token(rs256_clerk_token_for("user_active"))

    assert identity.clerk_user_id == "user_active"


def test_clerk_auth_provider_accepts_valid_rs256_token_via_jwks_url(
    monkeypatch: pytest.MonkeyPatch,
    rs256_clerk_token_for,
    clerk_key_material,
) -> None:
    monkeypatch.setenv("CLERK_JWT_KEY", "")
    monkeypatch.setenv("CLERK_JWKS_URL", "https://clerk.example.com/.well-known/jwks.json")

    from app.core.config import get_settings

    get_settings.cache_clear()

    private_key = serialization.load_pem_private_key(
        str(clerk_key_material["private_key_pem"]).encode("utf-8"),
        password=None,
    )

    class FakeSigningKey:
        def __init__(self, key) -> None:
            self.key = key

    class FakeJwkClient:
        def get_signing_key_from_jwt(self, _token: str):
            return FakeSigningKey(private_key.public_key())

    identity = ClerkAuthProvider(jwk_client=FakeJwkClient()).verify_token(
        rs256_clerk_token_for("user_active")
    )

    assert identity.clerk_user_id == "user_active"


@pytest.mark.anyio
async def test_protected_route_rejects_token_without_local_user(
    async_client,
    valid_clerk_but_missing_local_user_token,
) -> None:
    response = await async_client.get(
        "/api/agent/config",
        headers={"Authorization": f"Bearer {valid_clerk_but_missing_local_user_token}"},
    )

    assert response.status_code == 401
