import importlib
import time
from types import ModuleType
from uuid import uuid4

import jwt
import pytest

from app.core.dispatch_token import create_dispatch_token, verify_dispatch_token


DISPATCH_SECRET = "verification-test-secret-with-enough-entropy-for-hmac-tests"
AUDIENCE = "presvo-forwarding-verification"


def _verification_token_module() -> ModuleType:
    try:
        return importlib.import_module("app.core.verification_token")
    except ModuleNotFoundError:
        pytest.fail("verification-scoped token API is missing")


def _configure_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_JWT_SECRET", DISPATCH_SECRET)
    from app.core.config import get_settings

    get_settings.cache_clear()


def test_verification_token_has_distinct_audience_and_bounded_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_tokens(monkeypatch)
    module = _verification_token_module()
    session_id = str(uuid4())
    user_id = str(uuid4())

    token = module.create_verification_token(
        session_id=session_id,
        user_id=user_id,
    )
    payload = jwt.decode(
        token,
        DISPATCH_SECRET,
        algorithms=["HS256"],
        audience=AUDIENCE,
    )

    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    assert payload["aud"] == AUDIENCE
    assert payload["sub"] == session_id
    assert payload["user_id"] == user_id
    assert isinstance(payload["iat"], int)
    assert isinstance(payload["exp"], int)
    assert payload["exp"] - payload["iat"] == 900
    assert set(payload) == {"aud", "sub", "user_id", "iat", "exp"}


@pytest.mark.parametrize("ttl_seconds", [True, 0, -1, 901])
def test_verification_token_rejects_unbounded_lifetime(
    monkeypatch: pytest.MonkeyPatch,
    ttl_seconds: int,
) -> None:
    _configure_tokens(monkeypatch)
    module = _verification_token_module()

    with pytest.raises(ValueError, match="configured safely"):
        module.create_verification_token(
            session_id=str(uuid4()),
            user_id=str(uuid4()),
            ttl_seconds=ttl_seconds,
        )


def test_verification_token_verifies_exact_session_and_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_tokens(monkeypatch)
    module = _verification_token_module()
    session_id = str(uuid4())
    user_id = str(uuid4())
    token = module.create_verification_token(
        session_id=session_id,
        user_id=user_id,
    )

    claims = module.verify_verification_token(
        token,
        expected_session_id=session_id,
        expected_user_id=user_id,
    )

    assert claims["aud"] == AUDIENCE
    assert claims["sub"] == session_id
    assert claims["user_id"] == user_id


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "malformed",
        "expired",
        "wrong_session",
        "wrong_user",
        "missing_claim",
    ],
)
def test_verification_token_failures_are_generic(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    _configure_tokens(monkeypatch)
    module = _verification_token_module()
    session_id = str(uuid4())
    user_id = str(uuid4())
    expected_session_id = session_id
    expected_user_id = user_id
    now = int(time.time())
    payload = {
        "aud": AUDIENCE,
        "sub": session_id,
        "user_id": user_id,
        "iat": now,
        "exp": now + 900,
    }
    if case == "missing":
        token = ""
    elif case == "malformed":
        token = "not-a-jwt"
    elif case == "expired":
        payload["iat"] = now - 901
        payload["exp"] = now - 1
        token = jwt.encode(payload, DISPATCH_SECRET, algorithm="HS256")
    elif case == "wrong_session":
        expected_session_id = str(uuid4())
        token = jwt.encode(payload, DISPATCH_SECRET, algorithm="HS256")
    elif case == "wrong_user":
        expected_user_id = str(uuid4())
        token = jwt.encode(payload, DISPATCH_SECRET, algorithm="HS256")
    else:
        payload.pop("user_id")
        token = jwt.encode(payload, DISPATCH_SECRET, algorithm="HS256")

    with pytest.raises(ValueError) as exc_info:
        module.verify_verification_token(
            token,
            expected_session_id=expected_session_id,
            expected_user_id=expected_user_id,
        )

    assert str(exc_info.value) == "Invalid verification token"
    if token:
        assert token not in str(exc_info.value)
    assert session_id not in str(exc_info.value)
    assert user_id not in str(exc_info.value)


def test_call_and_verification_tokens_are_cross_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_tokens(monkeypatch)
    module = _verification_token_module()
    session_id = str(uuid4())
    call_id = str(uuid4())
    user_id = str(uuid4())
    call_token = create_dispatch_token(
        call_id=call_id,
        user_id=user_id,
        agent_config_id=str(uuid4()),
    )
    verification_token = module.create_verification_token(
        session_id=session_id,
        user_id=user_id,
    )

    with pytest.raises(ValueError, match="Invalid verification token"):
        module.verify_verification_token(
            call_token,
            expected_session_id=session_id,
            expected_user_id=user_id,
        )
    with pytest.raises(ValueError, match="Invalid dispatch token"):
        verify_dispatch_token(
            verification_token,
            expected_call_id=call_id,
            expected_user_id=user_id,
        )


@pytest.mark.parametrize(
    "unsafe_secret",
    ["too-short", "replace-with-a-long-random-secret"],
)
def test_verification_tokens_reuse_dispatch_secret_safety_validation(
    monkeypatch: pytest.MonkeyPatch,
    unsafe_secret: str,
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_JWT_SECRET", unsafe_secret)
    from app.core.config import get_settings

    get_settings.cache_clear()
    module = _verification_token_module()

    with pytest.raises(ValueError, match="configured safely"):
        module.create_verification_token(
            session_id=str(uuid4()),
            user_id=str(uuid4()),
        )


@pytest.mark.parametrize("missing_claim", ["aud", "sub", "user_id", "iat", "exp"])
def test_verification_token_rejects_every_missing_required_claim(
    monkeypatch: pytest.MonkeyPatch,
    missing_claim: str,
) -> None:
    _configure_tokens(monkeypatch)
    module = _verification_token_module()
    session_id = str(uuid4())
    user_id = str(uuid4())
    now = int(time.time())
    payload = {
        "aud": AUDIENCE,
        "sub": session_id,
        "user_id": user_id,
        "iat": now,
        "exp": now + 900,
    }
    payload.pop(missing_claim)
    token = jwt.encode(payload, DISPATCH_SECRET, algorithm="HS256")

    with pytest.raises(ValueError) as exc_info:
        module.verify_verification_token(
            token,
            expected_session_id=session_id,
            expected_user_id=user_id,
        )

    assert str(exc_info.value) == "Invalid verification token"
    assert token not in str(exc_info.value)


@pytest.mark.parametrize(
    ("mutation", "algorithm"),
    [
        ({"aud": "wrong-audience"}, "HS256"),
        ({"sub": "not-a-uuid"}, "HS256"),
        ({"user_id": "not-a-uuid"}, "HS256"),
        ({"iat": True}, "HS256"),
        ({"iat": "1"}, "HS256"),
        ({"exp": True}, "HS256"),
        ({"exp": "2"}, "HS256"),
        ({"iat": 10, "exp": 10}, "HS256"),
        ({}, "HS384"),
    ],
)
def test_verification_token_rejects_invalid_claim_shapes_and_algorithm(
    monkeypatch: pytest.MonkeyPatch,
    mutation: dict[str, object],
    algorithm: str,
) -> None:
    _configure_tokens(monkeypatch)
    module = _verification_token_module()
    session_id = str(uuid4())
    user_id = str(uuid4())
    now = int(time.time())
    payload: dict[str, object] = {
        "aud": AUDIENCE,
        "sub": session_id,
        "user_id": user_id,
        "iat": now,
        "exp": now + 900,
    }
    payload.update(mutation)
    token = jwt.encode(payload, DISPATCH_SECRET, algorithm=algorithm)

    with pytest.raises(ValueError) as exc_info:
        module.verify_verification_token(
            token,
            expected_session_id=session_id,
            expected_user_id=user_id,
        )

    assert str(exc_info.value) == "Invalid verification token"
    assert token not in str(exc_info.value)
    assert session_id not in str(exc_info.value)
    assert user_id not in str(exc_info.value)
