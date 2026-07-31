import logging
from uuid import UUID

from fastapi import WebSocket

from presvo_contracts import (
    CallFinalizedEvent,
    CallStartedEvent,
    ContractError,
    RealtimeEvent,
    create_contract,
    dump_contract,
    parse_realtime_event,
)

from app.core.auth import AuthProvider
from app.core.observability import Observability
from app.core.redis import RedisEventBus
from app.websockets.manager import WebSocketManager


logger = logging.getLogger(__name__)


class RealtimeService:
    def __init__(
        self,
        auth_provider: AuthProvider,
        *,
        event_bus: RedisEventBus,
        websocket_manager: WebSocketManager,
        observability: Observability,
    ) -> None:
        self.auth_provider = auth_provider
        self.event_bus = event_bus
        self.websocket_manager = websocket_manager
        self.observability = observability

    async def authenticate(self, websocket: WebSocket) -> str | None:
        message = await websocket.receive_json()
        if message.get("type") != "auth" or not message.get("token"):
            await websocket.send_json({"type": "error", "detail": "auth_required"})
            await websocket.close(code=1008)
            return None

        identity = self.auth_provider.verify_token(message["token"])
        await self.websocket_manager.connect(identity.clerk_user_id, websocket)
        return identity.clerk_user_id

    async def publish_call_started(
        self, user_id: UUID, *, room_name: str, call_id: UUID
    ) -> None:
        event = create_contract(
            CallStartedEvent,
            type="call_started",
            user_id=user_id,
            call_id=call_id,
            room_name=room_name,
        )
        await self.event_bus.publish(event)

    async def publish_call_finalized(
        self,
        user_id: UUID,
        *,
        call_id: UUID,
        minutes_charged: int,
        summary_text: str | None,
    ) -> None:
        event = create_contract(
            CallFinalizedEvent,
            type="call_finalized",
            user_id=user_id,
            call_id=call_id,
            minutes_charged=minutes_charged,
            summary_text=summary_text,
        )
        await self.event_bus.publish(event)

    def _validated_event(
        self, channel_user_id: str, raw_payload: object
    ) -> RealtimeEvent | None:
        try:
            event = parse_realtime_event(raw_payload)
        except ContractError as error:
            self.observability.record_invalid_contract(
                contract_name=error.contract_name,
                code=error.code,
                transport="redis",
            )
            return None
        if str(event.user_id) != channel_user_id:
            self.observability.record_invalid_contract(
                contract_name=type(event).__name__,
                code="channel_user_mismatch",
                transport="redis",
            )
            logger.error(
                "realtime_event_rejected code=channel_user_mismatch transport=redis"
            )
            return None
        return event

    async def fanout_once(self) -> None:
        async for channel_user_id, raw_payload in self.event_bus.subscribe():
            event = self._validated_event(channel_user_id, raw_payload)
            if event is None:
                continue
            await self.websocket_manager.broadcast(str(event.user_id), dump_contract(event))
            return

    async def fanout_forever(self) -> None:
        async for channel_user_id, raw_payload in self.event_bus.subscribe():
            event = self._validated_event(channel_user_id, raw_payload)
            if event is None:
                continue
            await self.websocket_manager.broadcast(str(event.user_id), dump_contract(event))
