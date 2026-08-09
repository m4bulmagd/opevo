import logging

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.domain import AuthenticatedUser
from app.auth.failures import UserNotProvisioned
from app.auth.providers.base import AuthProvider
from app.composition.runtime import ApiRuntimeUnavailable, get_api_runtime
from app.core.auth_failures import AuthenticationUnavailable, TokenRejected
from app.core.database import get_session
from app.services.authentication_service import AuthenticationService


logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)

def get_auth_provider(request: Request) -> AuthProvider:
    try:
        return get_api_runtime(request.app).auth_provider
    except ApiRuntimeUnavailable:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication provider not initialized",
        ) from None


async def require_user_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
    auth_provider: AuthProvider = Depends(get_auth_provider),
) -> AuthenticatedUser:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    try:
        return await AuthenticationService(
            session=session,
            auth_provider=auth_provider,
        ).authenticate(credentials.credentials)
    except TokenRejected as error:
        logger.warning(
            "event=auth_token_rejected operation=verify_token reason=%s",
            error.reason,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from None
    except UserNotProvisioned:
        logger.warning(
            "event=auth_user_not_provisioned operation=resolve_identity"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from None
    except AuthenticationUnavailable as error:
        logger.warning(
            "event=authentication_unavailable operation=verify_token reason=%s",
            error.reason,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication temporarily unavailable",
        ) from None
