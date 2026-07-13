import time
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from app.core.auth import ClerkAuthProvider
from app.core.dispatch_token import create_dispatch_token, verify_dispatch_token


DISPATCH_SECRET = "dispatch-test-secret-with-enough-entropy-for-all-hmac-tests"


def _configure_dispatch_tokens(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ttl: int | None = None,
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_JWT_SECRET", DISPATCH_SECRET)
    if ttl is None:
        monkeypatch.delenv("AGENT_DISPATCH_JWT_TTL_SECONDS", raising=False)
    else:
        monkeypatch.setenv("AGENT_DISPATCH_JWT_TTL_SECONDS", str(ttl))

    from app.core.config import get_settings

    get_settings.cache_clear()


def _encode_dispatch_payload(payload: dict, *, algorithm: str = "HS256") -> str:
    return jwt.encode(payload, DISPATCH_SECRET, algorithm=algorithm)


def _valid_dispatch_payload() -> dict:
    now = int(time.time())
    return {
        "call_id": str(uuid4()),
        "user_id": str(uuid4()),
        "agent_config_id": str(uuid4()),
        "iat": now,
        "exp": now + 7200,
    }


def test_dispatch_token_contains_all_required_call_scoped_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_dispatch_tokens(monkeypatch)
    call_id = str(uuid4())
    user_id = str(uuid4())
    agent_config_id = str(uuid4())

    token = create_dispatch_token(
        call_id,
        user_id,
        agent_config_id,
    )
    payload = jwt.decode(token, DISPATCH_SECRET, algorithms=["HS256"])

    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    assert payload["call_id"] == call_id
    assert payload["user_id"] == user_id
    assert payload["agent_config_id"] == agent_config_id
    assert isinstance(payload["iat"], int)
    assert payload["exp"] - payload["iat"] == 7200


def test_create_dispatch_token_fails_safely_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_DISPATCH_JWT_SECRET", raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()

    with pytest.raises(ValueError, match="configured"):
        create_dispatch_token(
            call_id=str(uuid4()),
            user_id=str(uuid4()),
            agent_config_id=str(uuid4()),
        )


@pytest.mark.parametrize(
    "unsafe_secret",
    [
        "too-short",
        "replace-with-a-long-random-secret",
        "CHANGE-ME-CHANGE-ME-CHANGE-ME-CHANGE-ME",
    ],
)
def test_dispatch_token_sign_and_verify_reject_unsafe_hmac_secrets(
    monkeypatch: pytest.MonkeyPatch,
    unsafe_secret: str,
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_JWT_SECRET", unsafe_secret)
    from app.core.config import get_settings

    get_settings.cache_clear()
    payload = _valid_dispatch_payload()
    token = jwt.encode(payload, DISPATCH_SECRET, algorithm="HS256")

    with pytest.raises(ValueError, match="configured safely"):
        create_dispatch_token(
            call_id=payload["call_id"],
            user_id=payload["user_id"],
            agent_config_id=payload["agent_config_id"],
        )
    with pytest.raises(ValueError, match="configured safely"):
        verify_dispatch_token(token, expected_call_id=payload["call_id"])


@pytest.mark.parametrize(
    "missing_claim",
    ["call_id", "user_id", "agent_config_id", "iat", "exp"],
)
def test_verify_dispatch_token_rejects_every_missing_required_claim(
    monkeypatch: pytest.MonkeyPatch,
    missing_claim: str,
) -> None:
    _configure_dispatch_tokens(monkeypatch)
    payload = _valid_dispatch_payload()
    expected_call_id = payload["call_id"]
    payload.pop(missing_claim)

    with pytest.raises(ValueError, match="Invalid dispatch token"):
        verify_dispatch_token(
            _encode_dispatch_payload(payload),
            expected_call_id=expected_call_id,
        )


@pytest.mark.parametrize(
    "token",
    ["not-a-jwt", "", "header.payload.signature"],
)
def test_verify_dispatch_token_rejects_malformed_tokens(
    monkeypatch: pytest.MonkeyPatch,
    token: str,
) -> None:
    _configure_dispatch_tokens(monkeypatch)

    with pytest.raises(ValueError, match="Invalid dispatch token"):
        verify_dispatch_token(token, expected_call_id=str(uuid4()))


def test_verify_dispatch_token_rejects_expired_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_dispatch_tokens(monkeypatch)
    payload = _valid_dispatch_payload()
    payload["iat"] = int(time.time()) - 10
    payload["exp"] = int(time.time()) - 1

    with pytest.raises(ValueError, match="Invalid dispatch token"):
        verify_dispatch_token(
            _encode_dispatch_payload(payload),
            expected_call_id=payload["call_id"],
        )


def test_verify_dispatch_token_rejects_non_hs256_algorithm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_dispatch_tokens(monkeypatch)
    payload = _valid_dispatch_payload()

    with pytest.raises(ValueError, match="Invalid dispatch token"):
        verify_dispatch_token(
            _encode_dispatch_payload(payload, algorithm="HS384"),
            expected_call_id=payload["call_id"],
        )


def test_verify_dispatch_token_rejects_wrong_call_without_echoing_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_dispatch_tokens(monkeypatch)
    payload = _valid_dispatch_payload()
    wrong_call_id = str(uuid4())
    token = _encode_dispatch_payload(payload)

    with pytest.raises(ValueError) as exc_info:
        verify_dispatch_token(token, expected_call_id=wrong_call_id)

    message = str(exc_info.value)
    assert payload["call_id"] not in message
    assert wrong_call_id not in message
    assert token not in message


def test_verify_dispatch_token_rejects_wrong_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_dispatch_tokens(monkeypatch)
    payload = _valid_dispatch_payload()

    with pytest.raises(ValueError, match="Invalid dispatch token"):
        verify_dispatch_token(
            _encode_dispatch_payload(payload),
            payload["call_id"],
            expected_user_id=str(uuid4()),
        )


def test_verify_dispatch_token_rejects_invalid_identifier_claim_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_dispatch_tokens(monkeypatch)
    payload = _valid_dispatch_payload()
    payload["agent_config_id"] = 123

    with pytest.raises(ValueError, match="Invalid dispatch token"):
        verify_dispatch_token(
            _encode_dispatch_payload(payload),
            expected_call_id=payload["call_id"],
        )


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
