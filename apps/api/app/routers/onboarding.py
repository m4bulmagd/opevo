from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUserIdentity, require_user_identity
from app.core.database import get_session
from app.schemas.onboarding import OnboardingStatusResponse, RetryProvisioningResponse
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


@router.post("/retry-provisioning", response_model=RetryProvisioningResponse)
async def retry_provisioning(
    request: Request,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: OnboardingService = Depends(get_onboarding_service),
) -> RetryProvisioningResponse:
    try:
        return await service.retry_provisioning(
            identity.internal_user_id,
            arq_pool=getattr(request.app.state, "arq_pool", None),
        )
    except OnboardingRetryNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Provisioning retry not allowed") from exc
