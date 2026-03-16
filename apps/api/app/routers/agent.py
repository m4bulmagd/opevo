import base64
import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.core.auth import UserIdentity, require_user_identity
from app.core.config import get_settings
from app.schemas.calls import AgentCallCompletionRequest, AgentCallCompletionResponse
from app.schemas.auth import UserIdentityResponse
from app.workers.call_finalization_queue import CallFinalizationQueue


router = APIRouter(prefix="/api/agent", tags=["agent"])


async def require_agent_internal_token(x_agent_token: str = Header(...)) -> None:
    settings = get_settings()
    expected_token = settings.agent_internal_api_token
    if not expected_token or not hmac.compare_digest(x_agent_token, expected_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent token")


def get_call_finalization_queue(request: Request) -> CallFinalizationQueue:
    queue = getattr(request.app.state, "call_finalization_queue", None)
    if queue is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Call finalization queue unavailable",
        )
    return queue


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
    queue: CallFinalizationQueue = Depends(get_call_finalization_queue),
) -> AgentCallCompletionResponse:
    recording_bytes = (
        base64.b64decode(payload.recording_bytes_base64.encode("utf-8"))
        if payload.recording_bytes_base64
        else None
    )
    job_id = await queue.enqueue(
        {
            "call_id": str(call_id),
            "user_id": str(payload.user_id),
            "duration_seconds": payload.duration_seconds,
            "minutes_remaining": payload.minutes_remaining,
            "caller_number": payload.caller_number,
            "transcript": [line.model_dump() for line in payload.transcript],
            "recording_bytes": recording_bytes,
        }
    )
    return AgentCallCompletionResponse(
        status="accepted",
        queued=True,
        job_id=job_id,
    )
