from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import jwt

from app.services.realtime_service import RealtimeService
from app.websockets.manager import manager


router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    realtime_service: RealtimeService = websocket.app.state.realtime_service
    user_id = None
    try:
        user_id = await realtime_service.authenticate(websocket)
        if user_id is None:
            return

        while True:
            message = await websocket.receive_json()
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except (WebSocketDisconnect, jwt.PyJWTError):
        if user_id is not None:
            await manager.disconnect(user_id, websocket)
