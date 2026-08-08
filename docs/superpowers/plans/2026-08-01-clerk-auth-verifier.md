# Application-Scoped Clerk Authentication Verifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one application-scoped asynchronous Clerk verifier that requires an approved `azp`, performs bounded/coalesced JWKS refreshes, and exposes distinct safe credential-rejection and provider-unavailability behavior without enabling realtime.

**Architecture:** Keep the existing `AuthProvider` boundary, make it asynchronous, and construct exactly one implementation in the FastAPI lifespan. Put canonical-origin validation, safe failure types, JWKS resolution/cache state, and transport mapping in focused modules; REST and the disabled WebSocket path consume the same provider instance and typed failures.

**Tech Stack:** Python 3.13, FastAPI/Starlette, Pydantic Settings, PyJWT 2.13, httpx 0.28, asyncio, OpenTelemetry, pytest 9, pytest-asyncio, Ruff, mypy, Docker Compose.

## Global Constraints

- `AUTH_MODE=clerk` requires non-empty `CLERK_ISSUER`, non-empty `CLERK_AUTHORIZED_PARTIES`, and exactly one of `CLERK_JWT_KEY` or `CLERK_JWKS_URL` in every environment.
- `CLERK_AUTHORIZED_PARTIES` is independent from CORS and contains exact canonical HTTP(S) origins; token `azp` values are compared without normalization.
- The only accepted JWT algorithm is `RS256`; `exp`, `nbf`, `sub`, and `azp` are required; configured issuer and optional audience are verified.
- Static-key mode creates no HTTP client and does not require `kid`.
- JWKS mode uses one application-scoped `httpx.AsyncClient`, follows no redirects, and accepts only an absolute HTTPS endpoint without credentials or a fragment.
- Cache defaults are 300 fresh seconds plus 600 stale-known-key-only seconds. Cache TTL is 30–3,600 seconds; stale grace is 1–3,600 seconds.
- Connect, read, pool, and total defaults are 0.5, 1.0, 0.25, and 2.0 seconds. Each timeout is finite and between 0.05 and 10 seconds.
- Fixed limits are: encoded `kid` length 128 characters, JWKS body 256 KiB, 16 signing keys, and five seconds between completed refresh attempts.
- Concurrent refreshes share one shielded task. Cancelling or timing out one waiter must not cancel the shared refresh.
- A failed refresh never replaces or extends a valid cache generation. Only an already-known key can use stale grace; an unknown key never can.
- Invalid credentials map to REST `401`/generic `Invalid token` and WebSocket `invalid_token`/close `1008`. Provider unavailability maps to REST `503`/generic `Authentication temporarily unavailable` and WebSocket `auth_unavailable`/close `1013`.
- Logs, exceptions, responses, spans, and metric labels must not expose tokens, claims, subjects, `azp`, `kid`, JWKS bodies, URLs, keys, secrets, response bodies, or raw provider error text.
- Local authentication and Clerk webhook signature verification retain their current external behavior.
- `REALTIME_ENABLED` and `NEXT_PUBLIC_REALTIME_ENABLED` remain false. This work tests the dormant WebSocket authentication path but does not enable or deploy it.
- No distributed cache or cross-replica lock is introduced; one or two API replicas each keep their own bounded process-local cache.
- Every behavior change is test-first. Do not lower coverage, weaken assertions, add skips, or update visual snapshots.

## File Responsibility Map

- `apps/api/app/core/http_origin.py`: existing general URL-origin extraction plus new strict canonical-origin and Clerk JWKS endpoint validation helpers.
- `apps/api/app/core/config.py`: validated Clerk cache/timeout settings and the raw authorized-parties setting.
- `apps/api/app/core/runtime_validation.py`: environment-independent Clerk-mode invariants and value-redacted startup failures.
- `apps/api/app/core/auth_failures.py`: two fixed-message exception families and bounded reason-code types shared by verifier, transports, and telemetry.
- `apps/api/app/core/observability.py`: bounded authentication/JWKS instruments; no dynamic or attacker-controlled labels.
- `apps/api/app/core/clerk_jwks.py`: static and JWKS signing-key adapters, defensive parsing, cache generation, refresh coalescing, stale-grace rules, and shutdown.
- `apps/api/app/core/auth.py`: async provider interface, Clerk claim policy, local provider, app-state dependency, REST mapping, and unchanged webhook verification.
- `apps/api/app/main.py`: application-scoped provider construction, shared injection into realtime, and exact-once cleanup.
- `apps/api/app/services/realtime_service.py` and `apps/api/app/routers/websocket.py`: async verifier call and typed WebSocket-safe failure mapping while the route remains disabled by configuration.
- `apps/api/tests/auth/test_clerk_auth_config.py`: exhaustive origin/settings/runtime validation.
- `apps/api/tests/auth/test_clerk_jwks.py`: HTTP, cache, concurrency, time-boundary, cancellation, parsing, and cleanup behavior.
- `apps/api/tests/auth/test_clerk_token_verifier.py`: strict claims, `azp`, algorithms, safe failures, static/JWKS parity, and redaction.
- Existing auth, observability, realtime, integration, deployment, and fixture tests: async interface and lifecycle regression coverage.
- `apps/api/pyproject.toml` and `apps/api/uv.lock`: promote httpx from test-only to direct runtime dependency.
- `compose.yaml`, `apps/api/.env.example`, and `.github/workflows/ci.yml`: API-only production/test configuration; local Compose remains local-auth and realtime-disabled.
- `docs/engineering/2026-07-30-agent-api-review-decisions.md`: mark Issues 3A and 13A implemented only after all gates pass.

---

### Task 1: Strict Clerk configuration and canonical origins

**Files:**
- Modify: `apps/api/app/core/http_origin.py`
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/app/core/runtime_validation.py`
- Create: `apps/api/tests/auth/test_clerk_auth_config.py`
- Modify: `apps/api/tests/test_deployment_readiness.py`
- Modify: `apps/api/tests/conftest.py`

**Interfaces:**
- Consumes: existing `Settings`, `validate_api_runtime(settings: Settings) -> None`, and `parse_http_origin(value: str) -> HttpOrigin`.
- Produces: `parse_canonical_http_origin(value: str) -> str`, `parse_canonical_http_origins(value: str | None) -> tuple[str, ...]`, and `validate_absolute_https_url(value: str) -> None`.
- Produces settings: `clerk_authorized_parties: str | None`; `clerk_jwks_cache_ttl_seconds: float`; `clerk_jwks_stale_grace_seconds: float`; `clerk_jwks_connect_timeout_seconds: float`; `clerk_jwks_read_timeout_seconds: float`; `clerk_jwks_pool_timeout_seconds: float`; `clerk_jwks_total_timeout_seconds: float`.

- [ ] **Step 1: Install the locked worktree dependencies and establish the focused baseline**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/test_deployment_readiness.py tests/auth/test_jwt_auth.py
```

Expected: dependency sync succeeds and the existing focused tests pass before behavior changes.

- [ ] **Step 2: Write failing canonical-origin and Clerk-mode configuration tests**

Create `tests/auth/test_clerk_auth_config.py` with explicit tables. Keep sentinel values in failure cases and assert they never appear in exception text.

```python
import math

import pytest

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
    assert origin not in str(exc_info.value)


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


@pytest.mark.parametrize(
    "url",
    [
        "http://clerk.example.com/.well-known/jwks.json",
        "https://user@clerk.example.com/jwks.json",
        "https://clerk.example.com/jwks.json#fragment",
        "//clerk.example.com/jwks.json",
        "JWKS_ENDPOINT_SENTINEL",
    ],
)
def test_jwks_endpoint_requires_safe_absolute_https_url(url: str) -> None:
    with pytest.raises(ValueError, match="invalid Clerk JWKS URL") as exc_info:
        validate_absolute_https_url(url)
    assert url not in str(exc_info.value)


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "auth_mode": "clerk",
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
            auth_mode="local",
            clerk_issuer="",
            clerk_authorized_parties=None,
            clerk_jwt_key=None,
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
```

Update `tests/test_deployment_readiness.py` so its production fixture includes `clerk_authorized_parties="https://app.example.com"`, static and JWKS cases use valid material, and both/neither sources are rejected rather than silently preferring one.

- [ ] **Step 3: Run the new tests and confirm the intended failures**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/auth/test_clerk_auth_config.py tests/test_deployment_readiness.py
```

Expected: collection fails because the three strict parsing helpers and new settings fields do not exist, followed by behavior failures until runtime validation is changed.

- [ ] **Step 4: Implement strict helpers and bounded settings**

Add the settings exactly as follows, using `allow_inf_nan=False` so NaN and infinities cannot bypass bounds:

```python
clerk_authorized_parties: str | None = None
clerk_jwks_cache_ttl_seconds: float = Field(
    default=300.0, ge=30.0, le=3600.0, allow_inf_nan=False
)
clerk_jwks_stale_grace_seconds: float = Field(
    default=600.0, ge=1.0, le=3600.0, allow_inf_nan=False
)
clerk_jwks_connect_timeout_seconds: float = Field(
    default=0.5, ge=0.05, le=10.0, allow_inf_nan=False
)
clerk_jwks_read_timeout_seconds: float = Field(
    default=1.0, ge=0.05, le=10.0, allow_inf_nan=False
)
clerk_jwks_pool_timeout_seconds: float = Field(
    default=0.25, ge=0.05, le=10.0, allow_inf_nan=False
)
clerk_jwks_total_timeout_seconds: float = Field(
    default=2.0, ge=0.05, le=10.0, allow_inf_nan=False
)
```

Implement the strict parsing surface in `http_origin.py`. Retain `parse_http_origin()` unchanged for Stripe URLs; the Clerk helper is intentionally stricter.

```python
import ipaddress
from urllib.parse import urlsplit


def _has_unsafe_url_character(value: str) -> bool:
    return any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in value
    )


def _canonical_origin_host(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if ":" in host:
            raise ValueError("invalid canonical HTTP origin") from None
        try:
            ascii_host = host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise ValueError("invalid canonical HTTP origin") from None
        labels = ascii_host.split(".")
        if not all(
            label
            and len(label) <= 63
            and label[0] != "-"
            and label[-1] != "-"
            and all(character.isalnum() or character == "-" for character in label)
            for label in labels
        ):
            raise ValueError("invalid canonical HTTP origin")
        return ascii_host
    canonical = address.compressed.lower()
    return f"[{canonical}]" if address.version == 6 else canonical


def parse_canonical_http_origin(value: str) -> str:
    error = ValueError("invalid canonical HTTP origin")
    if (
        not value
        or value != value.strip()
        or _has_unsafe_url_character(value)
        or "\\" in value
        or "*" in value
    ):
        raise error
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise error from None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise error
    host = _canonical_origin_host(parsed.hostname)
    default_port = 443 if parsed.scheme == "https" else 80
    port_suffix = "" if port is None or port == default_port else f":{port}"
    canonical = f"{parsed.scheme}://{host}{port_suffix}"
    if canonical != value:
        raise error
    return canonical


def parse_canonical_http_origins(value: str | None) -> tuple[str, ...]:
    if value is None:
        raise ValueError("missing canonical HTTP origins")
    origins = tuple(parse_canonical_http_origin(item) for item in value.split(","))
    if len(origins) != len(set(origins)):
        raise ValueError("duplicate canonical HTTP origin")
    return origins


def validate_absolute_https_url(value: str) -> None:
    error = ValueError("invalid Clerk JWKS URL")
    if (
        not value
        or value != value.strip()
        or _has_unsafe_url_character(value)
        or "\\" in value
        or "*" in value
    ):
        raise error
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        raise error from None
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise error
    try:
        _canonical_origin_host(parsed.hostname)
    except ValueError:
        raise error from None
```

Keep these helpers explicit and dependency-free; do not introduce a URL library.

Restructure `validate_api_runtime()` so Clerk invariants run before the existing development return:

```python
if settings.auth_mode == "clerk":
    invalid_clerk: list[str] = []
    if _is_missing(settings.clerk_issuer):
        invalid_clerk.append("CLERK_ISSUER")
    try:
        parse_canonical_http_origins(settings.clerk_authorized_parties)
    except ValueError:
        invalid_clerk.append("CLERK_AUTHORIZED_PARTIES")
    has_static_key = not _is_missing(settings.clerk_jwt_key)
    has_jwks_url = not _is_missing(settings.clerk_jwks_url)
    if has_static_key == has_jwks_url:
        invalid_clerk.append("exactly one of CLERK_JWT_KEY or CLERK_JWKS_URL")
    if has_jwks_url:
        try:
            validate_absolute_https_url(str(settings.clerk_jwks_url))
        except ValueError:
            invalid_clerk.append("CLERK_JWKS_URL")
    if invalid_clerk:
        raise RuntimeError(
            "Missing or invalid required runtime settings: "
            + ", ".join(invalid_clerk)
        )
```

Keep the existing rule that non-development API runtimes reject `AUTH_MODE=local`. Do not add Clerk settings to `validate_worker_runtime()`.

- [ ] **Step 5: Make the common test token/configuration explicit and rerun focused tests**

In `tests/conftest.py`, add:

```python
TEST_CLERK_AUTHORIZED_PARTY = "https://app.example.com"

# In settings_env:
monkeypatch.setenv("CLERK_AUTHORIZED_PARTIES", TEST_CLERK_AUTHORIZED_PARTY)
monkeypatch.delenv("CLERK_JWKS_URL", raising=False)
```

Then run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app/core/config.py app/core/http_origin.py app/core/runtime_validation.py tests/auth/test_clerk_auth_config.py tests/test_deployment_readiness.py tests/conftest.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/auth/test_clerk_auth_config.py tests/test_deployment_readiness.py
```

Expected: Ruff, mypy, and all focused tests pass; failures contain setting names but none of the configured sentinel values.

- [ ] **Step 6: Commit the configuration boundary**

```bash
git add apps/api/app/core/config.py apps/api/app/core/http_origin.py apps/api/app/core/runtime_validation.py apps/api/tests/auth/test_clerk_auth_config.py apps/api/tests/test_deployment_readiness.py apps/api/tests/conftest.py
git commit -m "feat(api): validate Clerk authentication configuration"
```

---

### Task 2: Safe authentication failure vocabulary and bounded telemetry

**Files:**
- Create: `apps/api/app/core/auth_failures.py`
- Modify: `apps/api/app/core/observability.py`
- Modify: `apps/api/tests/test_observability.py`
- Create: `apps/api/tests/auth/test_auth_failures.py`

**Interfaces:**
- Consumes: `Observability`'s existing `_safe_label()` and `_safe_call()` patterns.
- Produces: `TokenRejected(reason: TokenRejectionReason)` and `AuthenticationUnavailable(reason: AuthenticationUnavailableReason)`, each with a fixed non-sensitive exception message and public bounded `reason` property.
- Produces telemetry methods: `record_auth_verification(outcome: str, reason: str) -> None`, `record_jwks_refresh(outcome: str, duration_seconds: float) -> None`, `record_jwks_coalesced_wait() -> None`, `record_jwks_stale_key_use() -> None`, and `record_jwks_refresh_cooldown(outcome: str) -> None`.

- [ ] **Step 1: Write failing safe-failure tests**

```python
import pytest

from app.core.auth_failures import AuthenticationUnavailable, TokenRejected


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (TokenRejected("authorized_party"), "token rejected"),
        (AuthenticationUnavailable("jwks_timeout"), "authentication unavailable"),
    ],
)
def test_auth_failures_expose_only_fixed_message_and_bounded_reason(
    failure: Exception, message: str
) -> None:
    assert str(failure) == message
    assert "SENSITIVE_PROVIDER_TEXT" not in repr(failure)


def test_auth_failure_retains_bounded_reason() -> None:
    assert TokenRejected("claims").reason == "claims"
```

Define the exact literal vocabularies:

```python
TokenRejectionReason = Literal[
    "malformed",
    "algorithm",
    "signature",
    "issuer",
    "audience",
    "claims",
    "authorized_party",
    "signing_key",
]
AuthenticationUnavailableReason = Literal[
    "jwks_timeout",
    "jwks_http",
    "jwks_invalid",
    "jwks_closed",
]
```

- [ ] **Step 2: Write failing observability instrument and label-boundary tests**

Add tests using the existing `_SpecificationMeter` and `_observability()` helpers:

```python
def test_authentication_instruments_and_attributes_are_bounded() -> None:
    meter = _SpecificationMeter()
    telemetry = _observability(meter=meter)

    assert meter.specifications["opevo.auth.verifications"] == ("counter", None)
    assert meter.specifications["opevo.auth.jwks.refreshes"] == ("counter", None)
    assert meter.specifications["opevo.auth.jwks.refresh.duration"] == (
        "histogram",
        "s",
    )
    telemetry.record_auth_verification("rejected", "authorized_party")
    telemetry.record_auth_verification("PRIVATE_OUTCOME", "PRIVATE_REASON")
    telemetry.record_jwks_refresh("success", 0.25)
    telemetry.record_jwks_coalesced_wait()
    telemetry.record_jwks_stale_key_use()
    telemetry.record_jwks_refresh_cooldown("rejected")

    assert meter.instruments["opevo.auth.verifications"].measurements == [
        (1, {"outcome": "rejected", "reason": "authorized_party"}),
        (1, {"outcome": "other", "reason": "other"}),
    ]
    assert "PRIVATE_OUTCOME" not in repr(meter.instruments)
    assert "PRIVATE_REASON" not in repr(meter.instruments)


def test_authentication_telemetry_failures_never_break_authentication(caplog) -> None:
    telemetry = _observability(meter=_Meter(failure=RuntimeError("METRIC_SECRET")))
    telemetry.record_auth_verification("rejected", "claims")
    telemetry.record_jwks_refresh("timeout", 1.0)
    assert "METRIC_SECRET" not in caplog.text
```

- [ ] **Step 3: Run the tests and verify they fail because the vocabulary/instruments are absent**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/auth/test_auth_failures.py tests/test_observability.py -k 'auth_failure or authentication_instrument or authentication_telemetry'
```

Expected: import/attribute failures for `auth_failures.py` and the new telemetry methods.

- [ ] **Step 4: Implement fixed-message failures and fixed-cardinality instruments**

Create `auth_failures.py` with frozen reason storage and no raw-cause chaining:

```python
from typing import Literal

TokenRejectionReason = Literal[
    "malformed", "algorithm", "signature", "issuer", "audience", "claims",
    "authorized_party", "signing_key",
]
AuthenticationUnavailableReason = Literal[
    "jwks_timeout", "jwks_http", "jwks_invalid", "jwks_closed",
]


class TokenRejected(Exception):
    def __init__(self, reason: TokenRejectionReason) -> None:
        super().__init__("token rejected")
        self.reason = reason


class AuthenticationUnavailable(Exception):
    def __init__(self, reason: AuthenticationUnavailableReason) -> None:
        super().__init__("authentication unavailable")
        self.reason = reason
```

In `observability.py`, add fixed sets for outcomes/reasons, construct five counters plus one duration histogram, and implement the five methods through `_safe_label()` and `_safe_call()`. The exact metric names are:

```python
"opevo.auth.verifications"
"opevo.auth.jwks.refreshes"
"opevo.auth.jwks.refresh.duration"
"opevo.auth.jwks.coalesced_waits"
"opevo.auth.jwks.stale_key_uses"
"opevo.auth.jwks.refresh_cooldowns"
```

The only accepted label values are:

```python
_AUTH_OUTCOMES = frozenset({"accepted", "rejected", "unavailable"})
_AUTH_REASONS = frozenset({
    "none", "malformed", "algorithm", "signature", "issuer", "audience",
    "claims", "authorized_party", "signing_key", "jwks_timeout",
    "jwks_http", "jwks_invalid", "jwks_closed",
})
_JWKS_REFRESH_OUTCOMES = frozenset(
    {"success", "timeout", "http_error", "invalid", "cancelled"}
)
_JWKS_COOLDOWN_OUTCOMES = frozenset({"rejected", "unavailable"})
```

Every unknown value becomes `other`; no method accepts a URL, token, subject, `kid`, or exception object.

- [ ] **Step 5: Run focused quality and tests**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app/core/auth_failures.py app/core/observability.py tests/auth/test_auth_failures.py tests/test_observability.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/auth/test_auth_failures.py tests/test_observability.py
```

Expected: all commands pass and no sentinel appears in captured logs or metric attributes.

- [ ] **Step 6: Commit the failure and telemetry contract**

```bash
git add apps/api/app/core/auth_failures.py apps/api/app/core/observability.py apps/api/tests/auth/test_auth_failures.py apps/api/tests/test_observability.py
git commit -m "feat(api): add bounded authentication failure telemetry"
```

---

### Task 3: Async JWKS resolver with coalesced refresh and bounded stale grace

**Files:**
- Create: `apps/api/app/core/clerk_jwks.py`
- Create: `apps/api/tests/auth/test_clerk_jwks.py`
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/uv.lock`

**Interfaces:**
- Consumes: Task 1 timeout/cache settings, `AuthenticationUnavailable`, `TokenRejected`, and Task 2 telemetry methods.
- Produces: `VerificationKey` type alias; `SigningKeyResolver` protocol with `async resolve_key(token: str) -> VerificationKey` and `async aclose() -> None`; `StaticSigningKeyResolver`; `JwksSigningKeyResolver`.
- Produces constructor: `JwksSigningKeyResolver(*, jwks_url: str, cache_ttl_seconds: float, stale_grace_seconds: float, connect_timeout_seconds: float, read_timeout_seconds: float, pool_timeout_seconds: float, total_timeout_seconds: float, observability: Observability, transport: httpx.AsyncBaseTransport | None = None, monotonic: Callable[[], float] = time.monotonic) -> None`.

- [ ] **Step 1: Promote httpx to a direct runtime dependency and update the lock**

Move `"httpx>=0.28,<1.0"` from `[dependency-groups].dev` to `[project].dependencies`, then run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv lock
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups
```

Expected: `httpx` appears as a direct project dependency in `uv.lock`; no unrelated package upgrade is accepted. Inspect `git diff -- apps/api/uv.lock` before proceeding.

- [ ] **Step 2: Write test helpers and failing static/header/JWKS document tests**

Create a fake monotonic clock, RSA JWK encoder, instrumented `httpx.MockTransport`, and exact request counter. The first group must cover:

```python
@pytest.mark.anyio
async def test_static_key_returns_configured_key_without_parsing_kid_or_http() -> None:
    resolver = StaticSigningKeyResolver("PUBLIC_KEY_SENTINEL")
    assert await resolver.resolve_key("token-without-a-jwt-header") == "PUBLIC_KEY_SENTINEL"
    await resolver.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("token", ["not-a-jwt", "e30.e30.", ""])
async def test_jwks_mode_rejects_malformed_headers_without_fetch(token: str) -> None:
    transport = CountingTransport(valid_jwks_response())
    resolver = resolver_for(transport=transport)
    with pytest.raises(TokenRejected) as exc_info:
        await resolver.resolve_key(token)
    assert exc_info.value.reason == "malformed"
    assert transport.request_count == 0


@pytest.mark.anyio
@pytest.mark.parametrize("kid", [None, "", 7, ["kid"], "x" * 129])
async def test_jwks_mode_requires_bounded_string_kid_without_fetch(kid: object) -> None:
    token = unsigned_token(headers={"alg": "RS256", **({} if kid is None else {"kid": kid})})
    transport = CountingTransport(valid_jwks_response())
    resolver = resolver_for(transport=transport)
    with pytest.raises(TokenRejected) as exc_info:
        await resolver.resolve_key(token)
    assert exc_info.value.reason in {"malformed", "signing_key"}
    assert transport.request_count == 0
```

Add parametrized provider-failure cases for status `301`, `302`, `404`, `429`, `500`; invalid JSON; JSON array; missing/non-list `keys`; empty keys; 17 keys; duplicate `kid`; unsupported `kty`; wrong `alg`; non-signing `use`; and a body of 262,145 bytes. Each asserts `AuthenticationUnavailable.reason` is bounded and the response/body sentinel is absent from the exception.

- [ ] **Step 3: Write failing cache, rotation, outage, and concurrency tests**

Use `asyncio.Event` barriers rather than sleeps. Include these exact assertions:

```python
@pytest.mark.anyio
async def test_many_concurrent_cold_requests_share_one_refresh() -> None:
    transport = BarrierTransport(valid_jwks_response(kids=("kid-a",)))
    resolver = resolver_for(transport=transport)
    tokens = [unsigned_token(headers={"alg": "RS256", "kid": "kid-a"}) for _ in range(40)]
    tasks = [asyncio.create_task(resolver.resolve_key(token)) for token in tokens]
    await transport.wait_until_requested()
    transport.release()
    keys = await asyncio.gather(*tasks)
    assert len(keys) == 40
    assert transport.request_count == 1


@pytest.mark.anyio
async def test_fresh_known_key_uses_cache_without_another_request() -> None:
    clock = FakeMonotonic(100.0)
    transport = SequencedTransport([valid_jwks_response(kids=("kid-a",))])
    resolver = resolver_for(transport=transport, monotonic=clock)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
    first = await resolver.resolve_key(token)
    clock.advance(300.0)
    second = await resolver.resolve_key(token)
    assert second is first
    assert transport.request_count == 1


@pytest.mark.anyio
async def test_failed_refresh_uses_only_known_key_inside_stale_grace() -> None:
    clock = FakeMonotonic(100.0)
    transport = SequencedTransport([
        valid_jwks_response(kids=("kid-a",)),
        httpx.ConnectTimeout("PROVIDER_SENTINEL"),
    ])
    resolver = resolver_for(transport=transport, monotonic=clock)
    known = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
    await resolver.resolve_key(known)
    clock.advance(300.001)
    assert await resolver.resolve_key(known)
    clock.advance(600.0)
    with pytest.raises(AuthenticationUnavailable):
        await resolver.resolve_key(known)


@pytest.mark.anyio
async def test_unknown_key_never_uses_stale_generation() -> None:
    clock = FakeMonotonic(100.0)
    transport = SequencedTransport([
        valid_jwks_response(kids=("kid-a",)),
        httpx.ConnectTimeout("PROVIDER_SENTINEL"),
    ])
    resolver = resolver_for(transport=transport, monotonic=clock)
    known = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
    unknown = unsigned_token(headers={"alg": "RS256", "kid": "kid-b"})
    await resolver.resolve_key(known)
    clock.advance(300.001)
    with pytest.raises(AuthenticationUnavailable) as exc_info:
        await resolver.resolve_key(unknown)
    assert exc_info.value.reason == "jwks_http"
    assert transport.request_count == 2


@pytest.mark.anyio
async def test_cancelled_waiter_does_not_cancel_shared_refresh() -> None:
    transport = BarrierTransport(valid_jwks_response(kids=("kid-a",)))
    resolver = resolver_for(transport=transport)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
    cancelled = asyncio.create_task(resolver.resolve_key(token))
    survivor = asyncio.create_task(resolver.resolve_key(token))
    await transport.wait_until_requested()
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    transport.release()
    assert await survivor
    assert transport.request_count == 1
```

Also cover exact boundaries `now == fresh_until`, `now == stale_until`, and one microstep after each; successful atomic rotation removes old keys; one refresh for concurrent unknown-key requests; successful-refresh cooldown rejects random kids without HTTP; failed-refresh cooldown returns unavailable without HTTP; connect/read/pool/total timeout mapping; a heartbeat task advances while the HTTP mock waits; and exact-once client/refresh shutdown.

- [ ] **Step 4: Run resolver tests and confirm import failures**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/auth/test_clerk_jwks.py
```

Expected: FAIL because `app.core.clerk_jwks` does not exist.

- [ ] **Step 5: Implement the explicit resolver state machine**

Use these core types and constants:

```python
MAX_KID_LENGTH = 128
MAX_JWKS_BODY_BYTES = 256 * 1024
MAX_JWKS_KEYS = 16
REFRESH_COOLDOWN_SECONDS = 5.0

type VerificationKey = AllowedPublicKeys | jwt.PyJWK | str | bytes


class SigningKeyResolver(Protocol):
    async def resolve_key(self, token: str) -> VerificationKey:
        raise NotImplementedError

    async def aclose(self) -> None:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _KeyGeneration:
    keys: Mapping[str, jwt.PyJWK]
    fetched_at: float
    fresh_until: float
    stale_until: float
```

`StaticSigningKeyResolver.resolve_key()` returns its captured PEM/string directly and `aclose()` is a no-op.

`JwksSigningKeyResolver` must:

1. Create and own one `httpx.AsyncClient` with `follow_redirects=False` and explicit connect/read/write/pool timeouts.
2. Extract only an unverified header `kid`, require a non-empty string of at most 128 characters, and never use it in a URL or telemetry label.
3. Create a refresh task before any await, store it in `_refresh_task`, and have all waiters use `await asyncio.shield(task)`.
4. Bound the shared fetch itself with `asyncio.timeout(total_timeout_seconds)` so a stream that continuously emits small chunks cannot run forever.
5. Read streaming bytes while rejecting a cumulative length over 256 KiB before JSON parsing.
6. Require an object containing 1–16 unique `kid` values; construct `jwt.PyJWK` instances; retain only `RSA`/`RS256` signing keys; reject an empty resulting map.
7. Assign a complete `_KeyGeneration` only after the entire document validates.
8. Record the completed-at time and bounded success/failure reason for every attempt to enforce the five-second cooldown.
9. Apply the exact fresh/cold/unknown/expired/stale rules from the design, with `<=` accepted at both deadlines.
10. On `aclose()`, mark closed, cancel and await an active refresh once, close the owned client once, and make subsequent resolution raise `AuthenticationUnavailable("jwks_closed")`.

The refresh entrypoint must translate all raw exceptions into fixed safe failures and telemetry without chaining provider text:

```python
try:
    async with asyncio.timeout(self._total_timeout_seconds):
        generation = await self._fetch_generation()
except (TimeoutError, httpx.TimeoutException):
    failure = AuthenticationUnavailable("jwks_timeout")
except (httpx.HTTPError, OSError):
    failure = AuthenticationUnavailable("jwks_http")
except _InvalidJwksDocument:
    failure = AuthenticationUnavailable("jwks_invalid")
```

Handle `asyncio.CancelledError` separately to record a bounded `cancelled` refresh outcome and re-raise it. Store only the bounded failure reason for cooldown replay; do not retain the raw exception, response, body, endpoint string, or token.

- [ ] **Step 6: Run focused resolver quality/tests and audit concurrency stability**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app/core/clerk_jwks.py tests/auth/test_clerk_jwks.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/auth/test_clerk_jwks.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/auth/test_clerk_jwks.py -k 'concurrent or cancelled or heartbeat'
```

Run the final command ten consecutive times without installing a repetition plugin. Expected: every run passes, request-count assertions stay exact, and no pending-task warning appears.

- [ ] **Step 7: Commit the resolver**

```bash
git add apps/api/pyproject.toml apps/api/uv.lock apps/api/app/core/clerk_jwks.py apps/api/tests/auth/test_clerk_jwks.py
git commit -m "feat(api): add bounded asynchronous Clerk JWKS resolver"
```

---

### Task 4: Strict async token verifier and application-scoped REST provider

**Files:**
- Modify: `apps/api/app/core/auth.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/webhooks/clerk.py`
- Modify: `apps/api/tests/conftest.py`
- Create: `apps/api/tests/auth/test_clerk_token_verifier.py`
- Modify: `apps/api/tests/auth/test_jwt_auth.py`
- Modify: `apps/api/tests/auth/test_local_auth.py`
- Modify: `apps/api/tests/integration/test_local_activation_to_number.py`
- Modify: `apps/api/tests/realtime/test_runtime_resources.py`

**Interfaces:**
- Consumes: Task 1 parsed authorized parties/settings, Task 2 failures/telemetry, Task 3 `SigningKeyResolver` implementations.
- Produces: `async AuthProvider.verify_token(token: str) -> UserIdentity`, default `async AuthProvider.aclose() -> None`, `build_auth_provider(*, settings: Settings, observability: Observability) -> AuthProvider`, and app-state-backed `get_auth_provider(request: Request) -> AuthProvider`.
- Produces: one `app.state.auth_provider` used by REST and passed unchanged to `RealtimeService` when that dormant service is constructed.

- [ ] **Step 1: Upgrade token fixtures and write failing claim/authorized-party tests**

Change the common token factory to preserve its one-argument call sites while allowing exact claim/header overrides:

```python
def _build(
    clerk_user_id: str,
    *,
    claims: dict[str, object] | None = None,
    headers: dict[str, object] | None = None,
) -> str:
    payload: dict[str, object] = {
        "sub": clerk_user_id,
        "iss": "https://clerk.example.com",
        "exp": 4102444800,
        "nbf": 0,
        "azp": TEST_CLERK_AUTHORIZED_PARTY,
    }
    if claims:
        payload.update(claims)
    return jwt.encode(payload, private_key_pem, algorithm="RS256", headers=headers)
```

For missing-claim cases, build tokens directly from a copied payload with the key removed; `None` is not equivalent to absent.

Create `tests/auth/test_clerk_token_verifier.py` with an async provider fixture using `StaticSigningKeyResolver`. Cover:

```python
@pytest.mark.anyio
async def test_static_verifier_accepts_complete_rs256_token(
    clerk_provider, rs256_clerk_token_for
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
        ["https://app.example.com"],
        "HTTPS://app.example.com",
        "https://app.example.com/",
        "https://app.example.com.evil.test",
        "https://evil.test/app.example.com",
        "prefix-https://app.example.com",
    ],
)
async def test_verifier_rejects_every_nonexact_authorized_party(
    clerk_provider, token_with_claims, azp: object
) -> None:
    with pytest.raises(TokenRejected) as exc_info:
        await clerk_provider.verify_token(token_with_claims(azp=azp))
    assert exc_info.value.reason == "authorized_party"


@pytest.mark.anyio
@pytest.mark.parametrize("claim", ["exp", "nbf", "sub", "azp"])
async def test_verifier_requires_each_security_claim(
    clerk_provider, token_without_claim, claim: str
) -> None:
    with pytest.raises(TokenRejected):
        await clerk_provider.verify_token(token_without_claim(claim))
```

Also assert: expired `exp`; future `nbf`; empty/non-string `sub`; wrong issuer; wrong configured audience; audience disabled; `HS256`, `none`, and header-algorithm confusion rejection; invalid signature; malformed token; two approved origins; and sentinels absent from exception/log/metric output. Add a JWKS-backed happy path to prove both resolvers use identical claim policy.

- [ ] **Step 2: Write failing application ownership and REST mapping tests**

Update `test_jwt_auth.py` and `test_runtime_resources.py` to assert:

```python
def test_request_auth_provider_returns_exact_app_scoped_instance() -> None:
    provider = object()
    app = FastAPI()
    app.state.auth_provider = provider
    request = Request({"type": "http", "app": app})
    assert get_auth_provider(request) is provider


@pytest.mark.anyio
async def test_rest_maps_rejected_token_to_generic_401(test_app) -> None:
    test_app.state.auth_provider = RejectingProvider(TokenRejected("authorized_party"))
    response = await request_protected_route(test_app, token="TOKEN_SENTINEL")
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid token"}
    assert "TOKEN_SENTINEL" not in response.text


@pytest.mark.anyio
async def test_rest_maps_provider_outage_to_generic_503(test_app) -> None:
    test_app.state.auth_provider = RejectingProvider(
        AuthenticationUnavailable("jwks_timeout")
    )
    response = await request_protected_route(test_app, token="TOKEN_SENTINEL")
    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication temporarily unavailable"}
```

In lifecycle tests, monkeypatch `build_auth_provider` and assert the captured provider is exactly `app.state.auth_provider`, exactly the object passed to `RealtimeService`, and its `aclose()` count is one after normal shutdown and after a later startup failure.

- [ ] **Step 3: Run the token/application tests and confirm sync/lifecycle failures**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/auth/test_clerk_token_verifier.py tests/auth/test_jwt_auth.py tests/auth/test_local_auth.py tests/realtime/test_runtime_resources.py
```

Expected: FAIL because provider verification is synchronous, claims/`azp` are not strict, the REST dependency constructs per request, and the lifespan does not own a provider.

- [ ] **Step 4: Implement async provider construction and strict claim policy**

Change the interface and construction boundary:

```python
class AuthProvider(ABC):
    @abstractmethod
    async def verify_token(self, token: str) -> UserIdentity:
        raise NotImplementedError

    async def get_user_id(self, token: str) -> str:
        return (await self.verify_token(token)).clerk_user_id

    async def aclose(self) -> None:
        return None


def build_auth_provider(*, settings: Settings, observability: Observability) -> AuthProvider:
    if settings.auth_mode == "local":
        return LocalAuthProvider(token=settings.local_auth_token)
    authorized_parties = frozenset(
        parse_canonical_http_origins(settings.clerk_authorized_parties)
    )
    if settings.clerk_jwt_key:
        resolver: SigningKeyResolver = StaticSigningKeyResolver(settings.clerk_jwt_key)
    else:
        resolver = JwksSigningKeyResolver(
            jwks_url=str(settings.clerk_jwks_url),
            cache_ttl_seconds=settings.clerk_jwks_cache_ttl_seconds,
            stale_grace_seconds=settings.clerk_jwks_stale_grace_seconds,
            connect_timeout_seconds=settings.clerk_jwks_connect_timeout_seconds,
            read_timeout_seconds=settings.clerk_jwks_read_timeout_seconds,
            pool_timeout_seconds=settings.clerk_jwks_pool_timeout_seconds,
            total_timeout_seconds=settings.clerk_jwks_total_timeout_seconds,
            observability=observability,
        )
    return ClerkAuthProvider(
        settings=settings,
        authorized_parties=authorized_parties,
        signing_key_resolver=resolver,
        observability=observability,
    )
```

`ClerkAuthProvider.verify_token()` awaits the resolver, calls `jwt.decode()` with `algorithms=["RS256"]`, exact issuer, `options={"require": ["exp", "nbf", "sub", "azp"], "verify_aud": configured_audience_is_present}`, and configured audience only when present. Then explicitly require `type(sub) is str and bool(sub)` and `type(azp) is str and azp in authorized_parties`.

Map PyJWT exceptions in most-specific-first order to the Task 2 reasons; always raise `TokenRejected(reason) from None`. Let an existing `AuthenticationUnavailable` pass through unchanged. Record one accepted/rejected/unavailable verification metric per call.

Make `LocalAuthProvider.verify_token()` async and raise `TokenRejected("signature")` for a mismatch; preserve constant-time comparison and the external generic 401 result. `ClerkAuthProvider.aclose()` delegates exactly once to its resolver. Keep `verify_webhook()` unchanged.

- [ ] **Step 5: Own one provider in FastAPI lifespan and map REST failures**

In `main.py`, initialize `app.state.auth_provider = None`, build it immediately after observability initialization, and pass that exact object to `RealtimeService`. In `finally`, close it after stopping realtime fanout and before shutting down observability:

```python
app.state.auth_provider = build_auth_provider(
    settings=settings,
    observability=app.state.observability,
)
if settings.realtime_enabled:
    app.state.realtime_service = RealtimeService(
        auth_provider=app.state.auth_provider,
        event_bus=realtime_event_bus,
        websocket_manager=websocket_manager,
        observability=app.state.observability,
    )

# In the lifespan's existing finally block, after stopping fanout:
await _close_runtime_resource(
    app.state.auth_provider,
    event="auth_provider_close_failed",
    operation="close_auth_provider",
)
```

`get_auth_provider()` returns the app-state object and raises a fixed internal error only if lifespan was not entered. `require_user_identity()` awaits verification and catches `TokenRejected` and `AuthenticationUnavailable` separately, logging only bounded event/reason data and returning the exact generic 401/503 responses. Do not catch raw `httpx`/PyJWT provider exceptions at the route boundary; those are normalized inside the provider/resolver.

Preserve FastAPI dependency overrides. Update every direct fake in the listed tests/integration file to `async def verify_token` and every direct call to `await provider.verify_token(token)`.

- [ ] **Step 6: Run focused and broad auth regressions**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app/core/auth.py app/main.py app/webhooks/clerk.py tests/auth tests/realtime/test_runtime_resources.py tests/integration/test_local_activation_to_number.py tests/conftest.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/auth tests/realtime/test_runtime_resources.py tests/integration/test_local_activation_to_number.py
```

`tests/auth/test_clerk_sync.py` is included by `tests/auth` and supplies the Svix webhook regression. Expected: strict session-token tests pass, local mode and Svix webhook behavior do not regress, and provider close counts are exactly one.

- [ ] **Step 7: Commit application-scoped verification**

```bash
git add apps/api/app/core/auth.py apps/api/app/main.py apps/api/app/webhooks/clerk.py apps/api/tests/conftest.py apps/api/tests/auth/test_clerk_token_verifier.py apps/api/tests/auth/test_jwt_auth.py apps/api/tests/auth/test_local_auth.py apps/api/tests/integration/test_local_activation_to_number.py apps/api/tests/realtime/test_runtime_resources.py
git commit -m "feat(api): scope strict Clerk verifier to application lifecycle"
```

---

### Task 5: Dormant WebSocket parity, cleanup, and deployment wiring

**Files:**
- Modify: `apps/api/app/services/realtime_service.py`
- Modify: `apps/api/app/routers/websocket.py`
- Modify: `apps/api/tests/realtime/test_websocket_auth.py`
- Modify: `apps/api/tests/realtime/test_websocket_lifecycle.py`
- Modify: `apps/api/tests/realtime/test_redis_fanout.py`
- Modify: `apps/api/tests/contracts/test_realtime_compatibility.py`
- Modify: `compose.yaml`
- Modify: `apps/api/.env.example`
- Modify: `.github/workflows/ci.yml`
- Modify: `apps/api/tests/test_deployment_readiness.py`

**Interfaces:**
- Consumes: async `AuthProvider.verify_token`, Task 2 typed failures, and the shared app-scoped provider from Task 4.
- Produces: exact WebSocket error frames/close codes and production API-only Clerk environment propagation; no runtime route enablement.

- [ ] **Step 1: Write failing WebSocket mapping and disconnect tests**

Replace the fake provider's raw PyJWT exception with configurable typed failures and make it async:

```python
class FakeAuthProvider(AuthProvider):
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure

    async def verify_token(self, token: str) -> UserIdentity:
        if self.failure is not None:
            raise self.failure
        if token != "valid-token":
            raise TokenRejected("signature")
        return UserIdentity(clerk_user_id="user_ws_test")
```

Add exact transport tests:

```python
@pytest.mark.parametrize(
    ("failure", "detail", "close_code"),
    [
        (TokenRejected("authorized_party"), "invalid_token", 1008),
        (AuthenticationUnavailable("jwks_timeout"), "auth_unavailable", 1013),
    ],
)
def test_websocket_maps_typed_auth_failure_to_safe_frame_and_close(
    ws_app_factory, failure: Exception, detail: str, close_code: int
) -> None:
    app, _ = ws_app_factory(auth_provider=FakeAuthProvider(failure))
    with TestClient(app).websocket_connect("/ws") as websocket:
        websocket.send_json({"type": "auth", "token": "TOKEN_SENTINEL"})
        assert websocket.receive_json() == {"type": "error", "detail": detail}
        with pytest.raises(StarletteWebSocketDisconnect) as exc_info:
            websocket.receive_json()
    assert exc_info.value.code == close_code
```

Add a manager spy proving an authenticated socket is disconnected exactly once on client disconnect and no disconnect is attempted before identity is established. Replace `tempfile.mkdtemp()` with pytest `tmp_path` so test artifacts are cleaned automatically.

- [ ] **Step 2: Write failing deployment-scope tests**

Extend `test_deployment_readiness.py` to parse/render Compose and assert:

```python
def test_production_compose_scopes_clerk_session_verifier_settings_to_api() -> None:
    document = load_compose_yaml()
    api_environment = resolved_service_environment(document, "api")
    assert "CLERK_AUTHORIZED_PARTIES" in api_environment
    assert "CLERK_JWT_KEY" in api_environment
    assert "CLERK_JWKS_URL" in api_environment
    for service in ("worker", "agent", "web"):
        environment = resolved_service_environment(document, service)
        assert "CLERK_AUTHORIZED_PARTIES" not in environment
        assert "CLERK_JWT_KEY" not in environment
        assert "CLERK_JWKS_URL" not in environment


def test_local_compose_keeps_local_auth_and_realtime_disabled() -> None:
    api_environment = local_compose_service_environment("api")
    assert api_environment["AUTH_MODE"] == "local"
    assert api_environment.get("REALTIME_ENABLED", "false") == "false"
    assert "CLERK_AUTHORIZED_PARTIES" not in api_environment
```

Also assert the production file does not force both key sources to non-empty values, the example documents every cache/timeout variable, and the real-origin variable contains only an illustrative value.

- [ ] **Step 3: Run WebSocket/deployment tests and confirm failures**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/realtime/test_websocket_auth.py tests/realtime/test_websocket_lifecycle.py tests/realtime/test_redis_fanout.py tests/contracts/test_realtime_compatibility.py tests/test_deployment_readiness.py
```

Expected: async call and typed-close assertions fail; production Compose lacks the authorized-party setting.

- [ ] **Step 4: Implement WebSocket-safe failure mapping without enabling realtime**

In `RealtimeService.authenticate()`, use:

```python
identity = await self.auth_provider.verify_token(message["token"])
```

In the router, remove the raw `jwt` dependency and use separate branches:

```python
except TokenRejected:
    await websocket.send_json({"type": "error", "detail": "invalid_token"})
    await websocket.close(code=1008)
except AuthenticationUnavailable:
    await websocket.send_json({"type": "error", "detail": "auth_unavailable"})
    await websocket.close(code=1013)
except WebSocketDisconnect:
    pass
finally:
    if user_id is not None:
        await realtime_service.websocket_manager.disconnect(user_id, websocket)
```

Responses contain no reason code or raw exception. Keep router registration guarded by `configured_settings.realtime_enabled`; do not change defaults, Compose flags, web settings, or E2E realtime settings.

- [ ] **Step 5: Wire production/test configuration explicitly**

In `compose.yaml`'s `x-api-environment` only, add:

```yaml
CLERK_AUTHORIZED_PARTIES: ${CLERK_AUTHORIZED_PARTIES:?CLERK_AUTHORIZED_PARTIES is required}
CLERK_JWT_KEY: ${CLERK_JWT_KEY:-}
CLERK_JWKS_URL: ${CLERK_JWKS_URL:-}
CLERK_JWKS_CACHE_TTL_SECONDS: ${CLERK_JWKS_CACHE_TTL_SECONDS:-300}
CLERK_JWKS_STALE_GRACE_SECONDS: ${CLERK_JWKS_STALE_GRACE_SECONDS:-600}
CLERK_JWKS_CONNECT_TIMEOUT_SECONDS: ${CLERK_JWKS_CONNECT_TIMEOUT_SECONDS:-0.5}
CLERK_JWKS_READ_TIMEOUT_SECONDS: ${CLERK_JWKS_READ_TIMEOUT_SECONDS:-1.0}
CLERK_JWKS_POOL_TIMEOUT_SECONDS: ${CLERK_JWKS_POOL_TIMEOUT_SECONDS:-0.25}
CLERK_JWKS_TOTAL_TIMEOUT_SECONDS: ${CLERK_JWKS_TOTAL_TIMEOUT_SECONDS:-2.0}
```

The runtime XOR validation decides whether static or JWKS mode is selected. Do not copy these variables into the worker anchor.

In `.github/workflows/ci.yml`'s API job environment, add non-secret construction-safe values:

```yaml
CLERK_ISSUER: https://clerk.example.com
CLERK_AUTHORIZED_PARTIES: https://app.example.com
CLERK_JWKS_URL: https://clerk.example.com/.well-known/jwks.json
```

No network request occurs during app construction. The autouse test fixture continues selecting its generated static key.

In `.env.example`, add a correctly named Clerk section documenting the allowlist, XOR key sources, and seven defaulted cache/timeout settings. Use `https://your-app.example.com`; do not include any actual deployment origin.

- [ ] **Step 6: Run focused quality, Compose rendering, and realtime-disabled assertions**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app/services/realtime_service.py app/routers/websocket.py tests/realtime tests/contracts/test_realtime_compatibility.py tests/test_deployment_readiness.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/realtime tests/contracts/test_realtime_compatibility.py tests/test_deployment_readiness.py
cd ../..
docker compose -f compose.dev.yaml config --quiet
env \
  ACTIVATION_FLOW_ENABLED=true \
  AGENT_DISPATCH_JWT_SECRET=test-only-test-only-test-only-test-only \
  AGENT_IMAGE=opevo-agent:verification \
  API_BASE_URL=https://api.example.invalid \
  API_IMAGE=opevo-api:verification \
  CLERK_AUTHORIZED_PARTIES=https://app.example.com \
  CLERK_ISSUER=https://clerk.example.com \
  CLERK_JWKS_URL=https://clerk.example.com/.well-known/jwks.json \
  CLERK_SECRET_KEY=disposable-clerk-secret \
  CLERK_WEBHOOK_SECRET=disposable-webhook-secret \
  CORS_ALLOWED_ORIGINS=https://app.example.com \
  DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/ai_call \
  GEMINI_API_KEY=disposable-gemini-key \
  LIVEKIT_API_KEY=disposable-livekit-key \
  LIVEKIT_API_SECRET=disposable-livekit-secret \
  LIVEKIT_URL=wss://livekit.example.invalid \
  NEXT_PUBLIC_API_BASE_URL=https://api.example.invalid \
  NEXT_PUBLIC_APP_URL=https://app.example.com \
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_disposable \
  REDIS_URL=redis://redis:6379/0 \
  S3_ACCESS_KEY=test-only-s3-key \
  S3_ENDPOINT_URL=https://s3.example.invalid \
  S3_REGION=us-east-1 \
  S3_SECRET_KEY=test-only-s3-secret \
  SPEECHMATICS_API_KEY=disposable-speechmatics-key \
  STORAGE_BUCKET_NAME=recordings \
  STRIPE_BILLING_PORTAL_CONFIGURATION_ID=bpc_disposable \
  STRIPE_BILLING_PORTAL_RETURN_URL=https://app.example.com/dashboard/billing \
  STRIPE_CHECKOUT_CANCEL_URL=https://app.example.com/billing/cancel \
  STRIPE_CHECKOUT_SUCCESS_URL=https://app.example.com/billing/success \
  STRIPE_PRICE_STARTER=price_disposable \
  STRIPE_SECRET_KEY=stripe-test-fixture \
  STRIPE_WEBHOOK_SECRET=whsec_disposable \
  SUMMARY_MODEL=gemini-2.5-flash \
  SUMMARY_PROVIDER=gemini \
  TELNYX_ACTIVE_CONNECTION_ID=disposable-active-connection \
  TELNYX_API_KEY=disposable-telnyx-key \
  TELNYX_DISABLED_CONNECTION_ID=disposable-disabled-connection \
  TELNYX_ORDERING_ENABLED=true \
  WEB_IMAGE=opevo-web:verification \
  docker compose -f compose.yaml config --quiet
```

Expected: both renders pass; the deployment-readiness assertions prove verifier settings appear only on API; realtime flags remain absent/false; the production render contains exactly one non-empty key source.

- [ ] **Step 7: Commit transport/deployment parity**

```bash
git add apps/api/app/services/realtime_service.py apps/api/app/routers/websocket.py apps/api/tests/realtime/test_websocket_auth.py apps/api/tests/realtime/test_websocket_lifecycle.py apps/api/tests/realtime/test_redis_fanout.py apps/api/tests/contracts/test_realtime_compatibility.py compose.yaml apps/api/.env.example .github/workflows/ci.yml apps/api/tests/test_deployment_readiness.py
git commit -m "feat(api): map Clerk auth failures across dormant realtime path"
```

---

### Task 6: Full regression proof, review ledger, and clean handoff

**Files:**
- Modify: `apps/api/coverage-baseline.json` only if measured line or branch coverage increases and the repository's ratchet procedure requires recording the higher result.
- Modify: `docs/engineering/2026-07-30-agent-api-review-decisions.md`
- Verify: all files changed by Tasks 1–5.

**Interfaces:**
- Consumes: all prior tasks and the approved design specification.
- Produces: passing repository gates, clean disposable resources, evidence-backed Issue 3A/13A completion status, and no realtime enablement.

- [ ] **Step 1: Run the complete API static and test gates with coverage**

Start disposable PostgreSQL and Redis containers with unique explicit names and ports, then run the same API contract as CI:

```bash
docker run --detach --name opevo-clerk-auth-test-postgres --env POSTGRES_DB=ai_call_test --env POSTGRES_USER=postgres --env POSTGRES_PASSWORD=postgres --publish 127.0.0.1:55432:5432 --health-cmd 'pg_isready -U postgres -d ai_call_test' --health-interval 2s --health-timeout 2s --health-retries 30 postgres:17.8-bookworm
docker run --detach --name opevo-clerk-auth-test-redis --publish 127.0.0.1:56379:6379 --health-cmd 'redis-cli ping' --health-interval 2s --health-timeout 2s --health-retries 30 redis:7.4.7-alpine
docker inspect --format '{{.State.Health.Status}}' opevo-clerk-auth-test-postgres
docker inspect --format '{{.State.Health.Status}}' opevo-clerk-auth-test-redis
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
CLIENT_TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/ai_call_test TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/ai_call_test TEST_REDIS_URL=redis://127.0.0.1:56379/0 UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q --cov=app --cov-report=term-missing --cov-report=json:coverage.json
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python ../../scripts/check_python_coverage.py check --report coverage.json --baseline coverage-baseline.json
```

Wait until both `docker inspect` commands report `healthy` before pytest. Expected: zero skips in API, no timeout, all tests pass, and both line and branch ratchets pass. If a sandboxed SQLite worker thread stalls, rerun the exact failing test outside the filesystem sandbox before diagnosing source code; record both outcomes.

- [ ] **Step 2: Run shared, agent, and web gates**

```bash
cd libs/shared
UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check src tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy src
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q

cd ../../apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q --cov=agent --cov-report=term-missing --cov-report=json:coverage.json
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python ../../scripts/check_python_coverage.py check --report coverage.json --baseline coverage-baseline.json

cd ../web
npm ci
npm run check
npm run typecheck
npm run test:ci
```

Expected: all shared gates pass; agent's four credentialed manual evaluations remain the only intentional skips; web tests pass without snapshot updates.

- [ ] **Step 3: Prove deployment and local E2E behavior**

From the monorepo root:

```bash
docker compose -f compose.dev.yaml config --quiet
bash scripts/run-local-e2e.sh
docker build -f apps/api/Dockerfile --target runtime .
```

Run the complete E2E suite twice without snapshot-update flags. Expected: both runs pass, local auth works, realtime remains disabled, and the API runtime image imports `httpx` plus the shared contracts from the monorepo-root build context.

- [ ] **Step 4: Audit security invariants and sensitive-data redaction**

Run targeted searches and inspect every match:

```bash
rg -n "PyJWKClient|get_signing_key_from_jwt|requests\.|urllib\.request" apps/api/app
rg -n "REALTIME_ENABLED|NEXT_PUBLIC_REALTIME_ENABLED" compose.yaml compose.dev.yaml apps/api/.env.example apps/web
rg -n "CLERK_AUTHORIZED_PARTIES|CLERK_JWT_KEY|CLERK_JWKS_URL" compose.yaml compose.dev.yaml .github/workflows/ci.yml
rg -n "token|azp|kid|jwks" apps/api/app/core/auth.py apps/api/app/core/clerk_jwks.py apps/api/app/core/observability.py
git diff --check
```

Expected: no synchronous JWKS client remains; no raw auth data is logged/labeled; authorized parties and verification sources are API-only in production; local Compose has no Clerk verifier settings; all realtime values/defaults remain false.

- [ ] **Step 5: Update the durable review ledger only after successful evidence**

In `docs/engineering/2026-07-30-agent-api-review-decisions.md`:

- Change Issue 3 from accepted/not implemented to **Accepted; implemented** and link the approved design plus this plan.
- Change Issue 13 from accepted/not implemented to **Accepted; implemented** and link the same artifacts.
- Record the final test counts, coverage results, Compose/image/E2E evidence, cache/transport policy, and the fact that no deployment or realtime enablement occurred.
- Preserve Issues 4, 5, 6, 7, 8, 1, 14, 15, and 16 as not implemented; preserve 11C, 12C, and 18A as accepted risk.
- State the agreed next order: 4, 5, 6, 7, 8; realtime 1A/14A later.

- [ ] **Step 6: Remove only disposable artifacts created by this work and inspect status**

Stop/remove the explicitly named test containers and the disposable E2E Compose project. Remove generated `coverage.json` files only after confirming they are untracked build artifacts. Do not inspect or touch `/home/mo/code/ai/bmad-opevo/Opevo_frontend/` or `.worktrees/shadcn-activation-preview`.

```bash
docker compose --project-name opevo-e2e -f compose.dev.yaml down --volumes --remove-orphans
docker rm --force opevo-clerk-auth-test-postgres
docker rm --force opevo-clerk-auth-test-redis
git status --short apps/api/coverage.json apps/agent/coverage.json
rm --force apps/api/coverage.json apps/agent/coverage.json
git status --short
git diff --stat
git diff --check
git log --oneline --decorate -8
```

Expected: only intentional source/docs/lock changes remain; no disposable container, network, volume, coverage report, or temporary test database remains. The reusable `/tmp/uv-cache` may remain and contains dependency cache data only, not application state.

- [ ] **Step 7: Perform final spec-coverage review and commit the evidence**

Compare every acceptance criterion in `docs/superpowers/specs/2026-08-01-clerk-auth-verifier-design.md` against tests and implementation. Specifically recheck exact TTL/grace boundaries, unknown-key behavior, failed-refresh cooldown, cancellation shielding, exact-once close, response codes, sentinel redaction, webhook/local non-regression, API-only Compose scope, and false realtime flags.

```bash
git add docs/engineering/2026-07-30-agent-api-review-decisions.md apps/api/coverage-baseline.json
git commit -m "docs: record Clerk verifier regression evidence"
```

If `apps/api/coverage-baseline.json` did not legitimately increase, omit it from `git add`. Do not commit generated reports.

- [ ] **Step 8: Final branch verification**

```bash
git status --short --branch
git diff --check HEAD~1
```

Expected: clean worktree on `feat/clerk-auth-verifier`, all planned commits present, and no claim of deployment, remote push, merge, or realtime activation.
