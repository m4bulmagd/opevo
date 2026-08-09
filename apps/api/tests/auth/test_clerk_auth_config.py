import math

import pytest

from app.core.clerk_verification_source import select_clerk_verification_source
from app.core.config import Settings
from app.core.http_origin import (
    parse_canonical_http_origin,
    parse_canonical_http_origins,
    validate_absolute_https_url,
)
from app.core.runtime_validation import validate_api_runtime


VALID_ORIGINS = (
    "https://app.example.com",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://[2001:db8::1]:8443",
)


@pytest.mark.parametrize("origin", VALID_ORIGINS)
def test_canonical_http_origin_accepts_exact_canonical_value(origin: str) -> None:
    assert parse_canonical_http_origin(origin) == origin


@pytest.mark.parametrize(
    "origin",
    [
        " https://app.example.com",
        "https://app.example.com ",
        "HTTPS://app.example.com",
        "https://APP.example.com",
        "https://app.example.com/",
        "https://app.example.com/path",
        "https://app.example.com?query=1",
        "https://app.example.com#fragment",
        "https://user@app.example.com",
        "https://app.example.com:443",
        "http://app.example.com:80",
        "ftp://app.example.com",
        "https://*.example.com",
        "https://app.example.com\\evil",
        "https://app.example.com\n",
        "https://app.example.com:bad",
        "",
    ],
)
def test_canonical_http_origin_rejects_noncanonical_or_unsafe_value(
    origin: str,
) -> None:
    with pytest.raises(ValueError, match="invalid canonical HTTP origin") as exc_info:
        parse_canonical_http_origin(origin)
    assert not origin or origin not in str(exc_info.value)


def test_authorized_parties_preserve_order_and_reject_duplicates() -> None:
    assert parse_canonical_http_origins(
        "https://app.example.com,http://localhost:3000"
    ) == ("https://app.example.com", "http://localhost:3000")
    with pytest.raises(ValueError, match="duplicate canonical HTTP origin"):
        parse_canonical_http_origins(
            "https://app.example.com,https://app.example.com"
        )


@pytest.mark.parametrize("raw", [None, "", ",", "https://app.example.com,"])
def test_authorized_parties_reject_missing_or_empty_entries(raw: str | None) -> None:
    with pytest.raises(ValueError):
        parse_canonical_http_origins(raw)


def test_verification_source_presence_check_preserves_static_key_bytes() -> None:
    configured_key = "\nPEM_BODY_SENTINEL\n"

    source = select_clerk_verification_source(
        jwt_key=configured_key,
        jwks_url=None,
    )

    assert source is not None
    assert source.kind == "static"
    assert source.value == configured_key


@pytest.mark.parametrize(
    "url",
    [
        "http://clerk.example.com/.well-known/jwks.json",
        "https://user@clerk.example.com/jwks.json",
        "https://clerk.example.com/jwks.json#fragment",
        "https://clerk.example.com/jwks.json#",
        "//clerk.example.com/jwks.json",
        "JWKS_ENDPOINT_SENTINEL",
    ],
)
def test_jwks_endpoint_requires_safe_absolute_https_url(url: str) -> None:
    with pytest.raises(ValueError, match="invalid HTTPS URL") as exc_info:
        validate_absolute_https_url(url)
    assert url not in str(exc_info.value)


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "auth_provider": "clerk",
        "database_url": "sqlite+aiosqlite://",
        "redis_url": "redis://localhost:6379/0",
        "clerk_issuer": "https://clerk.example.com",
        "clerk_authorized_parties": "https://app.example.com",
        "clerk_jwt_key": "test-public-key",
        "clerk_jwks_url": None,
        "agent_dispatch_jwt_secret": "a-safe-test-secret-with-at-least-32-bytes",
    }
    values.update(updates)
    return Settings(**values)


@pytest.mark.parametrize(
    ("updates", "setting_name"),
    [
        ({"clerk_issuer": ""}, "CLERK_ISSUER"),
        ({"clerk_authorized_parties": None}, "CLERK_AUTHORIZED_PARTIES"),
        ({"clerk_jwt_key": None, "clerk_jwks_url": None}, "CLERK_JWT_KEY"),
        (
            {
                "clerk_jwt_key": "STATIC_KEY_SENTINEL",
                "clerk_jwks_url": "https://clerk.example.com/jwks.json",
            },
            "CLERK_JWT_KEY",
        ),
    ],
)
def test_clerk_mode_rejects_missing_or_ambiguous_verification_configuration(
    updates: dict[str, object], setting_name: str
) -> None:
    with pytest.raises(RuntimeError, match=setting_name) as exc_info:
        validate_api_runtime(_settings(**updates))
    assert "STATIC_KEY_SENTINEL" not in str(exc_info.value)


def test_local_development_does_not_require_clerk_configuration() -> None:
    validate_api_runtime(
        _settings(
            app_env="development",
            auth_provider="local",
            clerk_issuer="",
            clerk_authorized_parties=None,
            clerk_jwt_key=None,
            local_auth_token="explicit-local-test-token",
        )
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("clerk_jwks_cache_ttl_seconds", 29.99),
        ("clerk_jwks_cache_ttl_seconds", 3600.01),
        ("clerk_jwks_stale_grace_seconds", 0.99),
        ("clerk_jwks_stale_grace_seconds", 3600.01),
        ("clerk_jwks_connect_timeout_seconds", 0.049),
        ("clerk_jwks_read_timeout_seconds", 10.01),
        ("clerk_jwks_pool_timeout_seconds", math.inf),
        ("clerk_jwks_total_timeout_seconds", math.nan),
    ],
)
def test_clerk_duration_bounds_are_finite_and_enforced(
    field: str, value: float
) -> None:
    with pytest.raises(ValueError) as exc_info:
        _settings(**{field: value})
    assert field in str(exc_info.value)
