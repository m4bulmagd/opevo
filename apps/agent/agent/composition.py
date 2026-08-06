import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis

from agent.api_client import AgentApiClient
from agent.config import AgentSettings
from agent.event_publisher import EventPublisher, RedisEventBus
from agent.safe_logging import report_safe_exception


logger = logging.getLogger(__name__)


class AgentRuntimeConfigurationError(RuntimeError):
    """The LiveKit process does not contain a complete agent runtime."""


class AgentApiClientFactory(Protocol):
    """Synchronous API wrapper construction without I/O or async acquisition."""

    def __call__(self, settings: AgentSettings) -> AgentApiClient: ...


class EventPublisherFactory(Protocol):
    """Synchronous publisher construction without I/O or async acquisition."""

    def __call__(self, settings: AgentSettings) -> EventPublisher: ...


class AgentRuntimeCleanup:
    def __init__(self, stack: AsyncExitStack) -> None:
        self._stack = stack
        self._lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None

    async def aclose(self) -> None:
        async with self._lock:
            if self._close_task is None:
                self._close_task = asyncio.create_task(self._stack.aclose())
            close_task = self._close_task
        await asyncio.shield(close_task)


@dataclass(slots=True)
class AgentProcessRuntime:
    settings: AgentSettings
    api_client: AgentApiClient
    event_publisher: EventPublisher
    _cleanup: AgentRuntimeCleanup
    silero_vad: object | None = None

    async def aclose(self) -> None:
        await self._cleanup.aclose()


def build_agent_api_client(settings: AgentSettings) -> AgentApiClient:
    return AgentApiClient(
        base_url=settings.api_base_url,
        timeout=settings.api_timeout_seconds,
        max_retries=settings.api_max_retries,
    )


def build_event_publisher(
    settings: AgentSettings,
    *,
    redis_factory: Callable[..., Redis] = Redis.from_url,
) -> EventPublisher:
    redis = redis_factory(settings.redis_url, decode_responses=True)
    return EventPublisher(RedisEventBus(redis, owns_client=True))


async def _close_transport(
    close: Callable[[], Awaitable[None]],
    *,
    operation: str,
) -> None:
    try:
        await close()
    except asyncio.CancelledError:
        raise
    except Exception as error:
        report_safe_exception(
            logger,
            event="agent_runtime_resource_close_failed",
            operation=operation,
            error=error,
        )


def build_agent_process_runtime(
    settings: AgentSettings,
    *,
    api_client_factory: AgentApiClientFactory = build_agent_api_client,
    event_publisher_factory: EventPublisherFactory = build_event_publisher,
    silero_vad: object | None = None,
) -> AgentProcessRuntime:
    stack = AsyncExitStack()
    api_client = api_client_factory(settings)
    stack.push_async_callback(
        _close_transport,
        api_client.aclose,
        operation="close_api_client",
    )
    event_publisher = event_publisher_factory(settings)
    stack.push_async_callback(
        _close_transport,
        event_publisher.aclose,
        operation="close_event_publisher",
    )
    return AgentProcessRuntime(
        settings=settings,
        api_client=api_client,
        event_publisher=event_publisher,
        _cleanup=AgentRuntimeCleanup(stack),
        silero_vad=silero_vad,
    )


def require_agent_process_runtime(proc: object) -> AgentProcessRuntime:
    runtime = getattr(proc, "userdata", None)
    if not isinstance(runtime, AgentProcessRuntime):
        raise AgentRuntimeConfigurationError(
            "agent process runtime is not initialized"
        )
    return runtime
