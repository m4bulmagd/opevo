from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from presvo_contracts import (
    VERIFICATION_MESSAGE,
    ContractError,
    ForwardingVerificationDispatch,
    create_contract,
    dump_contract_json,
    parse_dispatch,
)

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.core.dispatch_token import (
    DispatchTokenConfigurationError,
    dispatch_token_config,
)
from app.core.verification_token import (
    VerificationTokenError,
    create_verification_token,
)
from app.models.customer_activation import CustomerActivation
from app.models.outbox_event import OutboxEvent
from app.providers.livekit_dispatch.base import LiveKitDispatch
from app.providers.livekit_dispatch.livekit import LiveKitDispatchAPIProvider
from app.repositories.customer_activation_repository import (
    CustomerActivationRepository,
)
from app.repositories.user_repository import UserRepository
from app.services.forwarding_verification_service import COMPLETION_GRACE, as_utc
from app.services.livekit_dispatch_lock import verification_dispatch_lock
from app.workers.outbox._account_lifecycle import _require_current_worker_account
from app.workers.outbox._livekit_delivery import ensure_livekit_dispatch
from app.workers.outbox.failures import OutboxDeliveryError


@dataclass(frozen=True)
class _VerificationDispatchSnapshot:
    activation_id: UUID
    user_id: UUID
    session_id: str
    room_name: str
    worker_name: str
    metadata: str
    persisted_dispatch_id: str | None


def _parse_verification_dispatch_metadata(
    metadata: object,
) -> ForwardingVerificationDispatch | None:
    try:
        parsed = parse_dispatch(metadata)
    except ContractError:
        return None
    if not isinstance(parsed, ForwardingVerificationDispatch):
        return None
    return parsed


async def deliver_livekit_verification_dispatch(
    ctx: dict[str, Any],
    event: OutboxEvent,
) -> None:
    activation_id, session_id, room_name, lifecycle_generation = (
        _validated_verification_dispatch_reference(event)
    )
    session_factory = ctx.get("session_factory") or get_session_factory()
    now_provider = ctx.get("verification_now") or (lambda: datetime.now(UTC))

    async with verification_dispatch_lock(session_factory, activation_id):
        snapshot = await _verification_dispatch_snapshot(
            session_factory,
            activation_id=activation_id,
            session_id=session_id,
            room_name=room_name,
            now=now_provider(),
        )
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
            return _reconcile_verification_dispatches(snapshot, dispatches)

        dispatch = await ensure_livekit_dispatch(
            provider=provider,
            room_name=snapshot.room_name,
            worker_name=snapshot.worker_name,
            metadata=snapshot.metadata,
            persisted_dispatch_id=snapshot.persisted_dispatch_id,
            revalidate_account=revalidate_account,
            reconcile=reconcile,
        )
        await _persist_verification_dispatch_identity(
            session_factory,
            activation_id=activation_id,
            session_id=session_id,
            dispatch_id=dispatch.id,
        )


def _validated_verification_dispatch_reference(
    event: OutboxEvent,
) -> tuple[UUID, str, str, int]:
    try:
        if set(event.payload) != {
            "activation_id",
            "session_id",
            "room_name",
            "lifecycle_generation",
        }:
            raise ValueError
        activation_id = UUID(event.payload["activation_id"])
        session_id = str(UUID(event.payload["session_id"]))
        room_name = event.payload["room_name"]
        lifecycle_generation = event.payload["lifecycle_generation"]
        if not isinstance(room_name, str) or not room_name:
            raise ValueError
        if type(lifecycle_generation) is not int or lifecycle_generation < 1:
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise OutboxDeliveryError(
            "dispatch_configuration",
            retryable=False,
        ) from None
    if (
        event.topic != "livekit.verification_dispatch"
        or event.aggregate_type != "forwarding-verification"
        or event.aggregate_id != activation_id
        or event.idempotency_key != f"livekit.verification_dispatch:{session_id}"
    ):
        raise OutboxDeliveryError(
            "dispatch_configuration",
            retryable=False,
        )
    return activation_id, session_id, room_name, lifecycle_generation


async def _verification_dispatch_snapshot(
    session_factory,
    *,
    activation_id: UUID,
    session_id: str,
    room_name: str,
    now: datetime,
) -> _VerificationDispatchSnapshot:
    async with session_factory() as session:
        resolved_activation = await session.get(CustomerActivation, activation_id)
        if resolved_activation is None:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            )
        user_id = resolved_activation.user_id
        user = await UserRepository(session).get_by_id_for_update(user_id)
        activation = await CustomerActivationRepository(
            session
        ).get_by_user_id_for_update(user_id)
        if (
            user is None
            or user.status != "active"
            or activation is None
            or activation.id != activation_id
            or activation.verification_status != "claimed"
            or activation.verification_session_id != session_id
            or activation.verification_claimed_at is None
            or activation.verification_window_started_at is None
            or activation.verification_window_expires_at is None
            or as_utc(now) < as_utc(activation.verification_window_started_at)
            or as_utc(now)
            >= as_utc(activation.verification_window_expires_at) + COMPLETION_GRACE
        ):
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_ineligible",
                retryable=False,
            )

        settings = get_settings()
        worker_name = settings.livekit_agent_name.strip()
        if not worker_name:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            )
        try:
            metadata = dump_contract_json(
                create_contract(
                    ForwardingVerificationDispatch,
                    job_type="forwarding_verification",
                    verification_session_id=session_id,
                    user_id=user_id,
                    agent_identity=_verification_agent_identity(session_id),
                    completion_token=create_verification_token(
                        session_id=session_id,
                        user_id=str(user_id),
                        config=dispatch_token_config(settings),
                    ),
                    message=VERIFICATION_MESSAGE,
                    tts_provider="speechmatics",
                )
            )
        except (
            DispatchTokenConfigurationError,
            VerificationTokenError,
            ContractError,
        ):
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            ) from None

        snapshot = _VerificationDispatchSnapshot(
            activation_id=activation_id,
            user_id=user_id,
            session_id=session_id,
            room_name=room_name,
            worker_name=worker_name,
            metadata=metadata,
            persisted_dispatch_id=activation.verification_dispatch_id,
        )
        await session.commit()
        return snapshot


def _reconcile_verification_dispatches(
    snapshot: _VerificationDispatchSnapshot,
    dispatches: list[LiveKitDispatch],
) -> LiveKitDispatch | None:
    named_dispatches = [
        dispatch for dispatch in dispatches if dispatch.agent_name.strip()
    ]
    expected_session_id = UUID(snapshot.session_id)
    matches: list[LiveKitDispatch] = []
    for dispatch in named_dispatches:
        metadata = _parse_verification_dispatch_metadata(dispatch.metadata)
        if (
            dispatch.agent_name == snapshot.worker_name
            and dispatch.room == snapshot.room_name
            and metadata is not None
            and metadata.verification_session_id == expected_session_id
            and metadata.user_id == snapshot.user_id
            and metadata.agent_identity
            == _verification_agent_identity(snapshot.session_id)
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


def _verification_agent_identity(session_id: str) -> str:
    return f"agent-verification-{session_id}"


async def _persist_verification_dispatch_identity(
    session_factory,
    *,
    activation_id: UUID,
    session_id: str,
    dispatch_id: str,
) -> None:
    if not dispatch_id:
        raise OutboxDeliveryError(
            "dispatch_conflict",
            retryable=False,
        )
    async with session_factory() as session:
        resolved_activation = await session.get(CustomerActivation, activation_id)
        if resolved_activation is None:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            )
        user_id = resolved_activation.user_id
        user = await UserRepository(session).get_by_id_for_update(user_id)
        activation_repository = CustomerActivationRepository(session)
        activation = await activation_repository.get_by_user_id_for_update(user_id)
        if (
            user is None
            or user.status != "active"
            or activation is None
            or activation.id != activation_id
            or activation.verification_session_id != session_id
            or activation.verification_status not in {"claimed", "succeeded"}
        ):
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_conflict",
                retryable=False,
            )
        if activation.verification_dispatch_id not in (None, dispatch_id):
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_conflict",
                retryable=False,
            )
        await activation_repository.set_verification_dispatch_id(
            activation,
            dispatch_id=dispatch_id,
        )
        await session.commit()
