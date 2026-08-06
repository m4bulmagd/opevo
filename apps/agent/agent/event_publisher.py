from presvo_contracts import RealtimeEvent, dump_contract_json, realtime_channel
from redis.asyncio import Redis


class RedisEventBus:
    def __init__(self, redis_client: Redis, *, owns_client: bool) -> None:
        self.redis_client = redis_client
        self._owns_client = owns_client

    async def publish(self, event: RealtimeEvent) -> None:
        await self.redis_client.publish(
            realtime_channel(event.user_id),
            dump_contract_json(event),
        )

    async def aclose(self) -> None:
        if not self._owns_client:
            return
        self._owns_client = False
        await self.redis_client.aclose()


class EventPublisher:
    def __init__(self, event_bus: RedisEventBus) -> None:
        self.event_bus = event_bus

    async def publish(self, event: RealtimeEvent) -> None:
        await self.event_bus.publish(event)

    async def aclose(self) -> None:
        await self.event_bus.aclose()
