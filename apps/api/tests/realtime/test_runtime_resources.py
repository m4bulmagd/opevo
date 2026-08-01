import asyncio
import logging
from types import SimpleNamespace

import pytest


class _Pool:
    def __init__(self, *, close_error: Exception | None = None) -> None:
        self.close_error = close_error
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1
        if self.close_error is not None:
            raise self.close_error


@pytest.mark.anyio
async def test_app_runtime_dependencies_use_captured_settings(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main as main_module

    configured = settings.model_copy(
        update={
            "app_env": "development",
            "redis_url": "redis://captured-settings.example:6380/7",
            "realtime_enabled": True,
        }
    )
    observed: dict[str, object] = {}
    pool = _Pool()

    class CapturingAuthProvider:
        def __init__(self) -> None:
            self.closed = 0

        async def aclose(self) -> None:
            self.closed += 1
            observed.setdefault("shutdown_order", []).append("auth_provider")

    provider = CapturingAuthProvider()

    def build_auth_provider(*, settings, observability):
        observed["auth_settings"] = settings
        observed["auth_observability"] = observability
        return provider

    class CapturingBus:
        def __init__(self, *, redis_url=None) -> None:
            observed["redis_url"] = redis_url
            self.closed = 0

        async def aclose(self) -> None:
            self.closed += 1
            observed["bus_closed"] = self.closed

    class WaitingRealtimeService:
        def __init__(self, auth_provider, *, event_bus, websocket_manager, observability) -> None:
            observed["auth_provider"] = auth_provider
            observed["event_bus"] = event_bus
            observed["observability"] = observability

        async def fanout_forever(self) -> None:
            await asyncio.Event().wait()

    async def create_pool(redis_url=None):
        observed["arq_redis_url"] = redis_url
        return pool

    async def shutdown_observability(observability) -> None:
        observed["shutdown_observability"] = observability
        observed.setdefault("shutdown_order", []).append("observability")

    monkeypatch.setattr(main_module, "build_auth_provider", build_auth_provider)
    monkeypatch.setattr(main_module, "RedisEventBus", CapturingBus)
    monkeypatch.setattr(main_module, "RealtimeService", WaitingRealtimeService)
    monkeypatch.setattr(main_module, "create_arq_pool", create_pool)
    monkeypatch.setattr(main_module, "shutdown_observability", shutdown_observability)

    app = main_module.create_app(configured)
    async with app.router.lifespan_context(app):
        assert app.state.settings is configured
        assert app.state.auth_provider is provider
        assert observed["auth_provider"] is provider
        assert observed["auth_observability"] is app.state.observability

    assert observed["auth_settings"] is configured
    assert observed["redis_url"] == configured.redis_url
    assert observed["arq_redis_url"] == configured.redis_url
    assert observed["bus_closed"] == 1
    assert pool.closed == 1
    assert provider.closed == 1
    assert observed["shutdown_order"] == ["auth_provider", "observability"]


@pytest.mark.anyio
async def test_explicit_redis_url_creates_and_closes_owned_event_bus_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import redis as redis_module

    observed: dict[str, object] = {}

    class Client:
        async def aclose(self) -> None:
            observed["closed"] = True

    client = Client()

    class RedisFactory:
        @staticmethod
        def from_url(redis_url: str, *, decode_responses: bool):
            observed["redis_url"] = redis_url
            observed["decode_responses"] = decode_responses
            return client

    monkeypatch.setattr(redis_module, "Redis", RedisFactory)
    try:
        bus = redis_module.RedisEventBus(redis_url="redis://captured.example/9")
    except TypeError as error:
        pytest.fail(f"RedisEventBus must accept an explicit redis_url: {error}")

    await bus.aclose()

    assert bus.redis_client is client
    assert observed == {
        "redis_url": "redis://captured.example/9",
        "decode_responses": True,
        "closed": True,
    }


@pytest.mark.anyio
async def test_explicit_arq_url_is_used_without_global_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import redis as redis_module

    observed: dict[str, object] = {}
    pool = object()

    async def create_pool(redis_settings):
        observed["host"] = redis_settings.host
        observed["port"] = redis_settings.port
        observed["database"] = redis_settings.database
        return pool

    monkeypatch.setattr(redis_module, "create_pool", create_pool)
    try:
        result = await redis_module.create_arq_pool(
            "redis://captured-arq.example:6381/11"
        )
    except TypeError as error:
        pytest.fail(f"create_arq_pool must accept an explicit redis_url: {error}")

    assert result is pool
    assert observed == {
        "host": "captured-arq.example",
        "port": 6381,
        "database": 11,
    }


@pytest.mark.anyio
async def test_lifespan_closes_arq_pool_when_later_startup_fails(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livekit import api as livekit_api_module

    from app import main as main_module

    configured = settings.model_copy(
        update={
            "app_env": "development",
            "realtime_enabled": False,
            "livekit_url": "wss://captured.livekit.example",
            "livekit_api_key": "captured-key",
            "livekit_api_secret": "captured-secret",
        }
    )
    pool = _Pool()
    shutdown_order: list[str] = []

    class Provider:
        def __init__(self) -> None:
            self.closed = 0

        async def aclose(self) -> None:
            self.closed += 1
            shutdown_order.append("auth_provider")

    provider = Provider()

    def build_auth_provider(*, settings, observability):
        del settings, observability
        return provider

    async def create_pool(redis_url=None):
        return pool

    class FailingVerifier:
        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError("STARTUP_PROVIDER_SECRET transcript")

    async def shutdown_observability(observability) -> None:
        del observability
        shutdown_order.append("observability")

    monkeypatch.setattr(main_module, "build_auth_provider", build_auth_provider)
    monkeypatch.setattr(main_module, "create_arq_pool", create_pool)
    monkeypatch.setattr(main_module, "shutdown_observability", shutdown_observability)
    monkeypatch.setattr(livekit_api_module, "TokenVerifier", FailingVerifier)

    app = main_module.create_app(configured)
    with pytest.raises(RuntimeError, match="STARTUP_PROVIDER_SECRET"):
        async with app.router.lifespan_context(app):
            pass

    assert pool.closed == 1
    assert app.state.auth_provider is provider
    assert provider.closed == 1
    assert shutdown_order == ["auth_provider", "observability"]


@pytest.mark.anyio
async def test_lifespan_safely_reports_relay_and_cleanup_failures(
    settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app import main as main_module

    configured = settings.model_copy(
        update={
            "app_env": "development",
            "redis_url": "redis://captured-settings.example:6380/7",
            "realtime_enabled": True,
        }
    )
    pool = _Pool(close_error=RuntimeError("ARQ_PROVIDER_SECRET customer text"))
    observed = SimpleNamespace(bus_close_calls=0)

    class ClosingBus:
        def __init__(self, *, redis_url=None) -> None:
            self.redis_url = redis_url

        async def aclose(self) -> None:
            observed.bus_close_calls += 1
            raise RuntimeError("REDIS_PROVIDER_SECRET customer text")

    class FailingRealtimeService:
        def __init__(self, auth_provider, *, event_bus, websocket_manager, observability) -> None:
            self.event_bus = event_bus

        async def fanout_forever(self) -> None:
            raise RuntimeError("RELAY_PROVIDER_SECRET customer text")

    async def create_pool(redis_url=None):
        return pool

    monkeypatch.setattr(main_module, "RedisEventBus", ClosingBus)
    monkeypatch.setattr(main_module, "RealtimeService", FailingRealtimeService)
    monkeypatch.setattr(main_module, "create_arq_pool", create_pool)

    app = main_module.create_app(configured)
    with caplog.at_level(logging.WARNING):
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0)

    assert observed.bus_close_calls == 1
    assert pool.closed == 1
    assert "event=realtime_fanout_failed" in caplog.text
    assert "event=realtime_bus_close_failed" in caplog.text
    assert "event=arq_pool_close_failed" in caplog.text
    for secret in (
        "RELAY_PROVIDER_SECRET",
        "REDIS_PROVIDER_SECRET",
        "ARQ_PROVIDER_SECRET",
        "customer text",
    ):
        assert secret not in caplog.text
