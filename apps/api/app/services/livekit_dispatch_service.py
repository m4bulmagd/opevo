import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.call_repository import CallRepository
from app.repositories.phone_number_repository import PhoneNumberRepository, normalize_phone_number
from app.repositories.user_repository import UserRepository
from app.repositories.usage_repository import UsageRepository
from app.schemas.livekit import LiveKitDispatchMetadata
from app.services.livekit_recording_service import LiveKitRecordingService
from app.services.realtime_service import RealtimeService


logger = logging.getLogger(__name__)


class LiveKitDispatchService:
    def __init__(
        self,
        session: AsyncSession,
        dispatch_client,
        realtime_service: RealtimeService | None = None,
        recording_service: LiveKitRecordingService | None = None,
    ) -> None:
        self.session = session
        self.dispatch_client = dispatch_client
        self.phone_number_repository = PhoneNumberRepository(session)
        self.agent_config_repository = AgentConfigRepository(session)
        self.call_repository = CallRepository(session)
        self.user_repository = UserRepository(session)
        self.usage_repository = UsageRepository(session)
        self.realtime_service = realtime_service or RealtimeService()
        self.recording_service = recording_service or LiveKitRecordingService()

    async def handle_participant_joined(self, event: dict) -> None:
        participant = event.get("participant", {})
        attributes = participant.get("attributes", {})

        # LiveKit SIP participant docs map inbound caller ID to sip.phoneNumber and
        # the dialed trunk number to sip.trunkPhoneNumber.
        raw_called_number = attributes.get("sip.trunkPhoneNumber") or attributes.get("sip.phoneNumber")
        raw_caller_number = attributes.get("sip.phoneNumber")
        if not raw_called_number:
            logger.info(
                "livekit dispatch skipped: missing called number room=%s identity=%s kind=%s attributes=%s",
                event.get("room", {}).get("name"),
                participant.get("identity"),
                participant.get("kind"),
                attributes,
            )
            await self.session.commit()
            return

        phone_number = await self.phone_number_repository.get_by_any_format(raw_called_number)
        if phone_number is None:
            logger.info(
                "livekit dispatch skipped: phone number not found called=%s normalized=%s caller=%s",
                raw_called_number,
                normalize_phone_number(raw_called_number),
                raw_caller_number,
            )
            await self.session.commit()
            return
        called_number = phone_number.e164
        caller_number = normalize_phone_number(raw_caller_number) if raw_caller_number else None

        agent_config = await self.agent_config_repository.get_by_user_id(phone_number.user_id)
        if agent_config is None:
            logger.info(
                "livekit dispatch skipped: agent config missing called=%s user_id=%s",
                called_number,
                str(phone_number.user_id),
            )
            await self.session.commit()
            return
        user = await self.user_repository.get_by_id(phone_number.user_id)
        if user is None:
            logger.info(
                "livekit dispatch skipped: user missing called=%s user_id=%s",
                called_number,
                str(phone_number.user_id),
            )
            await self.session.commit()
            return

        room_name = event["room"]["name"]
        minutes_remaining = await self.usage_repository.get_current_balance(user_id=phone_number.user_id)
        call = await self.call_repository.create_pending(
            user_id=phone_number.user_id,
            phone_number_id=phone_number.id,
            livekit_room_id=room_name,
            caller_number=caller_number,
        )
        try:
            recording = await self.recording_service.start_room_recording(
                room_name=room_name,
                user_id=phone_number.user_id,
                call_id=call.id,
            )
        except Exception:
            logger.exception(
                "livekit recording start failed room=%s call_id=%s user_id=%s",
                room_name,
                str(call.id),
                str(phone_number.user_id),
            )
        else:
            await self.call_repository.set_recording_metadata(
                call,
                recording_object_key=recording.object_key,
                recording_egress_id=recording.egress_id,
                recording_url=recording.url,
            )

        metadata = LiveKitDispatchMetadata(
            user_id=str(phone_number.user_id),
            agent_config_id=str(agent_config.id),
            call_id=str(call.id),
            minutes_remaining=minutes_remaining,
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
        logger.info(
            "livekit dispatch created room=%s called=%s caller=%s call_id=%s user_id=%s",
            room_name,
            called_number,
            caller_number,
            str(call.id),
            str(phone_number.user_id),
        )
        await self.session.commit()
        await self.realtime_service.publish_call_started(
            str(phone_number.user_id),
            room_name=room_name,
            call_id=str(call.id),
        )
