import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from app.core.auth import ClerkAuthProvider, UserIdentity
from app.core.auth_failures import AuthenticationUnavailable, TokenRejected
from app.core.clerk_jwks import JwksSigningKeyResolver, StaticSigningKeyResolver


APP_ORIGIN = "https://app.example.com"
ADMIN_ORIGIN = "https://admin.example.com"
JWKS_URL = "https://clerk.example.com/.well-known/jwks.json"


class RecordingObservability:
    def __init__(self) -> None:
        self.verifications: list[tuple[str, str]] = []

    def record_auth_verification(self, outcome: str, reason: str) -> None:
        self.verifications.append((outcome, reason))

    def record_jwks_refresh(self, outcome: str, duration_seconds: float) -> None:
        del outcome, duration_seconds

    def record_jwks_coalesced_wait(self) -> None:
        pass

    def record_jwks_stale_key_use(self) -> None:
        pass

    def record_jwks_refresh_cooldown(self, outcome: str) -> None:
        del outcome


class JsonTransport(httpx.AsyncBaseTransport):
    def __init__(self, document: dict[str, object]) -> None:
        self.document = document

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self.document, request=request)


@pytest.fixture
def recording_observability() -> RecordingObservability:
    return RecordingObservability()


@pytest.fixture
def clerk_provider(
    settings,
    clerk_key_material: dict[str, str | bytes],
    recording_observability: RecordingObservability,
) -> ClerkAuthProvider:
    return ClerkAuthProvider(
        settings=settings,
        authorized_parties=frozenset({APP_ORIGIN}),
        signing_key_resolver=StaticSigningKeyResolver(
            str(clerk_key_material["public_key_pem"])
        ),
        observability=recording_observability,  # type: ignore[arg-type]
    )


@pytest.fixture
def token_with_claims(rs256_clerk_token_for) -> Callable[..., str]:
    def _build(**claims: object) -> str:
        return rs256_clerk_token_for("user_active", claims=claims)

    return _build


@pytest.fixture
def token_without_claim(
    clerk_key_material: dict[str, str | bytes],
) -> Callable[[str], str]:
    private_key_pem = str(clerk_key_material["private_key_pem"])
    complete_payload: dict[str, object] = {
        "sub": "user_active",
        "iss": "https://clerk.example.com",
        "exp": 4102444800,
        "nbf": 0,
        "azp": APP_ORIGIN,
    }

    def _build(claim: str) -> str:
        payload = complete_payload.copy()
        payload.pop(claim)
        return jwt.encode(payload, private_key_pem, algorithm="RS256")

    return _build


@pytest.mark.anyio
async def test_static_verifier_accepts_complete_rs256_token(
    clerk_provider,
    rs256_clerk_token_for,
) -> None:
    identity = await clerk_provider.verify_token(rs256_clerk_token_for("user_active"))

    assert identity == UserIdentity(clerk_user_id="user_active")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "azp",
    [
        None,
        "",
        7,
        [APP_ORIGIN],
        "HTTPS://app.example.com",
        "https://app.example.com/",
        "https://app.example.com.evil.test",
        "https://evil.test/app.example.com",
        "prefix-https://app.example.com",
    ],
)
async def test_verifier_rejects_every_nonexact_authorized_party(
    clerk_provider,
    token_with_claims,
    azp: object,
) -> None:
    with pytest.raises(TokenRejected) as exc_info:
        await clerk_provider.verify_token(token_with_claims(azp=azp))

    assert exc_info.value.reason == "authorized_party"


@pytest.mark.anyio
@pytest.mark.parametrize("claim", ["exp", "nbf", "sub", "azp"])
async def test_verifier_requires_each_security_claim(
    clerk_provider,
    token_without_claim,
    claim: str,
) -> None:
    with pytest.raises(TokenRejected):
        await clerk_provider.verify_token(token_without_claim(claim))


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"exp": int(time.time()) - 1}, "claims"),
        ({"nbf": int(time.time()) + 3600}, "claims"),
        ({"sub": ""}, "claims"),
        ({"sub": 7}, "claims"),
        ({"iss": "https://wrong-issuer.example.com"}, "issuer"),
    ],
)
async def test_verifier_maps_invalid_security_claims_to_bounded_reasons(
    clerk_provider,
    token_with_claims,
    claims: dict[str, object],
    reason: str,
) -> None:
    with pytest.raises(TokenRejected) as exc_info:
        await clerk_provider.verify_token(token_with_claims(**claims))

    assert exc_info.value.reason == reason


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("claim", "value"),
    [
        pytest.param("exp", None, id="exp-null"),
        pytest.param("exp", True, id="exp-bool"),
        pytest.param("exp", [], id="exp-list"),
        pytest.param(
            "exp",
            {"private": "NUMERIC_DATE_SENTINEL"},
            id="exp-object",
        ),
        pytest.param("exp", "NUMERIC_DATE_SENTINEL", id="exp-string"),
        pytest.param("exp", float("inf"), id="exp-positive-overflow"),
        pytest.param("exp", float("-inf"), id="exp-negative-overflow"),
        pytest.param("exp", float("nan"), id="exp-not-a-number"),
        pytest.param("nbf", None, id="nbf-null"),
        pytest.param("nbf", False, id="nbf-bool"),
        pytest.param("nbf", [], id="nbf-list"),
        pytest.param(
            "nbf",
            {"private": "NUMERIC_DATE_SENTINEL"},
            id="nbf-object",
        ),
        pytest.param("nbf", "NUMERIC_DATE_SENTINEL", id="nbf-string"),
        pytest.param("nbf", float("inf"), id="nbf-positive-overflow"),
        pytest.param("nbf", float("-inf"), id="nbf-negative-overflow"),
        pytest.param("nbf", float("nan"), id="nbf-not-a-number"),
    ],
)
async def test_verifier_normalizes_hostile_numeric_date_types_without_leakage(
    clerk_provider,
    token_with_claims,
    recording_observability: RecordingObservability,
    caplog: pytest.LogCaptureFixture,
    claim: str,
    value: object,
) -> None:
    token = token_with_claims(
        **{claim: value, "private": "CLAIM_SENTINEL"}
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(Exception) as exc_info:
            await clerk_provider.verify_token(token)

    assert type(exc_info.value) is TokenRejected
    assert exc_info.value.reason == "claims"
    assert recording_observability.verifications == [("rejected", "claims")]
    exposed = " ".join(
        (
            str(exc_info.value),
            repr(exc_info.value),
            caplog.text,
            repr(recording_observability.verifications),
        )
    )
    for sentinel in ("NUMERIC_DATE_SENTINEL", "CLAIM_SENTINEL", token):
        assert sentinel not in exposed


@pytest.mark.anyio
async def test_verifier_enforces_configured_audience(
    settings,
    clerk_key_material: dict[str, str | bytes],
    recording_observability: RecordingObservability,
    rs256_clerk_token_for,
) -> None:
    provider = ClerkAuthProvider(
        settings=settings.model_copy(update={"clerk_audience": "expected-audience"}),
        authorized_parties=frozenset({APP_ORIGIN}),
        signing_key_resolver=StaticSigningKeyResolver(
            str(clerk_key_material["public_key_pem"])
        ),
        observability=recording_observability,  # type: ignore[arg-type]
    )
    token = rs256_clerk_token_for(
        "user_active", claims={"aud": "different-audience"}
    )

    with pytest.raises(TokenRejected) as exc_info:
        await provider.verify_token(token)

    assert exc_info.value.reason == "audience"


@pytest.mark.anyio
async def test_verifier_disables_audience_check_when_not_configured(
    clerk_provider,
    rs256_clerk_token_for,
) -> None:
    token = rs256_clerk_token_for(
        "user_active", claims={"aud": "unconfigured-audience"}
    )

    identity = await clerk_provider.verify_token(token)

    assert identity == UserIdentity(clerk_user_id="user_active")


def _unsigned_token(payload: dict[str, object], *, algorithm: str = "none") -> str:
    header = jwt.utils.base64url_encode(
        json.dumps({"alg": algorithm, "typ": "JWT"}).encode()
    ).decode()
    body = jwt.utils.base64url_encode(json.dumps(payload).encode()).decode()
    return f"{header}.{body}."


@pytest.mark.anyio
@pytest.mark.parametrize("algorithm", ["HS256", "none"])
async def test_verifier_rejects_non_rs256_algorithms(
    clerk_provider,
    algorithm: str,
) -> None:
    payload = {
        "sub": "user_active",
        "iss": "https://clerk.example.com",
        "exp": 4102444800,
        "nbf": 0,
        "azp": APP_ORIGIN,
    }
    token = (
        jwt.encode(
            payload,
            "hmac-test-secret-with-at-least-32-bytes",
            algorithm="HS256",
        )
        if algorithm == "HS256"
        else _unsigned_token(payload)
    )

    with pytest.raises(TokenRejected) as exc_info:
        await clerk_provider.verify_token(token)

    assert exc_info.value.reason == "algorithm"


@pytest.mark.anyio
async def test_verifier_rejects_header_algorithm_confusion(
    clerk_provider,
    rs256_clerk_token_for,
) -> None:
    token = rs256_clerk_token_for("user_active")
    _, payload, signature = token.split(".")
    confused_header = jwt.utils.base64url_encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
    ).decode()

    with pytest.raises(TokenRejected) as exc_info:
        await clerk_provider.verify_token(f"{confused_header}.{payload}.{signature}")

    assert exc_info.value.reason == "algorithm"


@pytest.mark.anyio
async def test_verifier_rejects_invalid_signature(
    clerk_provider,
    rs256_clerk_token_for,
) -> None:
    token = rs256_clerk_token_for("user_active")
    header, payload, signature = token.split(".")
    replacement = "A" if signature[0] != "A" else "B"

    with pytest.raises(TokenRejected) as exc_info:
        await clerk_provider.verify_token(
            f"{header}.{payload}.{replacement}{signature[1:]}"
        )

    assert exc_info.value.reason == "signature"


@pytest.mark.anyio
@pytest.mark.parametrize("token", ["not-a-token", "", "header.payload.signature"])
async def test_verifier_rejects_malformed_token(
    clerk_provider,
    token: str,
) -> None:
    with pytest.raises(TokenRejected) as exc_info:
        await clerk_provider.verify_token(token)

    assert exc_info.value.reason == "malformed"


@pytest.mark.anyio
async def test_verifier_accepts_either_exact_approved_origin(
    settings,
    clerk_key_material: dict[str, str | bytes],
    recording_observability: RecordingObservability,
    rs256_clerk_token_for,
) -> None:
    provider = ClerkAuthProvider(
        settings=settings,
        authorized_parties=frozenset({APP_ORIGIN, ADMIN_ORIGIN}),
        signing_key_resolver=StaticSigningKeyResolver(
            str(clerk_key_material["public_key_pem"])
        ),
        observability=recording_observability,  # type: ignore[arg-type]
    )

    for origin in (APP_ORIGIN, ADMIN_ORIGIN):
        identity = await provider.verify_token(
            rs256_clerk_token_for("user_active", claims={"azp": origin})
        )
        assert identity == UserIdentity(clerk_user_id="user_active")


@pytest.mark.anyio
async def test_verification_failures_never_leak_token_claim_or_provider_details(
    clerk_provider,
    recording_observability: RecordingObservability,
    rs256_clerk_token_for,
    caplog: pytest.LogCaptureFixture,
) -> None:
    subject = "SUBJECT_SENTINEL"
    authorized_party = "PROVIDER_SENTINEL"
    token = rs256_clerk_token_for(
        subject,
        claims={"azp": authorized_party, "private": "CLAIM_SENTINEL"},
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(TokenRejected) as exc_info:
            await clerk_provider.verify_token(token)

    exposed = " ".join(
        [
            str(exc_info.value),
            repr(exc_info.value),
            caplog.text,
            repr(recording_observability.verifications),
        ]
    )
    assert exc_info.value.reason == "authorized_party"
    assert recording_observability.verifications == [
        ("rejected", "authorized_party")
    ]
    for sentinel in (subject, authorized_party, "CLAIM_SENTINEL", token):
        assert sentinel not in exposed


@pytest.mark.anyio
async def test_verifier_records_one_metric_for_each_outcome(
    clerk_provider,
    recording_observability: RecordingObservability,
    rs256_clerk_token_for,
) -> None:
    await clerk_provider.verify_token(rs256_clerk_token_for("user_active"))
    with pytest.raises(TokenRejected):
        await clerk_provider.verify_token("malformed")

    assert recording_observability.verifications == [
        ("accepted", "none"),
        ("rejected", "malformed"),
    ]


@pytest.mark.anyio
async def test_verifier_preserves_typed_resolver_outage_and_records_it(
    settings,
    recording_observability: RecordingObservability,
) -> None:
    class UnavailableResolver:
        async def resolve_key(self, token: str) -> Any:
            del token
            raise AuthenticationUnavailable("jwks_timeout")

        async def aclose(self) -> None:
            pass

    provider = ClerkAuthProvider(
        settings=settings,
        authorized_parties=frozenset({APP_ORIGIN}),
        signing_key_resolver=UnavailableResolver(),
        observability=recording_observability,  # type: ignore[arg-type]
    )

    with pytest.raises(AuthenticationUnavailable) as exc_info:
        await provider.verify_token("TOKEN_SENTINEL")

    assert exc_info.value.reason == "jwks_timeout"
    assert recording_observability.verifications == [
        ("unavailable", "jwks_timeout")
    ]


@pytest.mark.anyio
async def test_jwks_verifier_uses_the_same_strict_claim_policy(
    settings,
    clerk_key_material: dict[str, str | bytes],
    recording_observability: RecordingObservability,
    rs256_clerk_token_for,
) -> None:
    private_key = serialization.load_pem_private_key(
        str(clerk_key_material["private_key_pem"]).encode(),
        password=None,
    )
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk.update({"kid": "test-key", "alg": "RS256", "use": "sig"})
    resolver = JwksSigningKeyResolver(
        jwks_url=JWKS_URL,
        cache_ttl_seconds=300.0,
        stale_grace_seconds=600.0,
        connect_timeout_seconds=0.25,
        read_timeout_seconds=0.5,
        pool_timeout_seconds=0.1,
        total_timeout_seconds=2.0,
        observability=recording_observability,  # type: ignore[arg-type]
        transport=JsonTransport({"keys": [jwk]}),
    )
    provider = ClerkAuthProvider(
        settings=settings,
        authorized_parties=frozenset({APP_ORIGIN}),
        signing_key_resolver=resolver,
        observability=recording_observability,  # type: ignore[arg-type]
    )
    token = rs256_clerk_token_for("user_active", headers={"kid": "test-key"})

    try:
        identity = await provider.verify_token(token)
    finally:
        await provider.aclose()

    assert identity == UserIdentity(clerk_user_id="user_active")
