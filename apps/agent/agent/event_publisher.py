import json

from agent.config import get_settings

# SHARED CONTRACT — apps/api/app/core/redis.py and
# libs/shared/constants.py use the same prefix. Update all if changing.
REALTIME_CHANNEL_PREFIX = "realtime:user:"


class RedisEventBus:
    def __init__(self, redis_client=None) -> None:
        self.redis_client = redis_client

    @staticmethod
    def channel_name(user_id: str) -> str:
        return f"{REALTIME_CHANNEL_PREFIX}{user_id}"

    async def publish_json(self, user_id: str, payload: dict) -> None:
        if self.redis_client is None:
            from redis.asyncio import Redis

            self.redis_client = Redis.from_url(
                get_settings().redis_url,
                decode_responses=True,
            )
        await self.redis_client.publish(self.channel_name(user_id), json.dumps(payload))


class EventPublisher:
    def __init__(self, event_bus: RedisEventBus | None = None) -> None:
        self.event_bus = event_bus or RedisEventBus()

    async def publish(self, payload: dict) -> None:
        user_id = payload.get("user_id")
        if not user_id:
            raise ValueError("user_id is required for realtime publishing")
        await self.event_bus.publish_json(user_id, payload)
