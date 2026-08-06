from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUserIdentity, require_user_identity
from app.composition.runtime import get_api_runtime
from app.core.database import get_session
from app.schemas.activation import ActivationSnapshotResponse
from app.schemas.onboarding import OnboardingStatusResponse
from app.services.onboarding_service import OnboardingRetryNotAllowedError, OnboardingService


router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


def get_onboarding_service(session: AsyncSession = Depends(get_session)) -> OnboardingService:
    return OnboardingService(session)


@router.get("", response_model=OnboardingStatusResponse)
async def get_onboarding_status(
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: OnboardingService = Depends(get_onboarding_service),
) -> OnboardingStatusResponse:
    return await service.get_status(identity.internal_user_id)


@router.post(
    "/retry-provisioning",
    response_model=ActivationSnapshotResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_provisioning(
    request: Request,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: OnboardingService = Depends(get_onboarding_service),
) -> ActivationSnapshotResponse:
    try:
        return await service.retry_provisioning(
            identity.internal_user_id,
            arq_pool=get_api_runtime(request.app).arq_pool,
        )
    except OnboardingRetryNotAllowedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": error.code},
        ) from None
