import base64
import hmac
from uuid import UUID
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import UserIdentity, require_user_identity
from app.core.config import get_settings
from app.core.database import get_session
from app.schemas.calls import AgentCallCompletionRequest, AgentCallCompletionResponse
from app.schemas.auth import UserIdentityResponse
from app.services.call_lifecycle_service import CallLifecycleService


router = APIRouter(prefix="/api/agent", tags=["agent"])


async def require_agent_internal_token(x_agent_token: str = Header(...)) -> None:
    settings = get_settings()
    expected_token = settings.agent_internal_api_token
    if not expected_token or not hmac.compare_digest(x_agent_token, expected_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent token")


async def get_call_lifecycle_service(
    session: AsyncSession = Depends(get_session),
) -> CallLifecycleService:
    return CallLifecycleService(session)


@router.get("/config", response_model=UserIdentityResponse)
async def get_agent_config(identity: UserIdentity = Depends(require_user_identity)) -> UserIdentityResponse:
    return UserIdentityResponse(user_id=identity.user_id)


@router.post(
    "/calls/{call_id}/complete",
    response_model=AgentCallCompletionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def complete_call(
    call_id: UUID,
    payload: AgentCallCompletionRequest,
    _: None = Depends(require_agent_internal_token),
    lifecycle_service: Any = Depends(get_call_lifecycle_service),
) -> AgentCallCompletionResponse:
    recording_bytes = (
        base64.b64decode(payload.recording_bytes_base64.encode("utf-8"))
        if payload.recording_bytes_base64
        else None
    )
    result = await lifecycle_service.finalize_call(
        {
            "call_id": str(call_id),
            "user_id": payload.user_id,
            "duration_seconds": payload.duration_seconds,
            "minutes_remaining": payload.minutes_remaining,
            "caller_number": payload.caller_number,
            "transcript": [line.model_dump() for line in payload.transcript],
            "recording_bytes": recording_bytes,
        }
    )
    return AgentCallCompletionResponse(
        status="accepted",
        minutes_charged=result.minutes_charged,
        summary_text=result.summary_text,
        recording_key=result.recording_key,
        number_disabled=result.number_disabled,
    )
