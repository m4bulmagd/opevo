from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import UserIdentity, require_user_identity
from app.core.database import get_session
from app.repositories.call_repository import CallRepository
from app.schemas.calls import CallResponse


router = APIRouter(prefix="/api/calls", tags=["calls"])


@router.get("/{call_id}", response_model=CallResponse)
async def get_call(
    call_id: UUID,
    identity: UserIdentity = Depends(require_user_identity),
    session: AsyncSession = Depends(get_session),
) -> CallResponse:
    call = await CallRepository(session).get_by_id(call_id)
    if call is None or str(call.user_id) != identity.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found")

    return CallResponse.model_validate(call, from_attributes=True)
