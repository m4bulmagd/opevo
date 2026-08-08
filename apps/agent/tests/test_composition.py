import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from livekit.agents import JobExecutorType, JobProcess

from agent.composition import (
    AgentRuntimeConfigurationError,
    build_agent_api_client,
    build_agent_process_runtime,
    build_event_publisher,
    publish_agent_process_runtime,
    require_agent_process_runtime,
)
from agent.config import AgentSettings
from agent.event_publisher import EventPublisher, RedisEventBus
from agent.main import prewarm_assets


def _settings(**overrides: object) -> AgentSettings:
    configured = AgentSettings(
        livekit_silero_vad_enabled=False,
        livekit_turn_detector_enabled=False,
        speechmatics_turn_detection_mode="adaptive",
    )
    return configured.model_copy(update=overrides)


class _ApiClient:
    def __init__(
        self,
        calls: list[str] | None = None,
        *,
        close_error: BaseException | None = None,
    ) -> None:
        self.calls = calls
        self.close_calls = 0
        self.close_error = close_error
        self.http_client = None

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.calls is not None:
            self.calls.append("api_client.close")
        if self.close_error is not None:
            raise self.close_error


class _Publisher:
    def __init__(
        self,
        calls: list[str] | None = None,
        *,
        close_error: BaseException | None = None,
    ) -> None:
        self.calls = calls
        self.close_calls = 0
        self.close_error = close_error

    async def publish(self, _event: object) -> None:
        return None

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.calls is not None:
            self.calls.append("publisher.close")
        if self.close_error is not None:
            raise self.close_error


class _Redis:
    def __init__(self) -> None:
        self.close_calls = 0
        self.publish_calls: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.publish_calls.append((channel, payload))

    async def aclose(self) -> None:
        self.close_calls += 1


def _job_process() -> JobProcess:
    return JobProcess(
        executor_type=JobExecutorType.PROCESS,
        user_arguments=None,
        http_proxy=None,
    )


@pytest.mark.parametrize("userdata", [None, {}, object()])
def test_require_agent_process_runtime_rejects_missing_or_wrong_process_data(
    userdata: object,
) -> None:
    proc = SimpleNamespace(userdata=userdata)

    with pytest.raises(
        AgentRuntimeConfigurationError,
        match="agent process runtime is not initialized",
    ):
        require_agent_process_runtime(proc)


def test_require_agent_process_runtime_returns_typed_process_data() -> None:
    runtime = build_agent_process_runtime(
        _settings(),
        api_client_factory=lambda _settings: _ApiClient(),
        event_publisher_factory=lambda _settings: _Publisher(),
        silero_vad=object(),
    )
    proc = _job_process()
    publish_agent_process_runtime(proc, runtime)

    assert require_agent_process_runtime(proc) is runtime


@pytest.mark.parametrize("userdata", [None, object()])
def test_publish_agent_process_runtime_rejects_nonmutable_userdata(
    userdata: object,
) -> None:
    runtime = build_agent_process_runtime(
        _settings(),
        api_client_factory=lambda _settings: _ApiClient(),
        event_publisher_factory=lambda _settings: _Publisher(),
    )

    with pytest.raises(
        AgentRuntimeConfigurationError,
        match="agent process userdata is not a mutable mapping",
    ):
        publish_agent_process_runtime(SimpleNamespace(userdata=userdata), runtime)


def test_agent_transport_factories_are_synchronous_and_construction_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        api_base_url="http://api.runtime.test/root/",
        api_timeout_seconds=7.5,
        api_max_retries=4,
        redis_url="redis://redis.runtime.test:6380/6",
    )
    redis = _Redis()
    redis_calls: list[tuple[str, bool]] = []
    proc = SimpleNamespace(userdata={})

    def fail_http_construction(**_kwargs: object) -> Any:
        pytest.fail("API factory eagerly acquired an HTTP transport")

    def build_redis(url: str, *, decode_responses: bool) -> _Redis:
        redis_calls.append((url, decode_responses))
        return redis

    monkeypatch.setattr("agent.api_client.httpx.AsyncClient", fail_http_construction)

    prewarm_assets(
        proc,
        settings=settings,
        api_client_factory=build_agent_api_client,
        event_publisher_factory=lambda configured: build_event_publisher(
            configured,
            redis_factory=build_redis,
        ),
    )

    runtime = require_agent_process_runtime(proc)
    api_client = runtime.api_client
    publisher = runtime.event_publisher
    assert api_client.base_url == "http://api.runtime.test/root"
    assert api_client.timeout == 7.5
    assert api_client.max_retries == 4
    assert api_client.http_client is None
    assert publisher.event_bus.redis_client is redis
    assert redis_calls == [("redis://redis.runtime.test:6380/6", True)]
    assert redis.publish_calls == []


@pytest.mark.anyio
async def test_process_runtime_closes_in_reverse_construction_order() -> None:
    calls: list[str] = []
    api_client = _ApiClient(calls)
    publisher = _Publisher(calls)
    runtime = build_agent_process_runtime(
        _settings(),
        api_client_factory=lambda _settings: api_client,
        event_publisher_factory=lambda _settings: publisher,
    )

    await asyncio.gather(runtime.aclose(), runtime.aclose())
    await runtime.aclose()

    assert calls == ["publisher.close", "api_client.close"]
    assert publisher.close_calls == 1
    assert api_client.close_calls == 1


@pytest.mark.anyio
async def test_process_runtime_close_survives_waiter_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingPublisher(_Publisher):
        async def aclose(self) -> None:
            self.close_calls += 1
            started.set()
            await release.wait()

    api_client = _ApiClient()
    publisher = BlockingPublisher()
    runtime = build_agent_process_runtime(
        _settings(),
        api_client_factory=lambda _settings: api_client,
        event_publisher_factory=lambda _settings: publisher,
    )

    waiter = asyncio.create_task(runtime.aclose())
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await runtime.aclose()

    assert publisher.close_calls == 1
    assert api_client.close_calls == 1


@pytest.mark.anyio
async def test_process_runtime_reports_cleanup_failure_and_closes_remaining_transport(
    caplog: pytest.LogCaptureFixture,
) -> None:
    api_client = _ApiClient()
    publisher = _Publisher(
        close_error=RuntimeError("REDIS_CREDENTIAL_SENTINEL")
    )
    runtime = build_agent_process_runtime(
        _settings(),
        api_client_factory=lambda _settings: api_client,
        event_publisher_factory=lambda _settings: publisher,
    )

    with caplog.at_level(logging.ERROR):
        await runtime.aclose()

    assert publisher.close_calls == 1
    assert api_client.close_calls == 1
    assert "event=agent_runtime_resource_close_failed" in caplog.text
    assert "operation=close_event_publisher" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "REDIS_CREDENTIAL_SENTINEL" not in caplog.text


@pytest.mark.anyio
async def test_event_publisher_closes_owned_redis_once() -> None:
    redis = _Redis()
    publisher = build_event_publisher(
        _settings(),
        redis_factory=lambda *_args, **_kwargs: redis,
    )

    await asyncio.gather(publisher.aclose(), publisher.aclose())
    await publisher.aclose()

    assert redis.close_calls == 1


@pytest.mark.anyio
async def test_event_publisher_never_closes_borrowed_redis() -> None:
    redis = _Redis()
    publisher = EventPublisher(RedisEventBus(redis, owns_client=False))

    await publisher.aclose()

    assert redis.close_calls == 0


def test_prewarm_publishes_complete_runtime_with_exact_settings_and_no_vad() -> None:
    settings = _settings()
    original_userdata: dict[object, object] = {}
    proc = SimpleNamespace(userdata=original_userdata)

    api_client = _ApiClient()
    publisher = _Publisher()

    prewarm_assets(
        proc,
        settings=settings,
        api_client_factory=lambda _settings: api_client,
        event_publisher_factory=lambda _settings: publisher,
    )

    runtime = require_agent_process_runtime(proc)
    assert proc.userdata is original_userdata
    assert runtime.settings is settings
    assert runtime.api_client is api_client
    assert runtime.event_publisher is publisher
    assert runtime.silero_vad is None


def test_prewarm_publishes_runtime_without_replacing_livekit_userdata() -> None:
    settings = _settings()
    proc = _job_process()
    original_userdata = proc.userdata
    unrelated_value = object()
    original_userdata["unrelated"] = unrelated_value

    api_client = _ApiClient()
    publisher = _Publisher()

    prewarm_assets(
        proc,
        settings=settings,
        api_client_factory=lambda _settings: api_client,
        event_publisher_factory=lambda _settings: publisher,
    )

    runtime = require_agent_process_runtime(proc)
    assert proc.userdata is original_userdata
    assert proc.userdata["unrelated"] is unrelated_value
    assert runtime.settings is settings
    assert runtime.api_client is api_client
    assert runtime.event_publisher is publisher
    assert runtime.silero_vad is None


def test_prewarm_publishes_loaded_vad_in_complete_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livekit import plugins

    settings = _settings(livekit_silero_vad_enabled=True)
    vad = object()
    original_userdata: dict[object, object] = {}
    proc = SimpleNamespace(userdata=original_userdata)

    def load_vad() -> object:
        assert proc.userdata is original_userdata
        return vad

    fake_silero = SimpleNamespace(VAD=SimpleNamespace(load=load_vad))
    monkeypatch.setattr(plugins, "silero", fake_silero, raising=False)

    api_client = _ApiClient()
    publisher = _Publisher()
    prewarm_assets(
        proc,
        settings=settings,
        api_client_factory=lambda _settings: api_client,
        event_publisher_factory=lambda _settings: publisher,
    )

    runtime = require_agent_process_runtime(proc)
    assert proc.userdata is original_userdata
    assert runtime.settings is settings
    assert runtime.api_client is api_client
    assert runtime.event_publisher is publisher
    assert runtime.silero_vad is vad


def test_prewarm_does_not_publish_a_partial_runtime_when_factory_fails() -> None:
    settings = _settings()
    existing_value = object()
    original_userdata: dict[object, object] = {"existing": existing_value}
    proc = SimpleNamespace(userdata=original_userdata)
    api_client = build_agent_api_client(settings)

    def fail_publisher(_settings: AgentSettings) -> EventPublisher:
        raise RuntimeError("publisher construction failed")

    with pytest.raises(RuntimeError, match="publisher construction failed"):
        prewarm_assets(
            proc,
            settings=settings,
            api_client_factory=lambda _settings: api_client,
            event_publisher_factory=fail_publisher,
        )

    assert proc.userdata == {"existing": existing_value}
    with pytest.raises(
        AgentRuntimeConfigurationError,
        match="agent process runtime is not initialized",
    ):
        require_agent_process_runtime(proc)
    assert api_client.http_client is None
