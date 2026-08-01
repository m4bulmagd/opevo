import asyncio
import json
from collections.abc import Callable, Sequence

import httpx
import jwt
import pytest
from opentelemetry import metrics, trace

from app.core.auth_failures import AuthenticationUnavailable, TokenRejected
from app.core.clerk_jwks import JwksSigningKeyResolver, StaticSigningKeyResolver
from app.core.observability import Observability


JWKS_URL = "https://clerk.example.com/.well-known/jwks.json"
BODY_SENTINEL = "RESPONSE_BODY_SENTINEL"
PROVIDER_SENTINEL = "PROVIDER_SENTINEL"


class FakeMonotonic:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class BytesStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def __aiter__(self):
        yield self.content


def as_network_response(response: httpx.Response) -> httpx.Response:
    if not response.is_stream_consumed:
        return response
    return httpx.Response(
        response.status_code,
        headers=response.headers,
        stream=BytesStream(response.content),
        extensions=response.extensions,
    )


class CountingTransport(httpx.AsyncBaseTransport):
    def __init__(self, outcome: httpx.Response | BaseException) -> None:
        self.outcome = outcome
        self.request_count = 0
        self.close_count = 0
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.request_count += 1
        self.requests.append(request)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return as_network_response(self.outcome)

    async def aclose(self) -> None:
        self.close_count += 1


class SequencedTransport(httpx.AsyncBaseTransport):
    def __init__(self, outcomes: Sequence[httpx.Response | BaseException]) -> None:
        self.outcomes = list(outcomes)
        self._last_outcome: httpx.Response | BaseException | None = None
        self.request_count = 0
        self.close_count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.request_count += 1
        if self.outcomes:
            self._last_outcome = self.outcomes.pop(0)
        if self._last_outcome is None:
            raise AssertionError("unexpected JWKS request")
        outcome = self._last_outcome
        if isinstance(outcome, BaseException):
            raise outcome
        return as_network_response(outcome)

    async def aclose(self) -> None:
        self.close_count += 1


class BarrierTransport(httpx.AsyncBaseTransport):
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.request_count = 0
        self.close_count = 0
        self.cancel_count = 0
        self._requested = asyncio.Event()
        self._release = asyncio.Event()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.request_count += 1
        self._requested.set()
        try:
            await self._release.wait()
        except asyncio.CancelledError:
            self.cancel_count += 1
            raise
        return as_network_response(self.response)

    async def wait_until_requested(self) -> None:
        await self._requested.wait()

    def release(self) -> None:
        self._release.set()

    async def aclose(self) -> None:
        self.close_count += 1


class FailingCloseTransport(CountingTransport):
    async def aclose(self) -> None:
        self.close_count += 1
        if self.close_count == 1:
            raise RuntimeError("CLIENT_CLOSE_SENTINEL")


class DistinctExitCloseTransport(FailingCloseTransport):
    def __init__(self, outcome: httpx.Response | BaseException) -> None:
        super().__init__(outcome)
        self.exit_count = 0

    async def __aexit__(self, *args: object) -> None:
        del args
        self.exit_count += 1


class CancellableCloseTransport(CountingTransport):
    def __init__(self, outcome: httpx.Response | BaseException) -> None:
        super().__init__(outcome)
        self.close_started = asyncio.Event()
        self._first_close_blocker = asyncio.Event()

    async def aclose(self) -> None:
        self.close_count += 1
        if self.close_count == 1:
            self.close_started.set()
            await self._first_close_blocker.wait()


class IterationGuardStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.iteration_count = 0
        self.close_count = 0

    async def __aiter__(self):
        self.iteration_count += 1
        raise AssertionError("encoded response body must not be iterated")
        yield b""  # pragma: no cover

    async def aclose(self) -> None:
        self.close_count += 1


class ContinuousSmallChunkStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.chunk_count = 0
        self.close_count = 0

    async def __aiter__(self):
        while self.chunk_count < 200:
            self.chunk_count += 1
            yield b" "
            await asyncio.sleep(0.001)

    async def aclose(self) -> None:
        self.close_count += 1


def _b64uint(value: int) -> str:
    width = (value.bit_length() + 7) // 8
    return jwt.utils.base64url_encode(value.to_bytes(width, "big")).decode()


def rsa_jwk(kid: str, **updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "kid": kid,
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "n": _b64uint((1 << 2048) - 159),
        "e": _b64uint(65537),
    }
    value.update(updates)
    return value


def valid_jwks_response(*, kids: Sequence[str] = ("kid-a",)) -> httpx.Response:
    return httpx.Response(200, json={"keys": [rsa_jwk(kid) for kid in kids]})


def unsigned_token(*, headers: dict[str, object]) -> str:
    encoded_header = jwt.utils.base64url_encode(
        json.dumps(headers, separators=(",", ":")).encode()
    ).decode()
    return f"{encoded_header}.e30."


def resolver_for(
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    monotonic: Callable[[], float] | None = None,
    total_timeout_seconds: float = 2.0,
    observability: Observability | None = None,
) -> JwksSigningKeyResolver:
    return JwksSigningKeyResolver(
        jwks_url=JWKS_URL,
        cache_ttl_seconds=300.0,
        stale_grace_seconds=600.0,
        connect_timeout_seconds=0.25,
        read_timeout_seconds=0.5,
        pool_timeout_seconds=0.1,
        total_timeout_seconds=total_timeout_seconds,
        observability=observability
        or Observability(
            meter=metrics.get_meter(__name__), tracer=trace.get_tracer(__name__)
        ),
        transport=transport,
        **({} if monotonic is None else {"monotonic": monotonic}),
    )


@pytest.mark.anyio
async def test_default_resolver_honors_https_proxy_and_no_proxy_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy_url = "http://proxy.example.com:8080"

    def transport_factory(**kwargs: object) -> CountingTransport:
        kid = "proxy-kid" if kwargs.get("proxy") == proxy_url else "direct-kid"
        return CountingTransport(valid_jwks_response(kids=(kid,)))

    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", transport_factory)
    monkeypatch.setenv("HTTPS_PROXY", proxy_url)

    proxied_resolver = resolver_for()
    proxy_token = unsigned_token(headers={"alg": "RS256", "kid": "proxy-kid"})
    assert await proxied_resolver.resolve_key(proxy_token)
    await proxied_resolver.aclose()

    monkeypatch.setenv("NO_PROXY", "clerk.example.com")
    direct_resolver = resolver_for()
    direct_token = unsigned_token(headers={"alg": "RS256", "kid": "direct-kid"})
    assert await direct_resolver.resolve_key(direct_token)
    await direct_resolver.aclose()


@pytest.mark.anyio
async def test_static_key_returns_configured_key_without_parsing_kid_or_http() -> None:
    resolver = StaticSigningKeyResolver("PUBLIC_KEY_SENTINEL")
    assert await resolver.resolve_key("token-without-a-jwt-header") == (
        "PUBLIC_KEY_SENTINEL"
    )
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
    await resolver.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("kid", [None, "", 7, ["kid"], "x" * 129])
async def test_jwks_mode_requires_bounded_string_kid_without_fetch(
    kid: object,
) -> None:
    token = unsigned_token(
        headers={"alg": "RS256", **({} if kid is None else {"kid": kid})}
    )
    transport = CountingTransport(valid_jwks_response())
    resolver = resolver_for(transport=transport)
    with pytest.raises(TokenRejected) as exc_info:
        await resolver.resolve_key(token)
    assert exc_info.value.reason in {"malformed", "signing_key"}
    assert transport.request_count == 0
    await resolver.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("alg", [None, "", "HS256", "RS512", 7])
async def test_jwks_mode_requires_rs256_algorithm_without_fetch(alg: object) -> None:
    headers: dict[str, object] = {"kid": "kid-a"}
    if alg is not None:
        headers["alg"] = alg
    transport = CountingTransport(valid_jwks_response())
    resolver = resolver_for(transport=transport)
    with pytest.raises(TokenRejected) as exc_info:
        await resolver.resolve_key(unsigned_token(headers=headers))
    assert exc_info.value.reason in {"malformed", "algorithm"}
    assert transport.request_count == 0
    await resolver.aclose()


@pytest.mark.anyio
async def test_jwks_request_requires_identity_content_encoding() -> None:
    transport = CountingTransport(valid_jwks_response())
    resolver = resolver_for(transport=transport)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})

    assert await resolver.resolve_key(token)

    assert transport.requests[0].headers["accept-encoding"] == "identity"
    await resolver.aclose()


@pytest.mark.anyio
async def test_encoded_jwks_response_is_rejected_before_body_iteration() -> None:
    stream = IterationGuardStream()
    transport = CountingTransport(
        httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            stream=stream,
        )
    )
    resolver = resolver_for(transport=transport)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})

    with pytest.raises(AuthenticationUnavailable) as exc_info:
        await resolver.resolve_key(token)

    assert exc_info.value.reason == "jwks_invalid"
    assert stream.iteration_count == 0
    assert stream.close_count == 1
    await resolver.aclose()


def _invalid_document_cases() -> list[tuple[str, httpx.Response]]:
    duplicate = rsa_jwk("kid-a")
    return [
        ("invalid-json", httpx.Response(200, content=b"{" + BODY_SENTINEL.encode())),
        (
            "excessively-nested-json",
            httpx.Response(200, content=b"[" * 10_000 + b"]" * 10_000),
        ),
        ("array", httpx.Response(200, json=[BODY_SENTINEL])),
        ("missing-keys", httpx.Response(200, json={"sentinel": BODY_SENTINEL})),
        ("non-list-keys", httpx.Response(200, json={"keys": {"kid": BODY_SENTINEL}})),
        ("empty-keys", httpx.Response(200, json={"keys": []})),
        (
            "too-many-keys",
            httpx.Response(200, json={"keys": [rsa_jwk(f"kid-{i}") for i in range(17)]}),
        ),
        ("duplicate-kid", httpx.Response(200, json={"keys": [duplicate, duplicate]})),
        (
            "unsupported-kty",
            httpx.Response(200, json={"keys": [rsa_jwk("kid-a", kty="EC")]}),
        ),
        (
            "wrong-alg",
            httpx.Response(200, json={"keys": [rsa_jwk("kid-a", alg="RS512")]}),
        ),
        (
            "non-signing-use",
            httpx.Response(200, json={"keys": [rsa_jwk("kid-a", use="enc")]}),
        ),
        (
            "invalid-rsa-material",
            httpx.Response(200, json={"keys": [rsa_jwk("kid-a", n=7)]}),
        ),
        (
            "oversized-body",
            httpx.Response(
                200,
                content=BODY_SENTINEL.encode()
                + b"x" * (262_145 - len(BODY_SENTINEL)),
            ),
        ),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("status", [301, 302, 404, 429, 500])
async def test_provider_status_is_bounded_http_failure(status: int) -> None:
    response = httpx.Response(status, content=BODY_SENTINEL.encode())
    transport = CountingTransport(response)
    resolver = resolver_for(transport=transport)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
    with pytest.raises(AuthenticationUnavailable) as exc_info:
        await resolver.resolve_key(token)
    assert exc_info.value.reason == "jwks_http"
    assert BODY_SENTINEL not in str(exc_info.value)
    assert BODY_SENTINEL not in repr(exc_info.value)
    await resolver.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(("case", "response"), _invalid_document_cases())
async def test_invalid_provider_document_is_bounded_failure(
    case: str, response: httpx.Response
) -> None:
    del case
    transport = CountingTransport(response)
    resolver = resolver_for(transport=transport)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
    with pytest.raises(AuthenticationUnavailable) as exc_info:
        await resolver.resolve_key(token)
    assert exc_info.value.reason == "jwks_invalid"
    assert BODY_SENTINEL not in str(exc_info.value)
    assert BODY_SENTINEL not in repr(exc_info.value)
    await resolver.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
async def test_jwks_rejects_nonstandard_json_numeric_constants(
    constant: str,
) -> None:
    body = (
        b'{"ignored":'
        + constant.encode()
        + b',"keys":['
        + json.dumps(rsa_jwk("kid-a"), separators=(",", ":")).encode()
        + b"]}"
    )
    transport = CountingTransport(httpx.Response(200, content=body))
    resolver = resolver_for(transport=transport)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})

    with pytest.raises(AuthenticationUnavailable) as exc_info:
        await resolver.resolve_key(token)

    assert exc_info.value.reason == "jwks_invalid"
    assert transport.request_count == 1
    await resolver.aclose()


@pytest.mark.anyio
async def test_huge_json_integer_is_safe_and_releases_refresh_for_retry() -> None:
    clock = FakeMonotonic(100.0)
    sentinel = "HUGE_INTEGER_BODY_SENTINEL"
    huge_integer_body = (
        b'{"number":'
        + b"9" * 10_000
        + b',"sentinel":"'
        + sentinel.encode()
        + b'","keys":[]}'
    )
    transport = CountingTransport(httpx.Response(200, content=huge_integer_body))
    resolver = resolver_for(transport=transport, monotonic=clock)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})

    for _ in range(2):
        with pytest.raises(AuthenticationUnavailable) as exc_info:
            await resolver.resolve_key(token)
        assert exc_info.value.reason == "jwks_invalid"
        assert sentinel not in str(exc_info.value)
        assert sentinel not in repr(exc_info.value)
        assert "integer string conversion" not in str(exc_info.value)
        assert "4300 digits" not in repr(exc_info.value)
    assert transport.request_count == 1

    clock.advance(5.001)
    transport.outcome = valid_jwks_response(kids=("kid-a",))
    assert await resolver.resolve_key(token)
    assert transport.request_count == 2

    await asyncio.gather(resolver.aclose(), resolver.aclose())
    await resolver.aclose()
    assert transport.close_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "key_ops",
    [
        pytest.param(["encrypt"], id="lacks-verify"),
        pytest.param([], id="empty-list"),
        pytest.param("verify", id="non-list"),
        pytest.param(["verify", 7], id="non-string-member"),
        pytest.param(None, id="null"),
    ],
)
async def test_jwks_rejects_invalid_verification_key_ops(key_ops: object) -> None:
    transport = CountingTransport(
        httpx.Response(
            200,
            json={"keys": [rsa_jwk("kid-a", key_ops=key_ops)]},
        )
    )
    resolver = resolver_for(transport=transport)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})

    with pytest.raises(AuthenticationUnavailable) as exc_info:
        await resolver.resolve_key(token)
    assert exc_info.value.reason == "jwks_invalid"
    assert transport.request_count == 1
    await resolver.aclose()


@pytest.mark.anyio
async def test_jwks_accepts_key_ops_with_verify() -> None:
    transport = CountingTransport(
        httpx.Response(
            200,
            json={"keys": [rsa_jwk("kid-a", key_ops=["verify"])]},
        )
    )
    resolver = resolver_for(transport=transport)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})

    assert await resolver.resolve_key(token)
    assert transport.request_count == 1
    await resolver.aclose()


@pytest.mark.anyio
async def test_many_concurrent_cold_requests_share_one_refresh() -> None:
    transport = BarrierTransport(valid_jwks_response(kids=("kid-a",)))
    resolver = resolver_for(transport=transport)
    tokens = [
        unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
        for _ in range(40)
    ]
    tasks = [asyncio.create_task(resolver.resolve_key(token)) for token in tokens]
    await transport.wait_until_requested()
    transport.release()
    keys = await asyncio.gather(*tasks)
    assert len(keys) == 40
    assert transport.request_count == 1
    await resolver.aclose()


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
    await resolver.aclose()


@pytest.mark.anyio
async def test_one_microstep_after_fresh_deadline_refreshes() -> None:
    clock = FakeMonotonic(100.0)
    transport = SequencedTransport(
        [valid_jwks_response(kids=("kid-a",)), valid_jwks_response(kids=("kid-a",))]
    )
    resolver = resolver_for(transport=transport, monotonic=clock)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
    await resolver.resolve_key(token)
    clock.advance(300.001)
    await resolver.resolve_key(token)
    assert transport.request_count == 2
    await resolver.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(("elapsed", "accepted"), [(900.0, True), (900.001, False)])
async def test_stale_deadline_is_inclusive(elapsed: float, accepted: bool) -> None:
    clock = FakeMonotonic(100.0)
    transport = SequencedTransport(
        [valid_jwks_response(kids=("kid-a",)), httpx.ConnectTimeout(PROVIDER_SENTINEL)]
    )
    resolver = resolver_for(transport=transport, monotonic=clock)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
    await resolver.resolve_key(token)
    clock.advance(elapsed)
    if accepted:
        assert await resolver.resolve_key(token)
    else:
        with pytest.raises(AuthenticationUnavailable):
            await resolver.resolve_key(token)
    assert transport.request_count == 2
    await resolver.aclose()


@pytest.mark.anyio
async def test_failed_refresh_uses_only_known_key_inside_stale_grace() -> None:
    clock = FakeMonotonic(100.0)
    transport = SequencedTransport(
        [valid_jwks_response(kids=("kid-a",)), httpx.ConnectTimeout(PROVIDER_SENTINEL)]
    )
    resolver = resolver_for(transport=transport, monotonic=clock)
    known = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
    await resolver.resolve_key(known)
    clock.advance(300.001)
    assert await resolver.resolve_key(known)
    clock.advance(600.0)
    with pytest.raises(AuthenticationUnavailable):
        await resolver.resolve_key(known)
    await resolver.aclose()


@pytest.mark.anyio
async def test_unknown_key_never_uses_stale_generation() -> None:
    clock = FakeMonotonic(100.0)
    transport = SequencedTransport(
        [valid_jwks_response(kids=("kid-a",)), httpx.ConnectTimeout(PROVIDER_SENTINEL)]
    )
    resolver = resolver_for(transport=transport, monotonic=clock)
    known = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
    unknown = unsigned_token(headers={"alg": "RS256", "kid": "kid-b"})
    await resolver.resolve_key(known)
    clock.advance(300.001)
    with pytest.raises(AuthenticationUnavailable) as exc_info:
        await resolver.resolve_key(unknown)
    assert exc_info.value.reason == "jwks_timeout"
    assert transport.request_count == 2
    await resolver.aclose()


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
    await resolver.aclose()


@pytest.mark.anyio
async def test_completed_refresh_is_released_after_its_only_waiter_cancels() -> None:
    class CompletionObservability(Observability):
        def __init__(self) -> None:
            super().__init__(
                meter=metrics.get_meter(__name__), tracer=trace.get_tracer(__name__)
            )
            self.refresh_completed = asyncio.Event()

        def record_jwks_refresh(self, outcome: str, duration_seconds: float) -> None:
            super().record_jwks_refresh(outcome, duration_seconds)
            self.refresh_completed.set()

    clock = FakeMonotonic(100.0)
    first = BarrierTransport(valid_jwks_response(kids=("kid-a",)))
    second = CountingTransport(valid_jwks_response(kids=("kid-b",)))
    telemetry = CompletionObservability()

    class SwitchingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if first.request_count == 0:
                return await first.handle_async_request(request)
            return await second.handle_async_request(request)

        async def aclose(self) -> None:
            await first.aclose()
            await second.aclose()

    resolver = resolver_for(
        transport=SwitchingTransport(), monotonic=clock, observability=telemetry
    )
    abandoned = asyncio.create_task(
        resolver.resolve_key(unsigned_token(headers={"alg": "RS256", "kid": "kid-a"}))
    )
    await first.wait_until_requested()
    abandoned.cancel()
    with pytest.raises(asyncio.CancelledError):
        await abandoned
    first.release()
    await telemetry.refresh_completed.wait()
    clock.advance(5.001)

    assert await resolver.resolve_key(
        unsigned_token(headers={"alg": "RS256", "kid": "kid-b"})
    )
    assert first.request_count + second.request_count == 2
    await resolver.aclose()


@pytest.mark.anyio
async def test_successful_rotation_atomically_removes_old_keys() -> None:
    clock = FakeMonotonic(100.0)
    transport = SequencedTransport(
        [valid_jwks_response(kids=("kid-a",)), valid_jwks_response(kids=("kid-b",))]
    )
    resolver = resolver_for(transport=transport, monotonic=clock)
    old = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
    new = unsigned_token(headers={"alg": "RS256", "kid": "kid-b"})
    await resolver.resolve_key(old)
    clock.advance(300.001)
    assert await resolver.resolve_key(new)
    with pytest.raises(TokenRejected) as exc_info:
        await resolver.resolve_key(old)
    assert exc_info.value.reason == "signing_key"
    assert transport.request_count == 2
    await resolver.aclose()


@pytest.mark.anyio
async def test_concurrent_unknown_key_requests_share_one_refresh() -> None:
    clock = FakeMonotonic(100.0)
    first = valid_jwks_response(kids=("kid-a",))
    barrier = BarrierTransport(valid_jwks_response(kids=("kid-a", "kid-b")))
    initial = CountingTransport(first)

    class SwitchingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if initial.request_count == 0:
                return await initial.handle_async_request(request)
            return await barrier.handle_async_request(request)

        async def aclose(self) -> None:
            await initial.aclose()
            await barrier.aclose()

    resolver = resolver_for(transport=SwitchingTransport(), monotonic=clock)
    await resolver.resolve_key(unsigned_token(headers={"alg": "RS256", "kid": "kid-a"}))
    clock.advance(5.001)
    unknown = unsigned_token(headers={"alg": "RS256", "kid": "kid-b"})
    tasks = [asyncio.create_task(resolver.resolve_key(unknown)) for _ in range(20)]
    await barrier.wait_until_requested()
    barrier.release()
    assert len(await asyncio.gather(*tasks)) == 20
    assert initial.request_count + barrier.request_count == 2
    await resolver.aclose()


@pytest.mark.anyio
async def test_successful_refresh_cooldown_rejects_unknown_key_without_http() -> None:
    clock = FakeMonotonic(100.0)
    transport = SequencedTransport([valid_jwks_response(kids=("kid-a",))])
    resolver = resolver_for(transport=transport, monotonic=clock)
    await resolver.resolve_key(unsigned_token(headers={"alg": "RS256", "kid": "kid-a"}))
    with pytest.raises(TokenRejected) as exc_info:
        await resolver.resolve_key(
            unsigned_token(headers={"alg": "RS256", "kid": "random-kid"})
        )
    assert exc_info.value.reason == "signing_key"
    assert transport.request_count == 1
    await resolver.aclose()


@pytest.mark.anyio
async def test_successful_refresh_cooldown_ends_at_exactly_five_seconds() -> None:
    clock = FakeMonotonic(100.0)
    transport = SequencedTransport(
        [valid_jwks_response(kids=("kid-a",)), valid_jwks_response(kids=("kid-b",))]
    )
    resolver = resolver_for(transport=transport, monotonic=clock)
    await resolver.resolve_key(unsigned_token(headers={"alg": "RS256", "kid": "kid-a"}))
    unknown = unsigned_token(headers={"alg": "RS256", "kid": "kid-b"})

    clock.value = 104.999
    with pytest.raises(TokenRejected) as exc_info:
        await resolver.resolve_key(unknown)
    assert exc_info.value.reason == "signing_key"
    assert transport.request_count == 1

    clock.value = 105.0
    assert await resolver.resolve_key(unknown)
    assert transport.request_count == 2
    await resolver.aclose()


@pytest.mark.anyio
async def test_successful_refresh_without_unknown_key_rejects_after_one_fetch() -> None:
    clock = FakeMonotonic(100.0)
    transport = SequencedTransport(
        [valid_jwks_response(kids=("kid-a",)), valid_jwks_response(kids=("kid-a",))]
    )
    resolver = resolver_for(transport=transport, monotonic=clock)
    await resolver.resolve_key(unsigned_token(headers={"alg": "RS256", "kid": "kid-a"}))
    clock.value = 105.0

    with pytest.raises(TokenRejected) as exc_info:
        await resolver.resolve_key(
            unsigned_token(headers={"alg": "RS256", "kid": "kid-b"})
        )

    assert exc_info.value.reason == "signing_key"
    assert transport.request_count == 2
    await resolver.aclose()


@pytest.mark.anyio
async def test_failed_refresh_cooldown_replays_bounded_failure_without_http() -> None:
    clock = FakeMonotonic(100.0)
    transport = SequencedTransport([httpx.ConnectTimeout(PROVIDER_SENTINEL)])
    resolver = resolver_for(transport=transport, monotonic=clock)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
    for _ in range(2):
        with pytest.raises(AuthenticationUnavailable) as exc_info:
            await resolver.resolve_key(token)
        assert exc_info.value.reason == "jwks_timeout"
        assert PROVIDER_SENTINEL not in repr(exc_info.value)
    assert transport.request_count == 1
    await resolver.aclose()


@pytest.mark.anyio
async def test_failed_refresh_cooldown_ends_at_exactly_five_seconds() -> None:
    clock = FakeMonotonic(100.0)
    transport = SequencedTransport(
        [
            httpx.ConnectTimeout(PROVIDER_SENTINEL),
            valid_jwks_response(kids=("kid-a",)),
        ]
    )
    resolver = resolver_for(transport=transport, monotonic=clock)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
    with pytest.raises(AuthenticationUnavailable):
        await resolver.resolve_key(token)

    clock.value = 104.999
    with pytest.raises(AuthenticationUnavailable) as exc_info:
        await resolver.resolve_key(token)
    assert exc_info.value.reason == "jwks_timeout"
    assert transport.request_count == 1

    clock.value = 105.0
    assert await resolver.resolve_key(token)
    assert transport.request_count == 2
    await resolver.aclose()


@pytest.mark.anyio
async def test_known_stale_key_is_reused_during_active_failed_cooldown() -> None:
    clock = FakeMonotonic(100.0)
    transport = SequencedTransport(
        [valid_jwks_response(kids=("kid-a",)), httpx.ReadTimeout(PROVIDER_SENTINEL)]
    )
    resolver = resolver_for(transport=transport, monotonic=clock)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
    fresh_key = await resolver.resolve_key(token)

    clock.value = 400.001
    post_failure_key = await resolver.resolve_key(token)
    assert post_failure_key is fresh_key
    assert transport.request_count == 2

    clock.value = 401.0
    cooldown_key = await resolver.resolve_key(token)
    assert cooldown_key is fresh_key
    assert transport.request_count == 2
    await resolver.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (httpx.ConnectTimeout(PROVIDER_SENTINEL), "jwks_timeout"),
        (httpx.ReadTimeout(PROVIDER_SENTINEL), "jwks_timeout"),
        (httpx.PoolTimeout(PROVIDER_SENTINEL), "jwks_timeout"),
        (httpx.WriteTimeout(PROVIDER_SENTINEL), "jwks_timeout"),
        (TimeoutError(PROVIDER_SENTINEL), "jwks_timeout"),
        (httpx.ConnectError(PROVIDER_SENTINEL), "jwks_http"),
        (OSError(PROVIDER_SENTINEL), "jwks_http"),
    ],
)
async def test_transport_failures_map_to_bounded_reason(
    failure: BaseException, reason: str
) -> None:
    transport = CountingTransport(failure)
    resolver = resolver_for(transport=transport)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
    with pytest.raises(AuthenticationUnavailable) as exc_info:
        await resolver.resolve_key(token)
    assert exc_info.value.reason == reason
    assert PROVIDER_SENTINEL not in str(exc_info.value)
    assert PROVIDER_SENTINEL not in repr(exc_info.value)
    await resolver.aclose()


@pytest.mark.anyio
async def test_total_deadline_maps_to_bounded_timeout_reason() -> None:
    stream = ContinuousSmallChunkStream()
    transport = CountingTransport(httpx.Response(200, stream=stream))
    resolver = resolver_for(transport=transport, total_timeout_seconds=0.05)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})

    with pytest.raises(AuthenticationUnavailable) as exc_info:
        await resolver.resolve_key(token)

    assert exc_info.value.reason == "jwks_timeout"
    assert transport.request_count == 1
    assert stream.chunk_count >= 1
    assert stream.close_count == 1
    await resolver.aclose()


@pytest.mark.anyio
async def test_heartbeat_advances_while_http_request_waits() -> None:
    transport = BarrierTransport(valid_jwks_response(kids=("kid-a",)))
    resolver = resolver_for(transport=transport)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
    resolution = asyncio.create_task(resolver.resolve_key(token))
    await transport.wait_until_requested()
    heartbeat_complete = asyncio.Event()

    async def heartbeat() -> None:
        for _ in range(20):
            await asyncio.sleep(0)
        heartbeat_complete.set()

    heartbeat_task = asyncio.create_task(heartbeat())
    await heartbeat_complete.wait()
    assert not resolution.done()
    transport.release()
    assert await resolution
    await heartbeat_task
    await resolver.aclose()


@pytest.mark.anyio
async def test_aclose_cancels_refresh_and_closes_owned_client_exactly_once() -> None:
    transport = BarrierTransport(valid_jwks_response(kids=("kid-a",)))
    resolver = resolver_for(transport=transport)
    token = unsigned_token(headers={"alg": "RS256", "kid": "kid-a"})
    resolution = asyncio.create_task(resolver.resolve_key(token))
    await transport.wait_until_requested()
    await asyncio.gather(resolver.aclose(), resolver.aclose())
    with pytest.raises(asyncio.CancelledError):
        await resolution
    assert transport.cancel_count == 1
    assert transport.close_count == 1
    await resolver.aclose()
    assert transport.close_count == 1
    with pytest.raises(AuthenticationUnavailable) as exc_info:
        await resolver.resolve_key(token)
    assert exc_info.value.reason == "jwks_closed"
    assert transport.request_count == 1


@pytest.mark.anyio
async def test_aclose_retries_client_close_after_exception() -> None:
    transport = FailingCloseTransport(valid_jwks_response())
    resolver = resolver_for(transport=transport)

    with pytest.raises(RuntimeError, match="CLIENT_CLOSE_SENTINEL"):
        await resolver.aclose()
    assert transport.close_count == 1

    await resolver.aclose()
    assert transport.close_count == 2
    await resolver.aclose()
    assert transport.close_count == 2


@pytest.mark.anyio
async def test_aclose_retry_uses_actual_close_not_transport_context_exit() -> None:
    transport = DistinctExitCloseTransport(valid_jwks_response())
    resolver = resolver_for(transport=transport)

    with pytest.raises(RuntimeError, match="CLIENT_CLOSE_SENTINEL"):
        await resolver.aclose()
    assert transport.close_count == 1
    assert transport.exit_count == 0

    await resolver.aclose()
    assert transport.close_count == 2
    assert transport.exit_count == 0
    await resolver.aclose()
    assert transport.close_count == 2
    assert transport.exit_count == 0


@pytest.mark.anyio
async def test_aclose_retries_client_close_after_cancellation() -> None:
    transport = CancellableCloseTransport(valid_jwks_response())
    resolver = resolver_for(transport=transport)
    first_close = asyncio.create_task(resolver.aclose())
    await transport.close_started.wait()

    first_close.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_close
    assert transport.close_count == 1

    await resolver.aclose()
    assert transport.close_count == 2
    await resolver.aclose()
    assert transport.close_count == 2
