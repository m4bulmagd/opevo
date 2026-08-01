from __future__ import annotations

import asyncio
import ipaddress
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol
from urllib.request import getproxies

import httpx
import jwt
from jwt.algorithms import AllowedPublicKeys

from app.core.auth_failures import (
    AuthenticationUnavailable,
    AuthenticationUnavailableReason,
    TokenRejected,
)
from app.core.observability import Observability


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


class _InvalidJwksDocument(Exception):
    pass


def _reject_json_constant(value: str) -> None:
    del value
    raise ValueError


def _environment_proxy_url(url: str) -> str | None:
    """Select the fixed endpoint's route using HTTPX environment semantics."""
    target = httpx.URL(url)
    proxy_info = getproxies()
    no_proxy = proxy_info.get("no", "")
    if any(
        _no_proxy_host_matches(target, host.strip())
        for host in no_proxy.split(",")
        if host.strip()
    ):
        return None

    proxy_url = proxy_info.get(target.scheme) or proxy_info.get("all")
    if not proxy_url:
        return None
    return proxy_url if "://" in proxy_url else f"http://{proxy_url}"


def _no_proxy_host_matches(target: httpx.URL, host: str) -> bool:
    if host == "*":
        return True
    if "://" in host:
        pattern = httpx.URL(host)
    else:
        try:
            address = ipaddress.ip_address(host.split("/", maxsplit=1)[0])
        except ValueError:
            address = None
        if isinstance(address, ipaddress.IPv6Address):
            pattern = httpx.URL(f"all://[{host}]")
        elif address is not None or host.lower() == "localhost":
            pattern = httpx.URL(f"all://{host}")
        else:
            pattern = httpx.URL(f"all://*{host}")
    return _url_matches_pattern(target, pattern)


def _url_matches_pattern(target: httpx.URL, pattern: httpx.URL) -> bool:
    if pattern.scheme not in ("", "all", target.scheme):
        return False
    if pattern.port is not None and pattern.port != target.port:
        return False

    host = pattern.host
    if not host or host == "*":
        return True
    if host.startswith("*."):
        return target.host.endswith(host[1:]) and target.host != host[2:]
    if host.startswith("*"):
        domain = host[1:]
        return target.host == domain or target.host.endswith(f".{domain}")
    return target.host == host


class StaticSigningKeyResolver:
    def __init__(self, verification_key: VerificationKey) -> None:
        self._verification_key = verification_key

    async def resolve_key(self, token: str) -> VerificationKey:
        del token
        return self._verification_key

    async def aclose(self) -> None:
        return None


class JwksSigningKeyResolver:
    def __init__(
        self,
        *,
        jwks_url: str,
        cache_ttl_seconds: float,
        stale_grace_seconds: float,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        pool_timeout_seconds: float,
        total_timeout_seconds: float,
        observability: Observability,
        transport: httpx.AsyncBaseTransport | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._jwks_url = jwks_url
        self._cache_ttl_seconds = cache_ttl_seconds
        self._stale_grace_seconds = stale_grace_seconds
        self._total_timeout_seconds = total_timeout_seconds
        self._observability = observability
        self._monotonic = monotonic
        self._transport = (
            transport
            if transport is not None
            else httpx.AsyncHTTPTransport(proxy=_environment_proxy_url(jwks_url))
        )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=connect_timeout_seconds,
                read=read_timeout_seconds,
                write=read_timeout_seconds,
                pool=pool_timeout_seconds,
            ),
            follow_redirects=False,
            transport=self._transport,
        )
        self._generation: _KeyGeneration | None = None
        self._refresh_task: asyncio.Task[_KeyGeneration] | None = None
        self._last_refresh_completed_at: float | None = None
        self._last_refresh_failure: AuthenticationUnavailableReason | None = None
        self._closed = False
        self._client_close_attempted = False
        self._client_closed = False
        self._close_lock = asyncio.Lock()

    async def resolve_key(self, token: str) -> VerificationKey:
        if self._closed:
            raise AuthenticationUnavailable("jwks_closed")
        kid = self._extract_kid(token)

        now = self._monotonic()
        generation = self._generation
        known_key = None if generation is None else generation.keys.get(kid)
        if generation is not None and known_key is not None and now <= generation.fresh_until:
            return known_key

        cooldown_failure = self._cooldown_failure(now)
        if cooldown_failure is not None:
            stale_key = self._known_stale_key(generation, known_key, now)
            if stale_key is not None:
                return stale_key
            self._observability.record_jwks_refresh_cooldown("unavailable")
            raise AuthenticationUnavailable(cooldown_failure)
        if self._in_successful_cooldown(now):
            self._observability.record_jwks_refresh_cooldown("rejected")
            raise TokenRejected("signing_key")

        refresh_task = self._refresh_task
        if refresh_task is None:
            refresh_task = asyncio.create_task(self._refresh())
            self._refresh_task = refresh_task
            refresh_task.add_done_callback(self._consume_refresh_result)
        else:
            self._observability.record_jwks_coalesced_wait()

        try:
            await asyncio.shield(refresh_task)
        except AuthenticationUnavailable:
            now = self._monotonic()
            stale_key = self._known_stale_key(generation, known_key, now)
            if stale_key is not None:
                return stale_key
            raise
        finally:
            if refresh_task.done() and self._refresh_task is refresh_task:
                self._refresh_task = None

        generation = self._generation
        if generation is not None:
            key = generation.keys.get(kid)
            if key is not None:
                return key
        raise TokenRejected("signing_key")

    async def aclose(self) -> None:
        self._closed = True
        async with self._close_lock:
            if self._client_closed:
                return
            refresh_task = self._refresh_task
            try:
                if refresh_task is not None:
                    if not refresh_task.done():
                        refresh_task.cancel()
                    await refresh_task
            except (asyncio.CancelledError, AuthenticationUnavailable):
                pass
            finally:
                if self._refresh_task is refresh_task:
                    self._refresh_task = None
                if self._client_close_attempted:
                    await self._transport.aclose()
                else:
                    self._client_close_attempted = True
                    await self._client.aclose()
                self._client_closed = True

    def _extract_kid(self, token: str) -> str:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise TokenRejected("malformed") from None
        algorithm = header.get("alg")
        if algorithm is None:
            raise TokenRejected("malformed")
        if algorithm != "RS256":
            raise TokenRejected("algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid or len(kid) > MAX_KID_LENGTH:
            raise TokenRejected("signing_key")
        return kid

    def _cooldown_failure(
        self, now: float
    ) -> AuthenticationUnavailableReason | None:
        if not self._in_refresh_cooldown(now):
            return None
        return self._last_refresh_failure

    def _known_stale_key(
        self,
        generation: _KeyGeneration | None,
        known_key: VerificationKey | None,
        now: float,
    ) -> VerificationKey | None:
        if (
            generation is None
            or known_key is None
            or now > generation.stale_until
        ):
            return None
        self._observability.record_jwks_stale_key_use()
        return known_key

    def _in_successful_cooldown(self, now: float) -> bool:
        return self._in_refresh_cooldown(now) and self._last_refresh_failure is None

    def _in_refresh_cooldown(self, now: float) -> bool:
        completed_at = self._last_refresh_completed_at
        return (
            completed_at is not None
            and now < completed_at + REFRESH_COOLDOWN_SECONDS
        )

    @staticmethod
    def _consume_refresh_result(task: asyncio.Task[_KeyGeneration]) -> None:
        if not task.cancelled():
            task.exception()

    def _release_current_refresh(self) -> None:
        current_task = asyncio.current_task()
        if self._refresh_task is current_task:
            self._refresh_task = None

    async def _refresh(self) -> _KeyGeneration:
        started_at = self._monotonic()
        failure_reason: AuthenticationUnavailableReason | None = None
        outcome = "success"
        try:
            async with asyncio.timeout(self._total_timeout_seconds):
                generation = await self._fetch_generation()
        except asyncio.CancelledError:
            completed_at = self._monotonic()
            self._last_refresh_completed_at = completed_at
            self._observability.record_jwks_refresh(
                "cancelled", max(0.0, completed_at - started_at)
            )
            self._release_current_refresh()
            raise
        except (TimeoutError, httpx.TimeoutException):
            failure_reason = "jwks_timeout"
            outcome = "timeout"
        except (httpx.HTTPError, OSError):
            failure_reason = "jwks_http"
            outcome = "http_error"
        except _InvalidJwksDocument:
            failure_reason = "jwks_invalid"
            outcome = "invalid"

        completed_at = self._monotonic()
        self._last_refresh_completed_at = completed_at
        self._last_refresh_failure = failure_reason
        self._observability.record_jwks_refresh(
            outcome, max(0.0, completed_at - started_at)
        )
        if failure_reason is not None:
            self._release_current_refresh()
            raise AuthenticationUnavailable(failure_reason)
        self._generation = generation
        self._release_current_refresh()
        return generation

    async def _fetch_generation(self) -> _KeyGeneration:
        async with self._client.stream(
            "GET",
            self._jwks_url,
            headers={"Accept-Encoding": "identity"},
        ) as response:
            response.raise_for_status()
            content_encoding = response.headers.get("content-encoding")
            if (
                content_encoding is not None
                and content_encoding.strip().lower() != "identity"
            ):
                raise _InvalidJwksDocument
            body = bytearray()
            async for chunk in response.aiter_raw():
                if len(body) + len(chunk) > MAX_JWKS_BODY_BYTES:
                    raise _InvalidJwksDocument
                body.extend(chunk)
        try:
            document = json.loads(body, parse_constant=_reject_json_constant)
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise _InvalidJwksDocument from None
        keys = self._parse_keys(document)
        fetched_at = self._monotonic()
        fresh_until = fetched_at + self._cache_ttl_seconds
        return _KeyGeneration(
            keys=MappingProxyType(keys),
            fetched_at=fetched_at,
            fresh_until=fresh_until,
            stale_until=fresh_until + self._stale_grace_seconds,
        )

    def _parse_keys(self, document: Any) -> dict[str, jwt.PyJWK]:
        if not isinstance(document, dict):
            raise _InvalidJwksDocument
        raw_keys = document.get("keys")
        if (
            not isinstance(raw_keys, list)
            or not raw_keys
            or len(raw_keys) > MAX_JWKS_KEYS
        ):
            raise _InvalidJwksDocument

        seen_kids: set[str] = set()
        keys: dict[str, jwt.PyJWK] = {}
        for raw_key in raw_keys:
            if not isinstance(raw_key, dict):
                raise _InvalidJwksDocument
            kid = raw_key.get("kid")
            if (
                not isinstance(kid, str)
                or not kid
                or len(kid) > MAX_KID_LENGTH
                or kid in seen_kids
            ):
                raise _InvalidJwksDocument
            seen_kids.add(kid)
            if "key_ops" in raw_key:
                key_ops = raw_key["key_ops"]
                if (
                    not isinstance(key_ops, list)
                    or not all(isinstance(operation, str) for operation in key_ops)
                    or "verify" not in key_ops
                ):
                    raise _InvalidJwksDocument
            if (
                raw_key.get("kty") != "RSA"
                or raw_key.get("alg") != "RS256"
                or raw_key.get("use") not in (None, "sig")
            ):
                continue
            try:
                keys[kid] = jwt.PyJWK(raw_key)
            except (jwt.PyJWTError, TypeError, ValueError, OverflowError):
                raise _InvalidJwksDocument from None
        if not keys:
            raise _InvalidJwksDocument
        return keys
