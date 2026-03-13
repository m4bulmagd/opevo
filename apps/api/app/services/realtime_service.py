from fastapi import WebSocket

from app.core.auth import AuthProvider, ClerkAuthProvider
from app.websockets.manager import manager


class RealtimeService:
    def __init__(self, auth_provider: AuthProvider | None = None) -> None:
        self.auth_provider = auth_provider or ClerkAuthProvider()

    async def authenticate(self, websocket: WebSocket) -> str | None:
        message = await websocket.receive_json()
        if message.get("type") != "auth" or not message.get("token"):
            await websocket.send_json({"type": "error", "detail": "auth_required"})
            await websocket.close(code=1008)
            return None

        identity = self.auth_provider.verify_token(message["token"])
        await manager.connect(identity.user_id, websocket)
        return identity.user_id

    async def publish_call_started(self, user_id: str, *, room_name: str, call_id: str) -> None:
        await manager.broadcast(
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
        await manager.broadcast(
            user_id,
            {
                "type": "call_ended",
                "call_id": call_id,
                "minutes_charged": minutes_charged,
                "summary_text": summary_text,
            },
        )
