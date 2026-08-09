import logging
import time
from contextlib import AsyncExitStack
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import FastAPI
from starlette.requests import Request

from app.core.auth import get_auth_provider
from app.composition.lifecycle import RuntimeCleanup
from app.composition.runtime import ApiRuntime
from app.core.auth_failures import AuthenticationUnavailable, TokenRejected
from app.core.dispatch_token import (
    DispatchTokenConfig,
    DispatchTokenConfigurationError,
    dispatch_token_config,
    create_dispatch_token,
    verify_dispatch_token,
)


DISPATCH_SECRET = "dispatch-test-secret-with-enough-entropy-for-all-hmac-tests"
EXPLICIT_DISPATCH_SECRET = (
    "explicit-dispatch-secret-different-from-controlled-environment"
)


@pytest.mark.anyio
async def test_rejected_auth_token_logs_only_safe_fixed_fields(
    async_client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.main import app

    class RejectingAuthProvider:
        async def verify_token(self, _token: str) -> None:
            raise TokenRejected("authorized_party")

    token = jwt.encode(
        {
            "sub": "JWT_SUBJECT_SENTINEL",
            "jti": "JWT_TOKEN_SENTINEL",
        },
        "test-only-rejected-token-secret-with-entropy",
        algorithm="HS256",
    )
    app.dependency_overrides[get_auth_provider] = RejectingAuthProvider
    try:
        with caplog.at_level(logging.WARNING, logger="app.core.auth"):
            response = await async_client.get(
                "/api/agent/config",
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_auth_provider, None)

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid token"}
    for sentinel in (
        "JWT_SUBJECT_SENTINEL",
        "JWT_TOKEN_SENTINEL",
        token,
    ):
        assert sentinel not in caplog.text
    assert "event=auth_token_rejected" in caplog.text
    assert "operation=verify_token" in caplog.text
    assert "reason=authorized_party" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_request_auth_provider_returns_exact_app_scoped_instance() -> None:
    provider = object()
    app = FastAPI()
    app.state.runtime = ApiRuntime(
        settings=object(),
        engine=object(),
        session_factory=object(),
        redis_client=object(),
        observability=object(),
        auth_provider=provider,
        readiness_checks=object(),
        storage_provider=object(),
        arq_pool=None,
        call_finalization_queue=None,
        realtime_service=None,
        livekit_webhook_receiver=None,
        livekit_recording_service=None,
        _cleanup=RuntimeCleanup(AsyncExitStack()),
    )
    request = Request({"type": "http", "app": app})

    assert get_auth_provider(request) is provider


class RejectingProvider:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    async def verify_token(self, token: str) -> None:
        del token
        raise self.failure


async def request_protected_route(app: FastAPI, *, token: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.get(
            "/api/agent/config",
            headers={"Authorization": f"Bearer {token}"},
        )


async def request_protected_route_with_authorization(
    app: FastAPI,
    *,
    authorization: str | None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    headers = {} if authorization is None else {"Authorization": authorization}
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.get("/api/agent/config", headers=headers)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "authorization",
    [
        pytest.param(None, id="absent"),
        pytest.param("Basic CREDENTIAL_SENTINEL", id="wrong-scheme"),
        pytest.param("CREDENTIAL_SENTINEL", id="missing-scheme"),
        pytest.param("Bearer", id="missing-value"),
        pytest.param("Bearer ", id="empty-value"),
        pytest.param("Bearer    ", id="whitespace-value"),
        pytest.param("   ", id="whitespace-header"),
        pytest.param("Bearer\tCREDENTIAL_SENTINEL", id="tab-separator"),
    ],
)
async def test_rest_maps_missing_or_unusable_credentials_to_generic_401(
    test_app,
    authorization: str | None,
) -> None:
    response = await request_protected_route_with_authorization(
        test_app,
        authorization=authorization,
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid token"}
    if authorization and authorization.strip():
        assert authorization not in response.text


@pytest.mark.anyio
async def test_rest_maps_rejected_token_to_generic_401(test_app) -> None:
    original_provider = test_app.state.runtime.auth_provider
    try:
        test_app.state.runtime.auth_provider = RejectingProvider(
            TokenRejected("authorized_party")
        )

        response = await request_protected_route(test_app, token="TOKEN_SENTINEL")
    finally:
        test_app.state.runtime.auth_provider = original_provider

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid token"}
    assert "TOKEN_SENTINEL" not in response.text


@pytest.mark.anyio
async def test_rest_maps_provider_outage_to_generic_503(test_app) -> None:
    original_provider = test_app.state.runtime.auth_provider
    try:
        test_app.state.runtime.auth_provider = RejectingProvider(
            AuthenticationUnavailable("jwks_timeout")
        )

        response = await request_protected_route(test_app, token="TOKEN_SENTINEL")
    finally:
        test_app.state.runtime.auth_provider = original_provider

    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication temporarily unavailable"}
    assert "TOKEN_SENTINEL" not in response.text


def _dispatch_config(*, ttl: int = 7200) -> DispatchTokenConfig:
    return DispatchTokenConfig(secret=DISPATCH_SECRET, ttl_seconds=ttl)


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


def test_dispatch_token_contains_all_required_call_scoped_claims() -> None:
    call_id = str(uuid4())
    user_id = str(uuid4())
    agent_config_id = str(uuid4())

    token = create_dispatch_token(
        call_id,
        user_id,
        agent_config_id,
        config=_dispatch_config(),
    )
    payload = jwt.decode(token, DISPATCH_SECRET, algorithms=["HS256"])

    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    assert payload["call_id"] == call_id
    assert payload["user_id"] == user_id
    assert payload["agent_config_id"] == agent_config_id
    assert isinstance(payload["iat"], int)
    assert payload["exp"] - payload["iat"] == 7200


def test_dispatch_token_uses_explicit_config_instead_of_environment() -> None:
    config = DispatchTokenConfig(
        secret=EXPLICIT_DISPATCH_SECRET,
        ttl_seconds=37,
    )
    call_id = str(uuid4())
    user_id = str(uuid4())
    agent_config_id = str(uuid4())

    token = create_dispatch_token(
        call_id,
        user_id,
        agent_config_id,
        config=config,
    )
    payload = jwt.decode(
        token,
        EXPLICIT_DISPATCH_SECRET,
        algorithms=["HS256"],
    )

    assert payload["exp"] - payload["iat"] == 37
    assert (
        verify_dispatch_token(
            token,
            expected_call_id=call_id,
            expected_user_id=user_id,
            config=config,
        )["agent_config_id"]
        == agent_config_id
    )


def test_create_dispatch_token_fails_safely_without_secret() -> None:
    from app.core.config import Settings

    with pytest.raises(ValueError, match="configured"):
        dispatch_token_config(
            Settings(
                database_url="sqlite+aiosqlite://",
                redis_url="redis://localhost:6379/0",
                agent_dispatch_jwt_secret=None,
            )
        )


@pytest.mark.parametrize(
    "ttl_seconds",
    [
        pytest.param(True, id="bool"),
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
        pytest.param("7200", id="non-integer"),
    ],
)
def test_dispatch_token_config_rejects_unsafe_lifetime(ttl_seconds: object) -> None:
    from app.core.config import Settings

    settings = Settings.model_construct(
        agent_dispatch_jwt_secret=DISPATCH_SECRET,
        agent_dispatch_jwt_ttl_seconds=ttl_seconds,
    )

    with pytest.raises(DispatchTokenConfigurationError) as exc_info:
        dispatch_token_config(settings)

    assert str(exc_info.value) == "Dispatch token lifetime is not configured safely"


@pytest.mark.parametrize(
    "unsafe_secret",
    [
        "too-short",
        "replace-with-a-long-random-secret",
        "CHANGE-ME-CHANGE-ME-CHANGE-ME-CHANGE-ME",
    ],
)
def test_dispatch_token_sign_and_verify_reject_unsafe_hmac_secrets(
    unsafe_secret: str,
) -> None:
    from app.core.config import Settings

    with pytest.raises(ValueError, match="configured safely"):
        dispatch_token_config(
            Settings(
                database_url="sqlite+aiosqlite://",
                redis_url="redis://localhost:6379/0",
                agent_dispatch_jwt_secret=unsafe_secret,
            )
        )


@pytest.mark.parametrize(
    "missing_claim",
    ["call_id", "user_id", "agent_config_id", "iat", "exp"],
)
def test_verify_dispatch_token_rejects_every_missing_required_claim(
    missing_claim: str,
) -> None:
    payload = _valid_dispatch_payload()
    expected_call_id = payload["call_id"]
    payload.pop(missing_claim)

    with pytest.raises(ValueError, match="Invalid dispatch token"):
        verify_dispatch_token(
            _encode_dispatch_payload(payload),
            expected_call_id=expected_call_id,
            config=_dispatch_config(),
        )


@pytest.mark.parametrize(
    "token",
    ["not-a-jwt", "", "header.payload.signature"],
)
def test_verify_dispatch_token_rejects_malformed_tokens(
    token: str,
) -> None:
    with pytest.raises(ValueError, match="Invalid dispatch token"):
        verify_dispatch_token(
            token,
            expected_call_id=str(uuid4()),
            config=_dispatch_config(),
        )


def test_verify_dispatch_token_rejects_expired_token() -> None:
    payload = _valid_dispatch_payload()
    payload["iat"] = int(time.time()) - 10
    payload["exp"] = int(time.time()) - 1

    with pytest.raises(ValueError, match="Invalid dispatch token"):
        verify_dispatch_token(
            _encode_dispatch_payload(payload),
            expected_call_id=payload["call_id"],
            config=_dispatch_config(),
        )


def test_verify_dispatch_token_rejects_non_hs256_algorithm() -> None:
    payload = _valid_dispatch_payload()

    with pytest.raises(ValueError, match="Invalid dispatch token"):
        verify_dispatch_token(
            _encode_dispatch_payload(payload, algorithm="HS384"),
            expected_call_id=payload["call_id"],
            config=_dispatch_config(),
        )


def test_verify_dispatch_token_rejects_wrong_call_without_echoing_identifiers() -> None:
    payload = _valid_dispatch_payload()
    wrong_call_id = str(uuid4())
    token = _encode_dispatch_payload(payload)

    with pytest.raises(ValueError) as exc_info:
        verify_dispatch_token(
            token,
            expected_call_id=wrong_call_id,
            config=_dispatch_config(),
        )

    message = str(exc_info.value)
    assert payload["call_id"] not in message
    assert wrong_call_id not in message
    assert token not in message


def test_verify_dispatch_token_rejects_wrong_user() -> None:
    payload = _valid_dispatch_payload()

    with pytest.raises(ValueError, match="Invalid dispatch token"):
        verify_dispatch_token(
            _encode_dispatch_payload(payload),
            payload["call_id"],
            expected_user_id=str(uuid4()),
            config=_dispatch_config(),
        )


def test_verify_dispatch_token_rejects_invalid_identifier_claim_type() -> None:
    payload = _valid_dispatch_payload()
    payload["agent_config_id"] = 123

    with pytest.raises(ValueError, match="Invalid dispatch token"):
        verify_dispatch_token(
            _encode_dispatch_payload(payload),
            expected_call_id=payload["call_id"],
            config=_dispatch_config(),
        )


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
    assert response.json() == {"detail": "Invalid token"}
