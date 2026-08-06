import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUserIdentity, require_user_identity
from app.composition.runtime import get_api_runtime
from app.core.database import get_session
from app.core.dispatch_token import (
    DispatchTokenError,
    dispatch_token_config,
    verify_dispatch_token,
)
from app.core.contract_http import contract_request_openapi, parse_contract_request
from app.models.agent_config import AgentConfig
from app.models.call import Call
from app.schemas.agent import AgentConfigPatchRequest, AgentConfigResponse
from app.schemas.agent_identity import AuthenticatedAgentIdentity
from app.repositories.agent_config_repository import AgentConfigRepository
from app.services.agent_config_service import (
    AgentConfigEnableManagedByActivationError,
    AgentConfigNotFoundError,
    AgentConfigPhoneNumberNotFoundError,
    AgentConfigReadinessError,
    AgentConfigService,
    AgentConfigTelephonySyncError,
)
from app.services.account_access_policy import AccountStateBlockedError
from app.repositories.call_repository import CallTransitionError
from app.repositories.message_repository import MessageRepository
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.customer_readiness_service import CustomerReadinessService
from app.services.outbox_service import OutboxService
from app.services.transcript_service import (
    TranscriptCallNotFoundError,
    TranscriptCallNotAcceptingError,
    TranscriptAuthorizationError,
    TranscriptSequenceConflictError,
    TranscriptService,
)
from app.workers.call_finalization_queue import CallFinalizationQueue
from app.workers.queueing import enqueue_outbox_wakeup
from presvo_contracts import (
    CallCompletionAcknowledgement,
    CallCompletionRequest,
    TranscriptAppendAcknowledgement,
    TranscriptAppendRequest,
    create_contract,
)


router = APIRouter(prefix="/api/agent", tags=["agent"])
logger = logging.getLogger(__name__)


async def _best_effort_outbox_wakeup(request: Request) -> None:
    arq_pool = get_api_runtime(request.app).arq_pool
    if arq_pool is None:
        return
    try:
        await enqueue_outbox_wakeup(arq_pool)
    except Exception:
        logger.warning(
            "outbox wakeup enqueue failed operation=complete_call error_type=unknown"
        )


async def require_agent_auth(
    call_id: UUID,
    request: Request,
    x_agent_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> AuthenticatedAgentIdentity:
    if not isinstance(x_agent_token, str) or not x_agent_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid agent token",
        )

    try:
        claims = verify_dispatch_token(
            x_agent_token,
            expected_call_id=str(call_id),
            config=dispatch_token_config(get_api_runtime(request.app).settings),
        )
        signed_user_id = UUID(claims["user_id"])
        signed_agent_config_id = UUID(claims["agent_config_id"])
    except (DispatchTokenError, KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid agent token",
        ) from None

    call = await session.get(Call, call_id)
    if (
        call is None
        or call.user_id != signed_user_id
        or getattr(call, "agent_config_id", None) != signed_agent_config_id
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid agent token",
        )

    agent_config = await session.get(AgentConfig, signed_agent_config_id)
    if agent_config is None or agent_config.user_id != signed_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid agent token",
        )
    return AuthenticatedAgentIdentity(
        user_id=signed_user_id,
        agent_config_id=signed_agent_config_id,
    )


def get_call_finalization_queue(request: Request) -> CallFinalizationQueue:
    queue = get_api_runtime(request.app).call_finalization_queue
    if queue is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Call finalization queue unavailable",
        )
    return queue


def get_agent_config_service(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AgentConfigService:
    settings = get_api_runtime(request.app).settings
    return AgentConfigService(
        session,
        agent_config_repository=AgentConfigRepository(session),
        readiness_service=CustomerReadinessService(
            session,
            activation_flow_enabled=settings.activation_flow_enabled,
        ),
        activation_flow_enabled=settings.activation_flow_enabled,
        arq_pool=get_api_runtime(request.app).arq_pool,
    )


@router.get("/config", response_model=AgentConfigResponse)
async def get_agent_config(
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: AgentConfigService = Depends(get_agent_config_service),
) -> AgentConfigResponse:
    try:
        config = await service.get_by_user_id(identity.internal_user_id)
    except AgentConfigNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent config not found",
        ) from exc
    return AgentConfigResponse.model_validate(config, from_attributes=True)


@router.patch("/config", response_model=AgentConfigResponse)
async def patch_agent_config(
    payload: AgentConfigPatchRequest,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: AgentConfigService = Depends(get_agent_config_service),
) -> AgentConfigResponse:
    try:
        config = await service.update_by_user_id(
            identity.internal_user_id,
            payload.model_dump(exclude_none=True),
            requested_fields=payload.model_fields_set,
        )
    except AccountStateBlockedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": error.code},
        ) from None
    except AgentConfigEnableManagedByActivationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "agent_enable_managed_by_go_live"},
        ) from None
    except AgentConfigNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent config not found",
        ) from exc
    except AgentConfigPhoneNumberNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Phone number not found",
        ) from exc
    except AgentConfigReadinessError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "agent_not_ready",
                "blockers": list(exc.blockers),
            },
        ) from None
    except AgentConfigTelephonySyncError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to update telephony state",
        ) from exc
    return AgentConfigResponse.model_validate(config, from_attributes=True)


@router.post(
    "/calls/{call_id}/transcript",
    response_model=TranscriptAppendAcknowledgement,
    openapi_extra=contract_request_openapi(TranscriptAppendRequest),
)
async def append_transcript(
    call_id: UUID,
    request: Request,
    identity: AuthenticatedAgentIdentity = Depends(require_agent_auth),
    session: AsyncSession = Depends(get_session),
) -> TranscriptAppendAcknowledgement:
    payload = await parse_contract_request(request, TranscriptAppendRequest)
    service = TranscriptService(session)
    try:
        result = await service.append(
            call_id=call_id,
            item=payload.segment,
            expected_user_id=identity.user_id,
            expected_agent_config_id=identity.agent_config_id,
        )
        await session.commit()
    except (
        TranscriptCallNotFoundError,
        TranscriptAuthorizationError,
        TranscriptSequenceConflictError,
        TranscriptCallNotAcceptingError,
    ) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
                if isinstance(exc, TranscriptCallNotFoundError)
                else status.HTTP_401_UNAUTHORIZED
                if isinstance(exc, TranscriptAuthorizationError)
                else status.HTTP_409_CONFLICT
            ),
            detail=exc.code,
        ) from None
    return create_contract(
        TranscriptAppendAcknowledgement,
        status=result.status,
        sequence_number=result.sequence_number,
    )


@router.post(
    "/calls/{call_id}/complete",
    response_model=CallCompletionAcknowledgement,
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra=contract_request_openapi(CallCompletionRequest),
)
async def complete_call(
    call_id: UUID,
    request: Request,
    identity: AuthenticatedAgentIdentity = Depends(require_agent_auth),
    session: AsyncSession = Depends(get_session),
) -> CallCompletionAcknowledgement:
    payload = await parse_contract_request(request, CallCompletionRequest)
    transcript_service = TranscriptService(session)
    try:
        recovery_results = await transcript_service.merge_recovery(
            call_id=call_id,
            transcript=payload.transcript,
            expected_user_id=identity.user_id,
            expected_agent_config_id=identity.agent_config_id,
        )
        ended_call = await CallLifecycleService(session).end_from_agent(
            call_id=call_id,
            duration_seconds=payload.duration_seconds,
        )
        if ended_call.status == "completed" and any(
            result.status == "stored" for result in recovery_results
        ):
            transcript_version = await MessageRepository(
                session
            ).max_sequence_by_call_id(call_id)
            await OutboxService(session).add(
                topic="summary.generate",
                aggregate_type="call-summary",
                aggregate_id=call_id,
                idempotency_key=(f"summary.generate:{call_id}:v{transcript_version}"),
                payload={"call_id": str(call_id)},
            )
        await session.commit()
    except (
        TranscriptCallNotFoundError,
        TranscriptAuthorizationError,
        TranscriptSequenceConflictError,
    ) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
                if isinstance(exc, TranscriptCallNotFoundError)
                else status.HTTP_401_UNAUTHORIZED
                if isinstance(exc, TranscriptAuthorizationError)
                else status.HTTP_409_CONFLICT
            ),
            detail=exc.code,
        ) from None
    except CallTransitionError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="call_not_accepting_completion",
        ) from None
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
                if str(exc) == "Call not found"
                else status.HTTP_409_CONFLICT
            ),
            detail=(
                "call_not_found"
                if str(exc) == "Call not found"
                else "call_not_accepting_completion"
            ),
        ) from None

    await _best_effort_outbox_wakeup(request)
    queue = get_call_finalization_queue(request)
    try:
        job_id = await queue.enqueue({"call_id": str(call_id)})
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Call finalization queue unavailable",
        ) from None
    return create_contract(
        CallCompletionAcknowledgement,
        status="accepted",
        queued=True,
        job_id=job_id,
    )
