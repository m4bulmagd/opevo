import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.call_repository import CallRepository
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.user_repository import UserRepository
from app.schemas.livekit import LiveKitDispatchMetadata
from app.services.realtime_service import RealtimeService


class LiveKitDispatchService:
    def __init__(self, session: AsyncSession, dispatch_client, realtime_service: RealtimeService | None = None) -> None:
        self.session = session
        self.dispatch_client = dispatch_client
        self.phone_number_repository = PhoneNumberRepository(session)
        self.agent_config_repository = AgentConfigRepository(session)
        self.call_repository = CallRepository(session)
        self.user_repository = UserRepository(session)
        self.realtime_service = realtime_service or RealtimeService()

    async def handle_participant_joined(self, event: dict) -> None:
        participant = event.get("participant", {})
        attributes = participant.get("attributes", {})

        # UNVERIFIED: LiveKit SIP attribute naming should be rechecked against current docs/dashboard payloads.
        called_number = attributes.get("sip.trunkPhoneNumber") or attributes.get("sip.phoneNumber")
        caller_number = attributes.get("sip.phoneNumber")
        if not called_number:
            await self.session.commit()
            return

        phone_number = await self.phone_number_repository.get_by_e164(called_number)
        if phone_number is None:
            await self.session.commit()
            return

        agent_config = await self.agent_config_repository.get_by_user_id(phone_number.user_id)
        if agent_config is None:
            await self.session.commit()
            return
        user = await self.user_repository.get_by_id(phone_number.user_id)
        if user is None:
            await self.session.commit()
            return

        room_name = event["room"]["name"]
        call = await self.call_repository.create_pending(
            user_id=phone_number.user_id,
            phone_number_id=phone_number.id,
            livekit_room_id=room_name,
            caller_number=caller_number,
        )

        metadata = LiveKitDispatchMetadata(
            user_id=str(phone_number.user_id),
            agent_config_id=str(agent_config.id),
            call_id=str(call.id),
            called_number=called_number,
            caller_number=caller_number,
            agent_name=agent_config.agent_name,
            owner_name=user.full_name or user.email,
            owner_context=agent_config.owner_context,
            system_prompt=agent_config.system_prompt,
            knowledge_base=agent_config.knowledge_base,
            pipeline_mode=agent_config.pipeline_mode,
        )
        await self.dispatch_client.create_dispatch(
            room_name=room_name,
            metadata=metadata.model_dump_json(),
        )
        await self.session.commit()
        await self.realtime_service.publish_call_started(
            str(phone_number.user_id),
            room_name=room_name,
            call_id=str(call.id),
        )
