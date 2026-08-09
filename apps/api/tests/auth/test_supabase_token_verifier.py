from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.auth.domain import ExternalUserProfile
from app.auth.factory import build_auth_provider
from app.auth.jwks import JwksSigningKeyResolver
from app.auth.jwks import StaticSigningKeyResolver
from app.auth.providers.supabase import SupabaseAuthProvider
from app.core.auth_failures import TokenRejected
from app.core.runtime_validation import validate_api_runtime


ISSUER = "https://project.supabase.co/auth/v1"
AUDIENCE = "authenticated"


class RecordingObservability:
    def __init__(self) -> None:
        self.verifications: list[tuple[str, str]] = []

    def record_auth_verification(self, outcome: str, reason: str) -> None:
        self.verifications.append((outcome, reason))


@pytest.fixture
def key_material() -> tuple[str, str]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def _token(private_key: str, **overrides: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "2f0746a1-ea79-4e85-b481-8eb25e30c051",
        "email": "member@example.com",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "role": "authenticated",
        "is_anonymous": False,
    }
    claims.update(overrides)
    return jwt.encode(
        claims,
        private_key,
        algorithm="ES256",
        headers={"kid": "supabase-signing-key"},
    )


def _provider(public_key: str, observability: RecordingObservability):
    return SupabaseAuthProvider(
        issuer=ISSUER,
        audience=AUDIENCE,
        signing_key_resolver=StaticSigningKeyResolver(public_key),
        observability=observability,  # type: ignore[arg-type]
    )


@pytest.mark.anyio
async def test_verified_supabase_token_supplies_trusted_bootstrap_profile(
    key_material: tuple[str, str],
) -> None:
    private_key, public_key = key_material
    observability = RecordingObservability()

    identity = await _provider(public_key, observability).verify_token(
        _token(private_key)
    )

    assert identity.external_user_id == "2f0746a1-ea79-4e85-b481-8eb25e30c051"
    assert identity.bootstrap_profile == ExternalUserProfile(
        external_user_id=identity.external_user_id,
        email="member@example.com",
    )
    assert observability.verifications == [("accepted", "none")]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("claim_overrides", "reason"),
    [
        ({"email": ""}, "claims"),
        ({"sub": ""}, "claims"),
        ({"iss": "https://attacker.example/auth/v1"}, "issuer"),
        ({"aud": "anon"}, "audience"),
        ({"exp": 0}, "claims"),
        ({"iat": "not-a-timestamp"}, "claims"),
        ({"role": "service_role"}, "claims"),
        ({"is_anonymous": True}, "claims"),
        ({"is_anonymous": "false"}, "claims"),
    ],
)
async def test_supabase_rejects_untrusted_identity_claims(
    key_material: tuple[str, str],
    claim_overrides: dict[str, object],
    reason: str,
) -> None:
    private_key, public_key = key_material
    observability = RecordingObservability()

    with pytest.raises(TokenRejected) as error:
        await _provider(public_key, observability).verify_token(
            _token(private_key, **claim_overrides)
        )

    assert error.value.reason == reason
    assert observability.verifications == [("rejected", reason)]


@pytest.mark.anyio
async def test_supabase_rejects_a_token_with_the_wrong_signature(
    key_material: tuple[str, str],
) -> None:
    _, public_key = key_material
    other_private_key = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    observability = RecordingObservability()

    with pytest.raises(TokenRejected) as error:
        await _provider(public_key, observability).verify_token(
            _token(other_private_key)
        )

    assert error.value.reason == "signature"
    assert observability.verifications == [("rejected", "signature")]


@pytest.mark.anyio
async def test_factory_builds_supabase_from_selected_provider(
    settings,
) -> None:
    observability = RecordingObservability()
    configured = settings.model_copy(
        update={
            "auth_provider": "supabase",
            "supabase_url": "https://project.supabase.co",
            "clerk_issuer": "",
            "clerk_jwt_key": None,
            "clerk_jwks_url": None,
        }
    )

    validate_api_runtime(configured)
    provider = build_auth_provider(
        settings=configured,
        observability=observability,  # type: ignore[arg-type]
    )

    assert isinstance(provider, SupabaseAuthProvider)
    assert isinstance(provider._signing_key_resolver, JwksSigningKeyResolver)
    assert provider._signing_key_resolver._jwks_url == (
        "https://project.supabase.co/auth/v1/.well-known/jwks.json"
    )
    await provider.aclose()


@pytest.mark.parametrize(
    "supabase_url",
    ["", "https://project.supabase.co/path", "https://user@example.com"],
)
def test_selected_supabase_provider_rejects_invalid_project_url(
    settings,
    supabase_url: str,
) -> None:
    configured = settings.model_copy(
        update={"auth_provider": "supabase", "supabase_url": supabase_url}
    )

    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        validate_api_runtime(configured)
