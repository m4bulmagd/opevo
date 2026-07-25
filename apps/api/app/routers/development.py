from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUserIdentity, require_user_identity
from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.call_repository import CallRepository, CallTransitionError
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.schemas.activation import ActivationSnapshotResponse
from app.services.activation_snapshot_service import ActivationSnapshotService
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.forwarding_verification_service import (
    ForwardingVerificationConflictError,
    ForwardingVerificationService,
)
from app.services.local_billing_service import (
    LocalBillingConflictError,
    LocalBillingService,
)


router = APIRouter(prefix="/api/development", tags=["development"])


class CallDrainFixturePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: UUID


class CallDrainFixtureResponse(BaseModel):
    call_id: UUID


def get_local_billing_service(
    session: AsyncSession = Depends(get_session),
) -> LocalBillingService:
    return LocalBillingService(session)


def get_development_activation_snapshot_service(
    session: AsyncSession = Depends(get_session),
) -> ActivationSnapshotService:
    return ActivationSnapshotService(session)


def get_development_forwarding_verification_service(
    session: AsyncSession = Depends(get_session),
) -> ForwardingVerificationService:
    return ForwardingVerificationService(session)


def _request_settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def _require_fake_telephony(request: Request) -> None:
    if _request_settings(request).telephony_mode != "fake":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "local_telephony_disabled"},
        )


def _require_local_call_fixture(request: Request) -> None:
    settings = _request_settings(request)
    if settings.auth_mode != "local" or settings.telephony_mode != "fake":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "local_telephony_disabled"},
        )


@router.post(
    "/activate-starter",
    response_model=ActivationSnapshotResponse,
)
async def activate_starter(
    request: Request,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: LocalBillingService = Depends(get_local_billing_service),
    snapshot_service: ActivationSnapshotService = Depends(
        get_development_activation_snapshot_service
    ),
) -> ActivationSnapshotResponse:
    if _request_settings(request).billing_mode != "fake":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "local_billing_disabled"},
        )

    try:
        await service.activate_starter(
            identity.internal_user_id,
            now=datetime.now(UTC),
        )
    except LocalBillingConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": error.code},
        ) from None
    return await snapshot_service.get(identity.internal_user_id)


@router.post(
    "/simulate-forwarded-call",
    response_model=ActivationSnapshotResponse,
)
async def simulate_forwarded_call(
    request: Request,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: ForwardingVerificationService = Depends(
        get_development_forwarding_verification_service
    ),
    snapshot_service: ActivationSnapshotService = Depends(
        get_development_activation_snapshot_service
    ),
) -> ActivationSnapshotResponse:
    _require_fake_telephony(request)

    try:
        claim = await service.claim_for_user(identity.internal_user_id)
        await service.complete(session_id=claim.session_id)
    except ForwardingVerificationConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": error.code},
        ) from None
    return await snapshot_service.get(identity.internal_user_id)


@router.post(
    "/call-drain-fixture/start",
    response_model=CallDrainFixtureResponse,
)
async def start_call_drain_fixture(
    request: Request,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    session: AsyncSession = Depends(get_session),
) -> CallDrainFixtureResponse:
    _require_local_call_fixture(request)

    call_repository = CallRepository(session)
    phone_number = await PhoneNumberRepository(session).get_by_user_id(
        identity.internal_user_id
    )
    agent_config = await AgentConfigRepository(session).get_by_user_id(
        identity.internal_user_id
    )
    call = await call_repository.create_pending(
        user_id=identity.internal_user_id,
        phone_number_id=phone_number.id if phone_number is not None else None,
        agent_config_id=agent_config.id if agent_config is not None else None,
        caller_number="+33199000001",
    )
    connected_call = await call_repository.connect_if_pending(call_id=call.id)
    if connected_call is None:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "call_fixture_conflict"},
        )
    await session.commit()
    return CallDrainFixtureResponse(call_id=connected_call.id)


@router.post(
    "/call-drain-fixture/finish",
    response_model=CallDrainFixtureResponse,
)
async def finish_call_drain_fixture(
    payload: CallDrainFixturePayload,
    request: Request,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    session: AsyncSession = Depends(get_session),
) -> CallDrainFixtureResponse:
    _require_local_call_fixture(request)

    owned_call = await CallRepository(session).get_visible_by_id(
        payload.call_id,
        user_id=identity.internal_user_id,
    )
    if owned_call is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "call_fixture_not_found"},
        )

    lifecycle = CallLifecycleService(session)
    try:
        await lifecycle.end_from_agent(
            call_id=owned_call.id,
            duration_seconds=1,
        )
        claim = await lifecycle.claim_finalization(owned_call.id)
        if claim.unavailable:
            raise CallTransitionError("Call fixture cannot be finalized")
        await lifecycle.complete_finalization(
            owned_call.id,
            generation=claim.generation,
        )
    except (CallTransitionError, ValueError):
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "call_fixture_conflict"},
        ) from None
    return CallDrainFixtureResponse(call_id=owned_call.id)
