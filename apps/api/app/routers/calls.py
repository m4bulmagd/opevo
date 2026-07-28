import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import limiter

from app.core.auth import AuthenticatedUserIdentity, require_user_identity
from app.core.database import get_session
from app.schemas.calls import CallDetailResponse, CallHistoryListResponse
from app.services.call_history_service import (
    CallDateRange,
    CallDeleteActiveError,
    CallHistoryNotFoundError,
    CallHistoryService,
    CallStatusFilter,
)
from app.services.account_access_policy import AccountStateBlockedError
from app.services.recording_lifecycle_service import RecordingLifecycleService
from app.services.recording_service import RecordingService, get_recording_service


router = APIRouter(prefix="/api/calls", tags=["calls"])
logger = logging.getLogger(__name__)


def get_call_history_service(
    session: AsyncSession = Depends(get_session),
    recording_service: RecordingService = Depends(get_recording_service),
) -> CallHistoryService:
    return CallHistoryService(
        session,
        recording_service=recording_service,
        recording_lifecycle_service=RecordingLifecycleService(session),
    )


def get_call_deletion_service(
    session: AsyncSession = Depends(get_session),
) -> CallHistoryService:
    return CallHistoryService(
        session,
        recording_service=None,
        recording_lifecycle_service=RecordingLifecycleService(session),
    )


@router.get("", response_model=CallHistoryListResponse)
@limiter.limit("60/minute")
async def list_calls(
    request: Request,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: CallHistoryService = Depends(get_call_history_service),
    q: Annotated[str | None, Query(max_length=100)] = None,
    status_filter: Annotated[
        CallStatusFilter | None,
        Query(alias="status"),
    ] = None,
    date_range: Annotated[
        CallDateRange | None,
        Query(alias="range"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CallHistoryListResponse:
    page = await service.list_calls(
        identity.internal_user_id,
        limit=limit,
        offset=offset,
        query=q,
        status_filter=status_filter,
        date_range=date_range,
    )
    return CallHistoryListResponse(
        calls=page.calls,
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        has_more=page.has_more,
    )


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
@limiter.limit("60/minute")
async def delete_call(
    request: Request,
    call_id: UUID,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: CallHistoryService = Depends(get_call_deletion_service),
) -> Response:
    try:
        await service.delete_call(identity.internal_user_id, call_id)
    except CallHistoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found") from exc
    except CallDeleteActiveError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "call_delete_active"},
        ) from None
    except AccountStateBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code},
        ) from None
    arq_pool = getattr(request.app.state, "arq_pool", None)
    if arq_pool is not None:
        try:
            await arq_pool.enqueue_job("outbox_delivery_job", {})
        except Exception:
            logger.warning(
                "outbox wakeup enqueue failed operation=delete_call "
                "error_type=unknown"
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
