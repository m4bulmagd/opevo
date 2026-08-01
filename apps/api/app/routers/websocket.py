from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.auth_failures import AuthenticationUnavailable, TokenRejected
from app.services.realtime_service import RealtimeService


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
    except TokenRejected:
        await websocket.send_json({"type": "error", "detail": "invalid_token"})
        await websocket.close(code=1008)
    except AuthenticationUnavailable:
        await websocket.send_json({"type": "error", "detail": "auth_unavailable"})
        await websocket.close(code=1013)
    except WebSocketDisconnect:
        pass
    finally:
        if user_id is not None:
            await realtime_service.websocket_manager.disconnect(user_id, websocket)
