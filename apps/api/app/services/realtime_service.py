from fastapi import WebSocket

from app.core.auth import AuthProvider
from app.core.redis import RedisEventBus
from app.websockets.manager import WebSocketManager


class RealtimeService:
    def __init__(
        self,
        auth_provider: AuthProvider,
        *,
        event_bus: RedisEventBus,
        websocket_manager: WebSocketManager,
    ) -> None:
        self.auth_provider = auth_provider
        self.event_bus = event_bus
        self.websocket_manager = websocket_manager

    async def authenticate(self, websocket: WebSocket) -> str | None:
        message = await websocket.receive_json()
        if message.get("type") != "auth" or not message.get("token"):
            await websocket.send_json({"type": "error", "detail": "auth_required"})
            await websocket.close(code=1008)
            return None

        identity = self.auth_provider.verify_token(message["token"])
        await self.websocket_manager.connect(identity.clerk_user_id, websocket)
        return identity.clerk_user_id

    async def publish_call_started(self, user_id: str, *, room_name: str, call_id: str) -> None:
        await self.event_bus.publish_json(
            user_id,
            {"type": "call_started", "room_name": room_name, "call_id": call_id},
        )

    async def publish_call_ended(
        self,
        user_id: str,
        *,
        call_id: str,
        minutes_charged: int,
        summary_text: str | None,
    ) -> None:
        await self.event_bus.publish_json(
            user_id,
            {
                "type": "call_ended",
                "call_id": call_id,
                "minutes_charged": minutes_charged,
                "summary_text": summary_text,
            },
        )

    async def fanout_once(self) -> None:
        async for user_id, payload in self.event_bus.subscribe():
            await self.websocket_manager.broadcast(user_id, payload)
            return

    async def fanout_forever(self) -> None:
        async for user_id, payload in self.event_bus.subscribe():
            await self.websocket_manager.broadcast(user_id, payload)
