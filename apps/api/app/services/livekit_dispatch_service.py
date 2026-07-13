import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dispatch_token import create_dispatch_token
from app.core.logging import report_safe_exception
from app.core.redaction import redact_phone
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
        *,
        phone_number_repository: PhoneNumberRepository,
        agent_config_repository: AgentConfigRepository,
        call_repository: CallRepository,
        user_repository: UserRepository,
        usage_repository: UsageRepository,
        realtime_service: RealtimeService,
        recording_service: LiveKitRecordingService,
    ) -> None:
        self.session = session
        self.dispatch_client = dispatch_client
        self.phone_number_repository = phone_number_repository
        self.agent_config_repository = agent_config_repository
        self.call_repository = call_repository
        self.user_repository = user_repository
        self.usage_repository = usage_repository
        self.realtime_service = realtime_service
        self.recording_service = recording_service

    async def handle_participant_joined(self, event: dict) -> None:
        participant = event.get("participant", {})
        if self._is_sip_participant(participant):
            await self._handle_sip_participant_joined(event)
            return

        await self._handle_agent_participant_joined(event)

    async def handle_participant_left(self, event: dict) -> None:
        participant = event.get("participant", {})
        if not self._is_sip_participant(participant):
            await self.session.commit()
            return

        room_name = event.get("room", {}).get("name")
        if not room_name:
            await self.session.commit()
            return

        call = await self.call_repository.get_active_by_room_with_recording(room_name=room_name)
        if call is None:
            await self.session.commit()
            return

        try:
            await self.recording_service.stop_room_recording(egress_id=call.recording_egress_id)
        except Exception as exc:
            report_safe_exception(
                logger,
                event="livekit_recording_stop_failed",
                operation="stop_room_recording",
                error=exc,
                call_id=call.id,
                provider_request_id=call.recording_egress_id,
                status="failed",
            )

        await self.session.commit()

    def _is_sip_participant(self, participant: dict) -> bool:
        if participant.get("kind") == "SIP":
            return True
        attributes = participant.get("attributes", {})
        return any(key.startswith("sip.") for key in attributes)

    async def _handle_sip_participant_joined(self, event: dict) -> None:
        participant = event.get("participant", {})
        attributes = participant.get("attributes", {})

        # LiveKit SIP participant docs map inbound caller ID to sip.phoneNumber and
        # the dialed trunk number to sip.trunkPhoneNumber.
        raw_called_number = attributes.get("sip.trunkPhoneNumber") or attributes.get("sip.phoneNumber")
        raw_caller_number = attributes.get("sip.phoneNumber")
        if not raw_called_number:
            logger.info(
                "livekit dispatch skipped event=missing_called_number participant_kind=%s",
                participant.get("kind"),
            )
            await self.session.commit()
            return

        phone_number = await self.phone_number_repository.get_by_any_format(raw_called_number)
        if phone_number is None:
            logger.info(
                "livekit dispatch skipped event=phone_number_not_found called=%s caller=%s",
                redact_phone(normalize_phone_number(raw_called_number)),
                (
                    redact_phone(normalize_phone_number(raw_caller_number))
                    if raw_caller_number
                    else None
                ),
            )
            await self.session.commit()
            return
        called_number = phone_number.e164
        caller_number = normalize_phone_number(raw_caller_number) if raw_caller_number else None

        agent_config = await self.agent_config_repository.get_by_user_id(phone_number.user_id)
        if agent_config is None:
            logger.info(
                "livekit dispatch skipped: agent config missing called=%s user_id=%s",
                redact_phone(called_number),
                str(phone_number.user_id),
            )
            await self.session.commit()
            return
        user = await self.user_repository.get_by_id(phone_number.user_id)
        if user is None:
            logger.info(
                "livekit dispatch skipped: user missing called=%s user_id=%s",
                redact_phone(called_number),
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
        dispatch_token = create_dispatch_token(
            call_id=str(call.id),
            user_id=str(phone_number.user_id),
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
            dispatch_token=dispatch_token,
        )
        await self.dispatch_client.create_dispatch(
            room_name=room_name,
            metadata=metadata.model_dump_json(),
        )
        logger.info(
            "livekit dispatch created called=%s caller=%s call_id=%s user_id=%s",
            redact_phone(called_number),
            redact_phone(caller_number),
            str(call.id),
            str(phone_number.user_id),
        )
        await self.session.commit()
        await self.realtime_service.publish_call_started(
            str(phone_number.user_id),
            room_name=room_name,
            call_id=str(call.id),
        )

    async def _handle_agent_participant_joined(self, event: dict) -> None:
        room_name = event.get("room", {}).get("name")
        if not room_name:
            await self.session.commit()
            return

        call = await self.call_repository.get_pending_by_room_without_recording(room_name=room_name)
        if call is None:
            await self.session.commit()
            return

        try:
            recording = await self.recording_service.start_room_recording(
                room_name=room_name,
                user_id=call.user_id,
                call_id=call.id,
            )
        except Exception as exc:
            report_safe_exception(
                logger,
                event="livekit_recording_start_failed",
                operation="start_room_recording",
                error=exc,
                call_id=call.id,
                user_id=call.user_id,
                status="failed",
            )
        else:
            await self.call_repository.set_recording_metadata(
                call,
                recording_object_key=recording.object_key,
                recording_egress_id=recording.egress_id,
                recording_url=recording.url,
            )

        await self.session.commit()
