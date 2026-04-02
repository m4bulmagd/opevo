import logging
from collections import defaultdict

from fastapi import WebSocket

from app.core.redis import REALTIME_CHANNEL_PREFIX

logger = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self) -> None:
        self.connections: dict[str, set[WebSocket]] = defaultdict(set)

    def channel_name(self, user_id: str) -> str:
        return f"{REALTIME_CHANNEL_PREFIX}{user_id}"

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        self.connections[user_id].add(websocket)

    async def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        connections = self.connections.get(user_id)
        if not connections:
            return
        connections.discard(websocket)
        if not connections:
            self.connections.pop(user_id, None)

    async def broadcast(self, user_id: str, payload: dict) -> None:
        for websocket in list(self.connections.get(user_id, set())):
            try:
                await websocket.send_json(payload)
            except Exception:
                logger.warning("WebSocket send failed for user %s, removing connection", user_id)
                await self.disconnect(user_id, websocket)


manager = WebSocketManager()
