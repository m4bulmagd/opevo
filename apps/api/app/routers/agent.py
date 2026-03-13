from fastapi import APIRouter, Depends

from app.core.auth import UserIdentity, require_user_identity
from app.schemas.auth import UserIdentityResponse


router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/config", response_model=UserIdentityResponse)
async def get_agent_config(identity: UserIdentity = Depends(require_user_identity)) -> UserIdentityResponse:
    return UserIdentityResponse(user_id=identity.user_id)
