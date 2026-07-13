from dataclasses import dataclass
import json
from typing import Any
from uuid import UUID

from app.core.config import get_settings
from app.core.dispatch_token import create_dispatch_token
from app.core.database import get_session_factory
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.providers.livekit_dispatch.base import LiveKitDispatch
from app.providers.livekit_dispatch.livekit import LiveKitDispatchAPIProvider
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.call_repository import CallRepository
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.phone_number_provisioning_repository import (
    PhoneNumberProvisioningRepository,
)
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.usage_repository import UsageRepository
from app.schemas.livekit import LiveKitDispatchMetadata
from app.services.dispatch_eligibility_policy import DispatchEligibilityPolicy
from app.services.livekit_dispatch_service import (
    _agent_setup_complete,
    calculate_allowed_duration,
    expected_agent_identity,
)
from app.services.livekit_dispatch_lock import livekit_dispatch_lock
from app.services.onboarding_service import OnboardingService
from app.services.livekit_recording_service import LiveKitRecordingService
from app.services.summary_service import SummaryService
from app.services.subscription_access_policy import SubscriptionAccessPolicy
from app.workers.jobs.outbox_delivery import OutboxDeliveryError
from app.workers.jobs.phone_provisioning import phone_provisioning_job
from app.providers.summaries.gemini import GeminiSummaryProvider


@dataclass(frozen=True)
class _RoutingSnapshot:
    phone_number_id: UUID
    provider_number_id: str
    should_enable: bool
    is_active: bool
    provider_connection_name: str | None


@dataclass(frozen=True)
class _DispatchSnapshot:
    call_id: UUID
    user_id: UUID
    agent_config_id: UUID
    room_name: str
    worker_name: str
    metadata: str
    persisted_dispatch_id: str | None


async def deliver_phone_provision(
    ctx: dict[str, Any],
    event: OutboxEvent,
) -> None:
    await phone_provisioning_job(
        ctx,
        dict(event.payload),
        operation_key=event.idempotency_key,
    )
    user_id = UUID(event.payload["user_id"])
    session_factory = ctx.get("session_factory") or get_session_factory()
    async with session_factory() as session:
        phone_number = await PhoneNumberRepository(session).get_by_user_id(user_id)
        provisioning = await PhoneNumberProvisioningRepository(
            session
        ).get_by_user_id(user_id)
        await session.commit()
    if phone_number is None:
        retryable = bool(
            provisioning is not None
            and (
                provisioning.can_retry
                or provisioning.last_error_reason == "existing_order_pending"
            )
        )
        raise OutboxDeliveryError(
            "provider_retryable" if retryable else "provider_terminal",
            retryable=retryable,
        )
    await deliver_phone_routing(ctx, event)


async def deliver_phone_routing(
    ctx: dict[str, Any],
    event: OutboxEvent,
) -> None:
    user_id = UUID(event.payload["user_id"])
    session_factory = ctx.get("session_factory") or get_session_factory()
    async with session_factory() as session:
        snapshot = await _routing_snapshot(session, user_id)
        await session.commit()

    if snapshot is None:
        return
    desired_connection_name = "app-active" if snapshot.should_enable else "app-disabled"

    provider = ctx.get("telephony_provider")
    if provider is None:
        from app.providers.telephony.telnyx import TelephonyTelnyx

        provider = TelephonyTelnyx()
    if snapshot.should_enable:
        provider_connection_name = await provider.enable_number(
            provider_number_id=snapshot.provider_number_id
        )
    else:
        provider_connection_name = await provider.disable_number(
            provider_number_id=snapshot.provider_number_id
        )
    if provider_connection_name != desired_connection_name:
        raise OutboxDeliveryError("provider_retryable", retryable=True)

    async with session_factory() as session:
        current = await _routing_snapshot(session, user_id)
        if current is None:
            await session.rollback()
            return
        if current.should_enable != snapshot.should_enable:
            await session.rollback()
            raise OutboxDeliveryError("provider_retryable", retryable=True)
        phone_number = await session.get(
            PhoneNumber,
            snapshot.phone_number_id,
            with_for_update=True,
        )
        if phone_number is None:
            await session.rollback()
            return
        phone_number.provider_connection_name = provider_connection_name
        phone_number.is_active = provider_connection_name == "app-active"
        await session.commit()


async def _routing_snapshot(session, user_id: UUID) -> _RoutingSnapshot | None:
    phone_number = await PhoneNumberRepository(session).get_by_user_id(user_id)
    if phone_number is None:
        return None
    if not phone_number.provider_number_id:
        raise OutboxDeliveryError("provider_terminal", retryable=False)
    subscription = await SubscriptionRepository(session).get_by_user_id(user_id)
    agent_config = await AgentConfigRepository(session).get_by_user_id(user_id)
    balance = await UsageRepository(session).get_current_balance(user_id=user_id)
    should_enable = bool(
        subscription is not None
        and SubscriptionAccessPolicy.can_route(
            subscription.status,
            subscription.current_period_end,
        )
        and balance > 0
        and agent_config is not None
        and agent_config.is_enabled
        and OnboardingService._is_agent_setup_complete(agent_config)
    )
    return _RoutingSnapshot(
        phone_number_id=phone_number.id,
        provider_number_id=phone_number.provider_number_id,
        should_enable=should_enable,
        is_active=phone_number.is_active,
        provider_connection_name=phone_number.provider_connection_name,
    )


async def deliver_livekit_dispatch(
    ctx: dict[str, Any],
    event: OutboxEvent,
) -> None:
    call_id = _validated_dispatch_call_id(event)
    session_factory = ctx.get("session_factory") or get_session_factory()

    async with livekit_dispatch_lock(session_factory, call_id):
        snapshot = await _dispatch_snapshot(session_factory, call_id)
        provider = ctx.get("livekit_dispatch_provider")
        if provider is None:
            provider = LiveKitDispatchAPIProvider()

        try:
            dispatches = await provider.list_dispatches(
                room_name=snapshot.room_name
            )
        except ValueError:
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            ) from None
        except Exception:
            raise OutboxDeliveryError(
                "provider_retryable",
                retryable=True,
            ) from None

        dispatch = _reconcile_dispatches(snapshot, dispatches)
        if dispatch is None:
            if snapshot.persisted_dispatch_id is not None:
                raise OutboxDeliveryError(
                    "dispatch_conflict",
                    retryable=False,
                )
            try:
                created_dispatch = await provider.create_dispatch(
                    agent_name=snapshot.worker_name,
                    room_name=snapshot.room_name,
                    metadata=snapshot.metadata,
                )
            except ValueError:
                raise OutboxDeliveryError(
                    "dispatch_configuration",
                    retryable=False,
                ) from None
            except Exception:
                try:
                    dispatches = await provider.list_dispatches(
                        room_name=snapshot.room_name
                    )
                except Exception:
                    raise OutboxDeliveryError(
                        "provider_retryable",
                        retryable=True,
                    ) from None
                dispatch = _reconcile_dispatches(snapshot, dispatches)
                if dispatch is None:
                    raise OutboxDeliveryError(
                        "provider_retryable",
                        retryable=True,
                    ) from None
            else:
                dispatch = _reconcile_dispatches(
                    snapshot,
                    [created_dispatch],
                )

        await _persist_dispatch_identity(
            session_factory,
            call_id=call_id,
            dispatch_id=dispatch.id,
        )


def _validated_dispatch_call_id(event: OutboxEvent) -> UUID:
    try:
        call_id = UUID(event.payload["call_id"])
    except (KeyError, TypeError, ValueError):
        raise OutboxDeliveryError(
            "dispatch_configuration",
            retryable=False,
        ) from None
    if event.aggregate_type != "call" or event.aggregate_id != call_id:
        raise OutboxDeliveryError(
            "dispatch_configuration",
            retryable=False,
        )
    return call_id


async def _dispatch_snapshot(session_factory, call_id: UUID) -> _DispatchSnapshot:
    async with session_factory() as session:
        call_repository = CallRepository(session)
        call = await call_repository.get_by_id(call_id)
        if call is None or call.agent_config_id is None or not call.livekit_room_id:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            )

        user = await UserRepository(session).get_by_id_for_update(call.user_id)
        if user is None:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            )
        await session.refresh(call)

        # Keep this order aligned with webhook admission: phone, subscription,
        # then agent config after the User row serialization boundary.
        phone = (
            await PhoneNumberRepository(session).get_by_id_for_update(
                call.phone_number_id
            )
            if call.phone_number_id is not None
            else None
        )
        subscription = await SubscriptionRepository(
            session
        ).get_by_user_id_for_update(call.user_id)
        agent_config = await AgentConfigRepository(
            session
        ).get_by_user_id_for_update(call.user_id)
        balance = await UsageRepository(session).get_current_balance(
            user_id=call.user_id
        )

        called_number_matches = bool(
            phone is not None
            and phone.id == call.phone_number_id
            and phone.user_id == call.user_id
            and bool(phone.e164)
        )
        eligible = bool(
            user.status == "active"
            and call.status in {"pending", "connected"}
            and subscription is not None
            and agent_config is not None
            and agent_config.id == call.agent_config_id
            and DispatchEligibilityPolicy.can_dispatch(
                subscription_status=subscription.status,
                current_period_start=subscription.current_period_start,
                current_period_end=subscription.current_period_end,
                balance=balance,
                phone_active=bool(phone is not None and phone.is_active),
                agent_enabled=agent_config.is_enabled,
                setup_complete=_agent_setup_complete(agent_config),
                called_number_matches=called_number_matches,
            )
        )
        if not eligible:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_ineligible",
                retryable=False,
            )

        try:
            settings = get_settings()
            worker_name = settings.livekit_agent_name.strip()
            if not worker_name:
                raise ValueError("LiveKit agent worker name is not configured")
            dispatch_token = create_dispatch_token(
                call_id=str(call.id),
                user_id=str(call.user_id),
                agent_config_id=str(agent_config.id),
            )
            metadata = LiveKitDispatchMetadata(
                user_id=str(call.user_id),
                agent_config_id=str(agent_config.id),
                call_id=str(call.id),
                agent_identity=expected_agent_identity(call.id),
                minutes_remaining=balance,
                allowed_duration_seconds=calculate_allowed_duration(
                    minutes_remaining=balance,
                    maximum=settings.max_call_duration_seconds,
                ),
                agent_name=agent_config.agent_name,
                owner_name=user.full_name or user.email,
                owner_context=agent_config.owner_context,
                system_prompt=agent_config.system_prompt,
                knowledge_base=agent_config.knowledge_base,
                pipeline_mode=agent_config.pipeline_mode,
                dispatch_token=dispatch_token,
            ).model_dump_json()
        except Exception:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            ) from None

        snapshot = _DispatchSnapshot(
            call_id=call.id,
            user_id=call.user_id,
            agent_config_id=agent_config.id,
            room_name=call.livekit_room_id,
            worker_name=worker_name,
            metadata=metadata,
            persisted_dispatch_id=call.livekit_dispatch_id,
        )
        await session.commit()
        return snapshot


def _reconcile_dispatches(
    snapshot: _DispatchSnapshot,
    dispatches: list[LiveKitDispatch],
) -> LiveKitDispatch | None:
    matches: list[LiveKitDispatch] = []
    for dispatch in dispatches:
        try:
            metadata = json.loads(dispatch.metadata)
        except (TypeError, ValueError):
            metadata = None
        if (
            dispatch.agent_name == snapshot.worker_name
            and dispatch.room == snapshot.room_name
            and isinstance(metadata, dict)
            and metadata.get("call_id") == str(snapshot.call_id)
        ):
            matches.append(dispatch)

    if not dispatches:
        return None
    if len(dispatches) == 1 and len(matches) == 1 and matches[0].id:
        if (
            snapshot.persisted_dispatch_id is not None
            and matches[0].id != snapshot.persisted_dispatch_id
        ):
            raise OutboxDeliveryError(
                "dispatch_conflict",
                retryable=False,
            )
        return matches[0]
    raise OutboxDeliveryError(
        "dispatch_conflict",
        retryable=False,
    )


async def _persist_dispatch_identity(
    session_factory,
    *,
    call_id: UUID,
    dispatch_id: str,
) -> None:
    if not dispatch_id:
        raise OutboxDeliveryError(
            "dispatch_conflict",
            retryable=False,
        )
    async with session_factory() as session:
        call = await CallRepository(session).get_by_id_for_update(call_id)
        if call is None:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            )
        if call.livekit_dispatch_id not in (None, dispatch_id):
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_conflict",
                retryable=False,
            )
        await CallRepository(session).set_livekit_dispatch_id(
            call,
            livekit_dispatch_id=dispatch_id,
        )
        await session.commit()


async def deliver_summary_generate(
    ctx: dict[str, Any],
    event: OutboxEvent,
) -> None:
    call_id = _validated_post_call_reference(
        event,
        topic="summary.generate",
        aggregate_type="call-summary",
    )
    session_factory = ctx.get("session_factory") or get_session_factory()
    async with session_factory() as session:
        call = await CallRepository(session).get_by_id(call_id)
        if call is None:
            await session.rollback()
            raise OutboxDeliveryError("provider_terminal", retryable=False)
        messages = await MessageRepository(session).list_by_call_id(call_id)
        transcript_max_sequence = (
            messages[-1].sequence_number if messages else 0
        )
        if (
            call.summary_transcript_max_sequence is not None
            and call.summary_transcript_max_sequence >= transcript_max_sequence
            and (transcript_max_sequence == 0 or call.summary_data is not None)
        ):
            await session.commit()
            return
        transcript = [
            {"speaker": message.speaker, "text": message.text}
            for message in messages
        ]
        await session.commit()

    summary_data = None
    if transcript:
        provider = ctx.get("summary_provider") or GeminiSummaryProvider()
        try:
            structured = await provider.generate_summary(transcript)
            summary_data = SummaryService.validate_structured_summary(structured)
        except Exception:
            raise OutboxDeliveryError("provider_retryable", retryable=True) from None
        if summary_data is None:
            raise OutboxDeliveryError("provider_retryable", retryable=True)

    async with session_factory() as session:
        call = await CallRepository(session).get_by_id_for_update(call_id)
        if call is None:
            await session.rollback()
            raise OutboxDeliveryError("provider_terminal", retryable=False)
        durable_max_sequence = await MessageRepository(
            session
        ).max_sequence_by_call_id(call_id)
        if durable_max_sequence != transcript_max_sequence:
            await session.rollback()
            raise OutboxDeliveryError("summary_stale", retryable=True)
        if (
            call.summary_transcript_max_sequence is not None
            and call.summary_transcript_max_sequence >= durable_max_sequence
            and (durable_max_sequence == 0 or call.summary_data is not None)
        ):
            await session.commit()
            return
        if summary_data is not None:
            call.summary_text = summary_data["summary_text"]
            call.summary_data = summary_data
        call.summary_transcript_max_sequence = durable_max_sequence
        await session.flush()
        await session.commit()


async def deliver_recording_stop(
    ctx: dict[str, Any],
    event: OutboxEvent,
) -> None:
    call_id = _validated_post_call_reference(
        event,
        topic="recording.stop",
        aggregate_type="call-recording",
    )
    session_factory = ctx.get("session_factory") or get_session_factory()
    async with session_factory() as session:
        call = await CallRepository(session).get_by_id(call_id)
        if call is None:
            await session.rollback()
            raise OutboxDeliveryError("provider_terminal", retryable=False)
        egress_id = call.recording_egress_id
        await session.commit()
    if not egress_id:
        return

    provider = ctx.get("livekit_recording_provider")
    provider = provider or LiveKitRecordingService()
    try:
        await provider.ensure_stopped(egress_id)
    except Exception:
        raise OutboxDeliveryError("provider_retryable", retryable=True) from None


def _validated_post_call_reference(
    event: OutboxEvent,
    *,
    topic: str,
    aggregate_type: str,
) -> UUID:
    try:
        call_id = UUID(event.payload["call_id"])
    except (KeyError, TypeError, ValueError):
        raise OutboxDeliveryError("invalid_payload", retryable=False) from None
    if (
        event.topic != topic
        or event.aggregate_type != aggregate_type
        or event.aggregate_id != call_id
    ):
        raise OutboxDeliveryError("invalid_payload", retryable=False)
    return call_id


DEFAULT_OUTBOX_HANDLERS = {
    "phone.provision": deliver_phone_provision,
    "phone.enable": deliver_phone_routing,
    "phone.disable": deliver_phone_routing,
    "livekit.dispatch": deliver_livekit_dispatch,
    "summary.generate": deliver_summary_generate,
    "recording.stop": deliver_recording_stop,
}
