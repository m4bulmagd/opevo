import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import report_safe_exception
from app.core.config import get_settings
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.business_profile_repository import BusinessProfileRepository
from app.repositories.call_repository import CallRepository
from app.repositories.customer_activation_repository import (
    CustomerActivationRepository,
)
from app.repositories.phone_number_provisioning_repository import (
    PhoneNumberProvisioningRepository,
)
from app.repositories.phone_number_repository import (
    PhoneNumberRepository,
    normalize_phone_number,
)
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.usage_repository import UsageRepository
from app.repositories.user_repository import UserRepository
from app.providers.telephony.telnyx import normalize_french_number
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.customer_readiness_service import (
    evaluate_customer_readiness,
)
from app.services.inbound_verification_service import InboundVerificationService
from app.services.livekit_recording_service import LiveKitRecordingService
from app.services.outbox_service import OutboxService
from app.services.realtime_service import RealtimeService


logger = logging.getLogger(__name__)

ACTIVE_CALL_CONSTRAINT = "uq_calls_user_active"
ROOM_IDENTITY_CONSTRAINT = "uq_calls_livekit_room_id"


@dataclass(frozen=True)
class DispatchJoinResult:
    status: str
    call_id: str | None = None


def calculate_allowed_duration(*, minutes_remaining: int, maximum: int) -> int:
    return min(minutes_remaining * 60, maximum)


def expected_agent_identity(call_id) -> str:
    return f"agent-call-{call_id}"


def normalize_participant_kind(kind) -> str:
    numeric_value = getattr(kind, "value", kind)
    if numeric_value in (3, "3"):
        return "SIP"
    if numeric_value in (4, "4"):
        return "AGENT"
    name = getattr(kind, "name", numeric_value)
    normalized = str(name or "").upper()
    for prefix in ("PARTICIPANT_KIND_", "KIND_"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
    return normalized


def _constraint_name(error: IntegrityError) -> str | None:
    original = error.orig
    for candidate in (original, getattr(original, "__cause__", None)):
        if candidate is None:
            continue
        diagnostic = getattr(candidate, "diag", None)
        name = getattr(diagnostic, "constraint_name", None)
        if name:
            return str(name)
        name = getattr(candidate, "constraint_name", None)
        if name:
            return str(name)
    message = str(original)
    if message == "UNIQUE constraint failed: calls.user_id":
        return ACTIVE_CALL_CONSTRAINT
    if message == "UNIQUE constraint failed: calls.livekit_room_id":
        return ROOM_IDENTITY_CONSTRAINT
    return None


class LiveKitDispatchService:
    def __init__(
        self,
        session: AsyncSession,
        dispatch_client=None,
        *,
        phone_number_repository: PhoneNumberRepository | None = None,
        agent_config_repository: AgentConfigRepository | None = None,
        call_repository: CallRepository | None = None,
        user_repository: UserRepository | None = None,
        usage_repository: UsageRepository | None = None,
        subscription_repository: SubscriptionRepository | None = None,
        business_profile_repository: BusinessProfileRepository | None = None,
        activation_repository: CustomerActivationRepository | None = None,
        provisioning_repository: PhoneNumberProvisioningRepository | None = None,
        inbound_verification_service: InboundVerificationService | None = None,
        outbox_service: OutboxService | None = None,
        realtime_service: RealtimeService | None,
        recording_service: LiveKitRecordingService,
        call_lifecycle_service: CallLifecycleService | None = None,
        arq_pool=None,
        now_provider=None,
    ) -> None:
        self.session = session
        self.now_provider = now_provider or (lambda: datetime.now(UTC))
        # Kept as a compatibility argument for callers while intentionally unused:
        # webhook handling records provider intent and never dispatches directly.
        self.dispatch_client = dispatch_client
        self.phone_number_repository = phone_number_repository or PhoneNumberRepository(session)
        self.agent_config_repository = agent_config_repository or AgentConfigRepository(session)
        self.call_repository = call_repository or CallRepository(session)
        self.user_repository = user_repository or UserRepository(session)
        self.usage_repository = usage_repository or UsageRepository(session)
        self.subscription_repository = subscription_repository or SubscriptionRepository(session)
        self.business_profile_repository = (
            business_profile_repository or BusinessProfileRepository(session)
        )
        self.activation_repository = (
            activation_repository or CustomerActivationRepository(session)
        )
        self.provisioning_repository = (
            provisioning_repository or PhoneNumberProvisioningRepository(session)
        )
        self.inbound_verification_service = (
            inbound_verification_service
            or InboundVerificationService(
                session,
                now_provider=self.now_provider,
            )
        )
        self.outbox_service = outbox_service or OutboxService(session)
        self.realtime_service = realtime_service
        self.recording_service = recording_service
        self.call_lifecycle_service = call_lifecycle_service or CallLifecycleService(
            session,
            call_repository=self.call_repository,
        )
        self.arq_pool = arq_pool

    async def handle_participant_joined(self, event: dict) -> DispatchJoinResult:
        participant = event.get("participant", {})
        kind = normalize_participant_kind(participant.get("kind"))
        if kind == "SIP":
            return await self._handle_sip_participant_joined(event)
        if kind == "AGENT":
            return await self._handle_agent_participant_joined(event)
        await self.session.commit()
        return DispatchJoinResult("ignored")

    async def handle_participant_left(self, event: dict) -> DispatchJoinResult:
        participant = event.get("participant", {})
        if normalize_participant_kind(participant.get("kind")) != "SIP":
            await self.session.commit()
            return DispatchJoinResult("ignored")

        room_name = event.get("room", {}).get("name")
        if not room_name:
            await self.session.commit()
            return DispatchJoinResult("ignored")

        call = await self.call_repository.get_active_by_room_for_update(
            room_name=room_name
        )
        if call is None:
            await self.session.commit()
            return DispatchJoinResult("ignored")

        ended = await self.call_lifecycle_service.end_from_sip(
            call_id=call.id,
            ended_at=self.now_provider(),
        )
        await self.session.commit()
        if ended.status == "ending" and self.arq_pool is not None:
            try:
                await self.arq_pool.enqueue_job(
                    "call_finalization_job",
                    {"call_id": str(call.id)},
                    _job_id=f"call-finalization:{call.id}",
                )
            except Exception:
                logger.warning(
                    "call finalization wakeup failed operation=sip_leave "
                    "call_id=%s error_type=enqueue_failed",
                    call.id,
                )
        return DispatchJoinResult(ended.status, str(call.id))

    async def _handle_sip_participant_joined(
        self,
        event: dict,
    ) -> DispatchJoinResult:
        room_name = event.get("room", {}).get("name")
        participant = event.get("participant", {})
        attributes = participant.get("attributes", {}) or {}
        raw_called_number = attributes.get("sip.trunkPhoneNumber")
        if not room_name or not raw_called_number:
            await self.session.commit()
            return DispatchJoinResult("ignored")

        existing = await self.call_repository.get_by_room(room_name=room_name)
        if existing is not None:
            await self.session.commit()
            return DispatchJoinResult("idempotent", str(existing.id))

        try:
            normalized_called_number = normalize_french_number(raw_called_number)
        except ValueError:
            await self.session.commit()
            return DispatchJoinResult("denied")
        initial_phone = await self.phone_number_repository.get_by_e164(
            normalized_called_number
        )
        if initial_phone is None:
            await self.session.commit()
            return DispatchJoinResult("denied")

        # This user lock is the ordering boundary. Every eligibility-bearing read
        # happens only after it, preventing stale routing decisions from racing
        # subscription/config/number changes.
        user = await self.user_repository.get_by_id_for_update(initial_phone.user_id)
        if user is None or user.status != "active":
            await self.session.commit()
            return DispatchJoinResult("denied")

        existing = await self.call_repository.get_by_room(room_name=room_name)
        if existing is not None:
            await self.session.commit()
            return DispatchJoinResult("idempotent", str(existing.id))

        verification_claim = await self.inbound_verification_service.claim_if_open(
            called_number=normalized_called_number,
            room_name=room_name,
            diversion_number=attributes.get("sip.diversion"),
        )
        if verification_claim is not None:
            await self._best_effort_outbox_wakeup()
            return DispatchJoinResult("verification_claimed")

        settings = get_settings()
        activation = None
        business_profile = None
        if settings.activation_flow_enabled:
            activation = await self.activation_repository.get_by_user_id_for_update(
                user.id
            )
            business_profile = (
                await self.business_profile_repository.get_by_user_id_for_update(
                    user.id
                )
            )
        phone_number = await self.phone_number_repository.get_by_e164_for_update(
            normalized_called_number
        )
        provisioning = await self.provisioning_repository.get_by_user_id_for_update(
            user.id
        )
        subscription = await self.subscription_repository.get_by_user_id_for_update(
            user.id
        )
        agent_config = await self.agent_config_repository.get_by_user_id_for_update(
            user.id
        )
        balance = await self.usage_repository.get_current_balance(user_id=user.id)
        if phone_number is None or subscription is None or agent_config is None:
            await self.session.commit()
            return DispatchJoinResult("denied")
        called_number_matches = bool(
            phone_number.id == initial_phone.id
            and phone_number.user_id == user.id
            and phone_number.e164 == normalized_called_number
        )
        readiness = evaluate_customer_readiness(
            user=user,
            subscription=subscription,
            balance=balance,
            phone_number=phone_number,
            provisioning=provisioning,
            agent_config=agent_config,
            business_profile=business_profile,
            activation=activation,
            activation_required=settings.activation_flow_enabled,
            now=self.now_provider(),
        )
        eligible = readiness.can_dispatch(
            called_number_matches=called_number_matches
        )
        if not eligible:
            await self.session.commit()
            return DispatchJoinResult("denied")

        raw_caller_number = attributes.get("sip.phoneNumber")
        caller_number = (
            normalize_phone_number(raw_caller_number) if raw_caller_number else None
        )
        try:
            async with self.session.begin_nested():
                call = await self.call_repository.create_pending(
                    user_id=user.id,
                    phone_number_id=phone_number.id,
                    agent_config_id=agent_config.id,
                    livekit_room_id=room_name,
                    caller_number=caller_number,
                )
                await self.outbox_service.add(
                    topic="livekit.dispatch",
                    aggregate_type="call",
                    aggregate_id=call.id,
                    idempotency_key=f"livekit.dispatch:{call.id}",
                    payload={"call_id": str(call.id)},
                )
        except IntegrityError as error:
            constraint = _constraint_name(error)
            if constraint == ROOM_IDENTITY_CONSTRAINT:
                existing = await self.call_repository.get_by_room(room_name=room_name)
                await self.session.commit()
                return DispatchJoinResult(
                    "idempotent",
                    str(existing.id) if existing is not None else None,
                )
            if constraint == ACTIVE_CALL_CONSTRAINT:
                await self.session.commit()
                return DispatchJoinResult("busy")
            raise

        await self.session.commit()
        await self._best_effort_outbox_wakeup()
        if self.realtime_service is not None:
            try:
                await self.realtime_service.publish_call_started(
                    str(user.id),
                    room_name=room_name,
                    call_id=str(call.id),
                )
            except Exception as error:
                report_safe_exception(
                    logger,
                    event="livekit_realtime_publish_failed",
                    operation="publish_call_started",
                    error=error,
                    call_id=call.id,
                    user_id=user.id,
                    status="failed",
                    level=logging.WARNING,
                )
        return DispatchJoinResult("accepted", str(call.id))

    async def _handle_agent_participant_joined(
        self,
        event: dict,
    ) -> DispatchJoinResult:
        room_name = event.get("room", {}).get("name")
        identity = event.get("participant", {}).get("identity")
        if not room_name or not identity:
            await self.session.commit()
            return DispatchJoinResult("ignored")

        call = await self.call_repository.get_pending_by_room_without_recording(
            room_name=room_name
        )
        if call is None or identity != expected_agent_identity(call.id):
            await self.session.commit()
            return DispatchJoinResult("ignored")

        connected_call = await self.call_repository.connect_if_pending(call_id=call.id)
        if connected_call is None:
            await self.session.commit()
            return DispatchJoinResult("ignored")

        call_id = connected_call.id
        user_id = connected_call.user_id
        await self.session.commit()
        try:
            recording = await self.recording_service.start_room_recording(
                room_name=room_name,
                user_id=user_id,
                call_id=call_id,
            )
        except Exception as exc:
            report_safe_exception(
                logger,
                event="livekit_recording_start_failed",
                operation="start_room_recording",
                error=exc,
                call_id=call_id,
                user_id=user_id,
                status="failed",
            )
        else:
            fresh_call = (
                await self.call_repository.get_by_id_without_recording_for_update(
                    call_id=call_id
                )
            )
            if fresh_call is not None:
                await self.call_repository.set_recording_metadata(
                    fresh_call,
                    recording_object_key=recording.object_key,
                    recording_egress_id=recording.egress_id,
                    recording_url=recording.url,
                )
            await self.session.commit()
            if fresh_call is None:
                try:
                    await self.recording_service.ensure_stopped(recording.egress_id)
                except Exception as exc:
                    cleanup_call = await self.call_repository.get_by_id_for_update(
                        call_id
                    )
                    if cleanup_call is not None:
                        await self.call_repository.set_recording_metadata(
                            cleanup_call,
                            recording_object_key=recording.object_key,
                            recording_egress_id=recording.egress_id,
                            recording_url=recording.url,
                        )
                        await self.outbox_service.add(
                            topic="recording.stop",
                            aggregate_type="call-recording",
                            aggregate_id=call_id,
                            idempotency_key=(
                                f"recording.stop:{call_id}:egress:"
                                f"{recording.egress_id}"
                            ),
                            payload={"call_id": str(call_id)},
                        )
                    await self.session.commit()
                    report_safe_exception(
                        logger,
                        event="livekit_orphan_recording_stop_failed",
                        operation="ensure_recording_stopped",
                        error=exc,
                        call_id=call_id,
                        user_id=user_id,
                        status="failed",
                    )

        return DispatchJoinResult("connected", str(call_id))

    async def _best_effort_outbox_wakeup(self) -> None:
        if self.arq_pool is None:
            return
        try:
            await self.arq_pool.enqueue_job("outbox_delivery_job", {})
        except Exception as error:
            logger.warning(
                "outbox wakeup enqueue failed operation=livekit_dispatch "
                "error_type=%s",
                type(error).__name__,
            )
