import json
from collections.abc import AsyncIterator
from functools import lru_cache

from arq import create_pool
from arq.connections import ArqRedis
from arq.connections import RedisSettings
from redis.asyncio import Redis

from app.core.config import get_settings

# SHARED CONTRACT — apps/agent/agent/event_publisher.py and
# libs/shared/constants.py use the same prefix. Update all if changing.
REALTIME_CHANNEL_PREFIX = "realtime:user:"


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

    @staticmethod
    def channel_name(user_id: str) -> str:
        return f"{REALTIME_CHANNEL_PREFIX}{user_id}"

    async def publish_json(self, user_id: str, payload: dict) -> None:
        await self.redis_client.publish(self.channel_name(user_id), json.dumps(payload))

    async def subscribe(self) -> AsyncIterator[tuple[str, dict]]:
        pubsub = self.redis_client.pubsub()
        await pubsub.psubscribe(f"{REALTIME_CHANNEL_PREFIX}*")
        try:
            async for message in pubsub.listen():
                if message.get("type") != "pmessage" or not message.get("data"):
                    continue

                channel = message["channel"]
                if isinstance(channel, bytes):
                    channel = channel.decode("utf-8")
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")

                yield channel.rsplit(":", 1)[-1], json.loads(data)
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
