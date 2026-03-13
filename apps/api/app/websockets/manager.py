from collections import defaultdict

from fastapi import WebSocket


class WebSocketManager:
    def __init__(self) -> None:
        self.connections: dict[str, set[WebSocket]] = defaultdict(set)

    def channel_name(self, user_id: str) -> str:
        return f"realtime:user:{user_id}"

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
            await websocket.send_json(payload)


manager = WebSocketManager()
