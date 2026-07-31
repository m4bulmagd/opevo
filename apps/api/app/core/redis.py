from collections.abc import AsyncIterator
from functools import lru_cache
from uuid import UUID

from arq import create_pool
from arq.connections import ArqRedis
from arq.connections import RedisSettings
from redis.asyncio import Redis

from app.core.config import get_settings
from presvo_contracts import (
    REALTIME_CHANNEL_PREFIX,
    RealtimeEvent,
    dump_contract_json,
    realtime_channel,
)


@lru_cache
def get_redis_client() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_url, decode_responses=True)


async def create_arq_pool(redis_url: str | None = None) -> ArqRedis:
    configured_url = redis_url or get_settings().redis_url
    return await create_pool(RedisSettings.from_dsn(configured_url))


class RedisEventBus:
    def __init__(
        self,
        redis_client: Redis | None = None,
        *,
        redis_url: str | None = None,
    ) -> None:
        if redis_client is not None:
            self.redis_client = redis_client
            self._owns_client = False
        elif redis_url is not None:
            self.redis_client = Redis.from_url(redis_url, decode_responses=True)
            self._owns_client = True
        else:
            self.redis_client = get_redis_client()
            self._owns_client = False

    async def publish(self, event: RealtimeEvent) -> None:
        await self.redis_client.publish(
            realtime_channel(event.user_id),
            dump_contract_json(event),
        )

    async def subscribe(self) -> AsyncIterator[tuple[str, object]]:
        pubsub = self.redis_client.pubsub()
        await pubsub.psubscribe(f"{REALTIME_CHANNEL_PREFIX}*")
        try:
            async for message in pubsub.listen():
                if message.get("type") != "pmessage" or message.get("data") is None:
                    continue

                channel = message["channel"]
                if isinstance(channel, bytes):
                    try:
                        channel = channel.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                if not isinstance(channel, str):
                    continue
                suffix = channel.removeprefix(REALTIME_CHANNEL_PREFIX)
                if suffix == channel:
                    continue
                try:
                    channel_user_id = str(UUID(suffix))
                except (TypeError, ValueError, AttributeError):
                    continue
                if suffix != channel_user_id:
                    continue
                yield channel_user_id, message["data"]
        finally:
            close = getattr(pubsub, "aclose", None)
            if close is not None:
                await close()
            else:
                await pubsub.close()

    async def aclose(self) -> None:
        if not self._owns_client:
            return
        self._owns_client = False
        close = getattr(self.redis_client, "aclose", None)
        if close is not None:
            await close()
        else:
            await self.redis_client.close()
