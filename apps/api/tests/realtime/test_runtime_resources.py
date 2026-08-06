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
) -> None:
    from app.composition.api import build_api_runtime

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

    class Engine:
        async def dispose(self) -> None:
            observed.setdefault("shutdown_order", []).append("engine")

    class RedisClient:
        async def aclose(self) -> None:
            observed.setdefault("shutdown_order", []).append("redis")

    class Storage:
        async def aclose(self) -> None:
            observed.setdefault("shutdown_order", []).append("storage")

    class Observability:
        async def aclose(self) -> None:
            observed.setdefault("shutdown_order", []).append("observability")

    engine = Engine()
    redis_client = RedisClient()
    storage = Storage()
    observability = Observability()

    class WaitingRealtimeService:
        def __init__(self, auth_provider, *, event_bus, websocket_manager, observability) -> None:
            observed["auth_provider"] = auth_provider
            observed["event_bus"] = event_bus
            observed["observability"] = observability

        async def fanout_forever(self) -> None:
            await asyncio.Event().wait()

    async def create_pool(redis_url: str):
        observed["arq_redis_url"] = redis_url
        return pool

    def create_redis(redis_url: str):
        observed["redis_url"] = redis_url
        return redis_client

    runtime = await build_api_runtime(
        configured,
        engine_factory=lambda _url: engine,
        redis_factory=create_redis,
        observability_factory=lambda **_kwargs: observability,
        auth_factory=build_auth_provider,
        readiness_factory=lambda **_kwargs: object(),
        storage_factory=lambda **_kwargs: storage,
        arq_pool_factory=create_pool,
        realtime_service_factory=WaitingRealtimeService,
        webhook_receiver_factory=lambda **_kwargs: object(),
        recording_service_factory=lambda **_kwargs: object(),
    )
    assert runtime.settings is configured
    assert observed["auth_provider"] is provider
    assert observed["auth_observability"] is observability
    assert observed["event_bus"].redis_client is redis_client

    await runtime.aclose()

    assert observed["auth_settings"] is configured
    assert observed["redis_url"] == configured.redis_url
    assert observed["arq_redis_url"] == configured.redis_url
    assert pool.closed == 1
    assert provider.closed == 1
    assert observed["shutdown_order"] == [
        "storage",
        "auth_provider",
        "redis",
        "engine",
        "observability",
    ]


@pytest.mark.anyio
async def test_event_bus_borrows_explicit_runtime_client_without_closing_it() -> None:
    from app.core import redis as redis_module

    class Client:
        def __init__(self) -> None:
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1

    client = Client()
    bus = redis_module.RedisEventBus(client)

    assert bus.redis_client is client
    assert not hasattr(bus, "aclose")
    assert client.close_calls == 0


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
) -> None:
    from app.composition.api import build_api_runtime

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

    async def create_pool(redis_url: str):
        del redis_url
        return pool

    class Engine:
        async def dispose(self) -> None:
            shutdown_order.append("engine")

    class RedisClient:
        async def aclose(self) -> None:
            shutdown_order.append("redis")

    class Resource:
        def __init__(self, name: str) -> None:
            self.name = name

        async def aclose(self) -> None:
            shutdown_order.append(self.name)

    def fail_webhook_receiver(**_kwargs):
        raise RuntimeError("STARTUP_PROVIDER_SECRET transcript")

    with pytest.raises(RuntimeError, match="STARTUP_PROVIDER_SECRET"):
        await build_api_runtime(
            configured,
            engine_factory=lambda _url: Engine(),
            redis_factory=lambda _url: RedisClient(),
            observability_factory=lambda **_kwargs: Resource("observability"),
            auth_factory=build_auth_provider,
            readiness_factory=lambda **_kwargs: object(),
            storage_factory=lambda **_kwargs: Resource("storage"),
            arq_pool_factory=create_pool,
            realtime_service_factory=lambda *_args, **_kwargs: object(),
            webhook_receiver_factory=fail_webhook_receiver,
            recording_service_factory=lambda **_kwargs: object(),
        )

    assert pool.closed == 1
    assert provider.closed == 1
    assert shutdown_order == [
        "storage",
        "auth_provider",
        "redis",
        "engine",
        "observability",
    ]


@pytest.mark.anyio
async def test_lifespan_safely_reports_relay_and_cleanup_failures(
    settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.composition.api import build_api_runtime

    configured = settings.model_copy(
        update={
            "app_env": "development",
            "redis_url": "redis://captured-settings.example:6380/7",
            "realtime_enabled": True,
        }
    )
    pool = _Pool(close_error=RuntimeError("ARQ_PROVIDER_SECRET customer text"))
    observed = SimpleNamespace(redis_close_calls=0)

    class ClosingRedis:
        async def aclose(self) -> None:
            observed.redis_close_calls += 1
            raise RuntimeError("REDIS_PROVIDER_SECRET customer text")

    class FailingRealtimeService:
        def __init__(self, auth_provider, *, event_bus, websocket_manager, observability) -> None:
            self.event_bus = event_bus

        async def fanout_forever(self) -> None:
            raise RuntimeError("RELAY_PROVIDER_SECRET customer text")

    async def create_pool(redis_url: str):
        del redis_url
        return pool

    class Engine:
        async def dispose(self) -> None:
            return None

    class Resource:
        async def aclose(self) -> None:
            return None

    with caplog.at_level(logging.WARNING):
        runtime = await build_api_runtime(
            configured,
            engine_factory=lambda _url: Engine(),
            redis_factory=lambda _url: ClosingRedis(),
            observability_factory=lambda **_kwargs: Resource(),
            auth_factory=lambda **_kwargs: Resource(),
            readiness_factory=lambda **_kwargs: object(),
            storage_factory=lambda **_kwargs: Resource(),
            arq_pool_factory=create_pool,
            realtime_service_factory=FailingRealtimeService,
            webhook_receiver_factory=lambda **_kwargs: object(),
            recording_service_factory=lambda **_kwargs: object(),
        )
        await asyncio.sleep(0)
        await runtime.aclose()

    assert observed.redis_close_calls == 1
    assert pool.closed == 1
    assert "event=realtime_fanout_failed" in caplog.text
    assert "event=redis_client_close_failed" in caplog.text
    assert "event=arq_pool_close_failed" in caplog.text
    for secret in (
        "RELAY_PROVIDER_SECRET",
        "REDIS_PROVIDER_SECRET",
        "ARQ_PROVIDER_SECRET",
        "customer text",
    ):
        assert secret not in caplog.text
