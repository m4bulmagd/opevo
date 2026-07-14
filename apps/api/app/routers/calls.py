from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import limiter

from app.core.auth import AuthenticatedUserIdentity, require_user_identity
from app.core.database import get_session
from app.schemas.calls import CallDetailResponse, CallHistoryListResponse
from app.services.call_history_service import CallHistoryNotFoundError, CallHistoryService
from app.services.recording_service import RecordingService, get_recording_service


router = APIRouter(prefix="/api/calls", tags=["calls"])


def get_call_history_service(
    session: AsyncSession = Depends(get_session),
    recording_service: RecordingService = Depends(get_recording_service),
) -> CallHistoryService:
    return CallHistoryService(session, recording_service=recording_service)


@router.get("", response_model=CallHistoryListResponse)
@limiter.limit("60/minute")
async def list_calls(
    request: Request,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: CallHistoryService = Depends(get_call_history_service),
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CallHistoryListResponse:
    calls = await service.list_calls(identity.internal_user_id, limit=limit, offset=offset)
    return CallHistoryListResponse(calls=calls)


@router.get("/{call_id}", response_model=CallDetailResponse)
@limiter.limit("60/minute")
async def get_call(
    request: Request,
    call_id: UUID,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: CallHistoryService = Depends(get_call_history_service),
) -> CallDetailResponse:
    try:
        return await service.get_call_detail(identity.internal_user_id, call_id)
    except CallHistoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found") from exc


@router.delete("/{call_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_call(
    call_id: UUID,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: CallHistoryService = Depends(get_call_history_service),
) -> Response:
    try:
        await service.delete_call(identity.internal_user_id, call_id)
    except CallHistoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
