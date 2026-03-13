import json
from collections.abc import AsyncIterator

from redis.asyncio import Redis

from app.core.config import get_settings


def get_redis_client() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_url, decode_responses=True)


class RedisEventBus:
    def __init__(self, redis_client: Redis | None = None) -> None:
        self.redis_client = redis_client or get_redis_client()

    @staticmethod
    def channel_name(user_id: str) -> str:
        return f"realtime:user:{user_id}"

    async def publish_json(self, user_id: str, payload: dict) -> None:
        await self.redis_client.publish(self.channel_name(user_id), json.dumps(payload))

    async def subscribe(self) -> AsyncIterator[tuple[str, dict]]:
        pubsub = self.redis_client.pubsub()
        await pubsub.psubscribe("realtime:user:*")
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
