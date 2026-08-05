from dataclasses import dataclass
from typing import Any
from uuid import UUID

from presvo_contracts import (
    ContractError,
    CustomerCallDispatch,
    create_contract,
    dump_contract_json,
    parse_dispatch,
)

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.core.dispatch_token import (
    DispatchTokenError,
    create_dispatch_token,
)
from app.models.outbox_event import OutboxEvent
from app.providers.livekit_dispatch.base import LiveKitDispatch
from app.providers.livekit_dispatch.livekit import LiveKitDispatchAPIProvider
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.business_profile_repository import BusinessProfileRepository
from app.repositories.call_repository import CallRepository
from app.repositories.customer_activation_repository import (
    CustomerActivationRepository,
)
from app.repositories.phone_number_provisioning_repository import (
    PhoneNumberProvisioningRepository,
)
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.usage_repository import UsageRepository
from app.repositories.user_repository import UserRepository
from app.services.customer_readiness_service import evaluate_customer_readiness
from app.services.livekit_dispatch_lock import livekit_dispatch_lock
from app.services.livekit_dispatch_service import (
    calculate_allowed_duration,
    expected_agent_identity,
)
from app.workers.outbox._account_lifecycle import _require_current_worker_account
from app.workers.outbox._livekit_delivery import ensure_livekit_dispatch
from app.workers.outbox.failures import OutboxDeliveryError


@dataclass(frozen=True)
class _DispatchSnapshot:
    call_id: UUID
    user_id: UUID
    agent_config_id: UUID
    room_name: str
    worker_name: str
    metadata: str
    persisted_dispatch_id: str | None


async def deliver_livekit_dispatch(
    ctx: dict[str, Any],
    event: OutboxEvent,
) -> None:
    call_id, lifecycle_generation = _validated_dispatch_reference(event)
    session_factory = ctx.get("session_factory") or get_session_factory()

    async with livekit_dispatch_lock(session_factory, call_id):
        snapshot = await _dispatch_snapshot(session_factory, call_id)
        provider = ctx.get("livekit_dispatch_provider")
        if provider is None:
            provider = LiveKitDispatchAPIProvider()

        async def revalidate_account() -> None:
            await _require_current_worker_account(
                session_factory,
                snapshot.user_id,
                lifecycle_generation=lifecycle_generation,
            )

        def reconcile(
            dispatches: list[LiveKitDispatch],
        ) -> LiveKitDispatch | None:
            return _reconcile_dispatches(snapshot, dispatches)

        dispatch = await ensure_livekit_dispatch(
            provider=provider,
            room_name=snapshot.room_name,
            worker_name=snapshot.worker_name,
            metadata=snapshot.metadata,
            persisted_dispatch_id=snapshot.persisted_dispatch_id,
            revalidate_account=revalidate_account,
            reconcile=reconcile,
        )
        await _persist_dispatch_identity(
            session_factory,
            call_id=call_id,
            dispatch_id=dispatch.id,
        )


def _validated_dispatch_reference(event: OutboxEvent) -> tuple[UUID, int]:
    try:
        if set(event.payload) != {"call_id", "lifecycle_generation"}:
            raise ValueError
        call_id = UUID(event.payload["call_id"])
        lifecycle_generation = event.payload["lifecycle_generation"]
        if type(lifecycle_generation) is not int or lifecycle_generation < 1:
            raise ValueError
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
    return call_id, lifecycle_generation


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

        settings = get_settings()
        activation = None
        business_profile = None
        if settings.activation_flow_enabled:
            activation = await CustomerActivationRepository(
                session
            ).get_by_user_id_for_update(call.user_id)
            business_profile = await BusinessProfileRepository(
                session
            ).get_by_user_id_for_update(call.user_id)
        phone = (
            await PhoneNumberRepository(session).get_by_id_for_update(
                call.phone_number_id
            )
            if call.phone_number_id is not None
            else None
        )
        provisioning = await PhoneNumberProvisioningRepository(
            session
        ).get_by_user_id_for_update(call.user_id)
        subscription = await SubscriptionRepository(session).get_by_user_id_for_update(
            call.user_id
        )
        agent_config = await AgentConfigRepository(session).get_by_user_id_for_update(
            call.user_id
        )
        balance = await UsageRepository(session).get_current_balance(
            user_id=call.user_id
        )
        if subscription is None or agent_config is None:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_ineligible",
                retryable=False,
            )

        called_number_matches = bool(
            phone is not None
            and phone.id == call.phone_number_id
            and phone.user_id == call.user_id
            and bool(phone.e164)
        )
        readiness = evaluate_customer_readiness(
            user=user,
            subscription=subscription,
            balance=balance,
            phone_number=phone,
            provisioning=provisioning,
            agent_config=agent_config,
            business_profile=business_profile,
            activation=activation,
            activation_required=settings.activation_flow_enabled,
        )
        eligible = bool(
            user.status == "active"
            and call.status in {"pending", "connected"}
            and agent_config.id == call.agent_config_id
            and readiness.can_dispatch(called_number_matches=called_number_matches)
        )
        if not eligible:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_ineligible",
                retryable=False,
            )

        worker_name = settings.livekit_agent_name.strip()
        if not worker_name:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            )
        business_display_name = (agent_config.business_display_name or "").strip()
        if settings.activation_flow_enabled and not business_display_name:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            )
        try:
            dispatch_token = create_dispatch_token(
                call_id=str(call.id),
                user_id=str(call.user_id),
                agent_config_id=str(agent_config.id),
            )
            metadata = dump_contract_json(
                create_contract(
                    CustomerCallDispatch,
                    job_type="customer_call",
                    user_id=call.user_id,
                    agent_config_id=agent_config.id,
                    call_id=call.id,
                    agent_identity=expected_agent_identity(call.id),
                    minutes_remaining=balance,
                    allowed_duration_seconds=calculate_allowed_duration(
                        minutes_remaining=balance,
                        maximum=settings.max_call_duration_seconds,
                    ),
                    agent_name=agent_config.agent_name,
                    owner_name=(
                        business_display_name
                        or (user.full_name or "").strip()
                        or "the business"
                    ),
                    owner_context=agent_config.owner_context,
                    system_prompt=agent_config.system_prompt,
                    knowledge_base=agent_config.knowledge_base,
                    pipeline_mode=agent_config.pipeline_mode,
                    dispatch_token=dispatch_token,
                )
            )
        except (DispatchTokenError, ContractError):
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


def _parse_customer_dispatch_metadata(
    metadata: object,
) -> CustomerCallDispatch | None:
    try:
        parsed = parse_dispatch(metadata)
    except ContractError:
        return None
    if not isinstance(parsed, CustomerCallDispatch):
        return None
    return parsed


def _reconcile_dispatches(
    snapshot: _DispatchSnapshot,
    dispatches: list[LiveKitDispatch],
) -> LiveKitDispatch | None:
    named_dispatches = [
        dispatch for dispatch in dispatches if dispatch.agent_name.strip()
    ]
    matches: list[LiveKitDispatch] = []
    for dispatch in named_dispatches:
        metadata = _parse_customer_dispatch_metadata(dispatch.metadata)
        if (
            dispatch.agent_name == snapshot.worker_name
            and dispatch.room == snapshot.room_name
            and metadata is not None
            and metadata.call_id == snapshot.call_id
            and metadata.user_id == snapshot.user_id
            and metadata.agent_config_id == snapshot.agent_config_id
            and metadata.agent_identity == expected_agent_identity(snapshot.call_id)
        ):
            matches.append(dispatch)

    if not named_dispatches:
        return None
    if len(named_dispatches) == 1 and len(matches) == 1 and matches[0].id:
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
