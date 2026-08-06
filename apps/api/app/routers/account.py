from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUserIdentity, require_user_identity
from app.composition.runtime import get_api_runtime
from app.core.database import get_session
from app.core.rate_limit import limiter
from app.schemas.account import AccountDeactivateRequest, AccountStatusResponse
from app.services.account_lifecycle_service import AccountLifecycleService


router = APIRouter(prefix="/api/account", tags=["account"])


def get_account_lifecycle_service(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AccountLifecycleService:
    return AccountLifecycleService(
        session,
        activation_flow_enabled=(
            get_api_runtime(request.app).settings.activation_flow_enabled
        ),
        arq_pool=get_api_runtime(request.app).arq_pool,
    )


@router.get("", response_model=AccountStatusResponse)
async def get_account(
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: AccountLifecycleService = Depends(get_account_lifecycle_service),
) -> AccountStatusResponse:
    return await service.get_account(identity.internal_user_id)


@router.post(
    "/deactivate",
    response_model=AccountStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit("5/minute")
async def deactivate_account(
    request: Request,
    payload: AccountDeactivateRequest,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: AccountLifecycleService = Depends(get_account_lifecycle_service),
) -> AccountStatusResponse:
    return await service.request_owner_deactivation(
        identity.internal_user_id,
        payload.confirmation,
    )
