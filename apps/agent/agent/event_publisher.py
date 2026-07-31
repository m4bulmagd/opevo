from agent.config import get_settings
from presvo_contracts import RealtimeEvent, dump_contract_json, realtime_channel


class RedisEventBus:
    def __init__(self, redis_client=None) -> None:
        self.redis_client = redis_client

    async def publish(self, event: RealtimeEvent) -> None:
        if self.redis_client is None:
            from redis.asyncio import Redis

            self.redis_client = Redis.from_url(
                get_settings().redis_url,
                decode_responses=True,
            )
        await self.redis_client.publish(
            realtime_channel(event.user_id),
            dump_contract_json(event),
        )


class EventPublisher:
    def __init__(self, event_bus: RedisEventBus | None = None) -> None:
        self.event_bus = event_bus or RedisEventBus()

    async def publish(self, event: RealtimeEvent) -> None:
        await self.event_bus.publish(event)
