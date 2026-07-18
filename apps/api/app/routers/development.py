from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUserIdentity, require_user_identity
from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.schemas.activation import ActivationSnapshotResponse
from app.services.activation_snapshot_service import ActivationSnapshotService
from app.services.forwarding_verification_service import (
    ForwardingVerificationConflictError,
    ForwardingVerificationService,
)
from app.services.local_billing_service import (
    LocalBillingConflictError,
    LocalBillingService,
)


router = APIRouter(prefix="/api/development", tags=["development"])


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
    if _request_settings(request).telephony_mode != "fake":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "local_telephony_disabled"},
        )

    try:
        claim = await service.claim_for_user(identity.internal_user_id)
        await service.complete(session_id=claim.session_id)
    except ForwardingVerificationConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": error.code},
        ) from None
    return await snapshot_service.get(identity.internal_user_id)
