from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.domain import AuthenticatedUser
from app.core.auth import require_user_identity
from app.composition.runtime import get_api_runtime
from app.core.database import get_session
from app.core.dispatch_token import (
    DispatchTokenConfigurationError,
    dispatch_token_config,
)
from app.core.contract_http import contract_request_openapi, parse_contract_request
from app.core.verification_token import (
    VerificationTokenError,
    verify_verification_token,
)
from app.repositories.customer_activation_repository import (
    CustomerActivationRepository,
)
from app.schemas.activation import ActivationSnapshotResponse, CarrierLookupResponse
from app.schemas.business_profile import BusinessProfileDraft, BusinessProfileResponse
from app.services.activation_snapshot_service import (
    ActivationSnapshotService,
    ActivationSnapshotUnavailableError,
)
from app.services.account_access_policy import AccountStateBlockedError
from app.services.activation_go_live_service import (
    ActivationGoLiveBlockedError,
    ActivationGoLiveService,
)
from app.services.activation_provisioning_service import (
    ActivationProvisioningBlockedError,
    ActivationProvisioningService,
)
from app.services.business_profile_service import (
    BusinessProfileIncompleteError,
    BusinessProfileNotFoundError,
    BusinessProfileService,
)
from app.services.carrier_lookup_service import (
    CarrierLookupService,
    CarrierLookupUnavailableError,
)
from app.providers.carrier_lookup.factory import build_carrier_lookup_provider
from app.services.forwarding_verification_service import (
    ForwardingVerificationConflictError,
    ForwardingVerificationService,
)
from app.services.receptionist_projection_service import (
    ReceptionistProjectionTooLargeError,
)
from opevo_contracts import (
    VerificationCompletionAcknowledgement,
    VerificationCompletionRequest,
    create_contract,
)


router = APIRouter(tags=["activation"])


def get_activation_snapshot_service(
    session: AsyncSession = Depends(get_session),
) -> ActivationSnapshotService:
    return ActivationSnapshotService(session)


def get_activation_provisioning_service(
    session: AsyncSession = Depends(get_session),
) -> ActivationProvisioningService:
    return ActivationProvisioningService(session)


def get_activation_go_live_service(
    session: AsyncSession = Depends(get_session),
) -> ActivationGoLiveService:
    return ActivationGoLiveService(session)


def get_business_profile_service(
    session: AsyncSession = Depends(get_session),
) -> BusinessProfileService:
    return BusinessProfileService(session)


def get_carrier_lookup_service(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> CarrierLookupService:
    runtime = get_api_runtime(request.app)
    return CarrierLookupService(
        session,
        provider=build_carrier_lookup_provider(
            runtime.settings,
            observability=runtime.observability,
        ),
    )


def get_forwarding_verification_service(
    session: AsyncSession = Depends(get_session),
) -> ForwardingVerificationService:
    return ForwardingVerificationService(session)


@router.get("/api/activation", response_model=ActivationSnapshotResponse)
async def get_activation(
    identity: AuthenticatedUser = Depends(require_user_identity),
    service: ActivationSnapshotService = Depends(get_activation_snapshot_service),
) -> ActivationSnapshotResponse:
    return await _get_activation_snapshot(identity.internal_user_id, service)


@router.put("/api/business-profile", response_model=BusinessProfileResponse)
async def put_business_profile(
    payload: BusinessProfileDraft,
    identity: AuthenticatedUser = Depends(require_user_identity),
    service: BusinessProfileService = Depends(get_business_profile_service),
) -> BusinessProfileResponse:
    try:
        profile = await service.save_draft(identity.internal_user_id, payload)
    except AccountStateBlockedError as error:
        raise _account_state_blocked_error(error) from None
    except BusinessProfileNotFoundError:
        raise _profile_unavailable_error() from None
    except ReceptionistProjectionTooLargeError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "profile_projection_too_large"},
        ) from None
    return BusinessProfileResponse.model_validate(profile)


@router.post(
    "/api/activation/lookup-carrier",
    response_model=CarrierLookupResponse,
)
async def lookup_carrier(
    identity: AuthenticatedUser = Depends(require_user_identity),
    service: CarrierLookupService = Depends(get_carrier_lookup_service),
) -> CarrierLookupResponse:
    try:
        result = await service.lookup_for_user(identity.internal_user_id)
    except AccountStateBlockedError as error:
        raise _account_state_blocked_error(error) from None
    except CarrierLookupUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "carrier_lookup_unavailable",
                "manual_selection_allowed": True,
            },
        ) from None
    return CarrierLookupResponse.model_validate(result)


@router.post(
    "/api/activation/confirm-profile",
    response_model=ActivationSnapshotResponse,
)
async def confirm_profile(
    identity: AuthenticatedUser = Depends(require_user_identity),
    command_service: BusinessProfileService = Depends(get_business_profile_service),
    snapshot_service: ActivationSnapshotService = Depends(
        get_activation_snapshot_service
    ),
) -> ActivationSnapshotResponse:
    try:
        await command_service.confirm_profile(identity.internal_user_id)
    except AccountStateBlockedError as error:
        raise _account_state_blocked_error(error) from None
    except BusinessProfileIncompleteError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "profile_incomplete", "fields": list(error.fields)},
        ) from None
    except BusinessProfileNotFoundError:
        raise _profile_unavailable_error() from None
    return await _get_activation_snapshot(identity.internal_user_id, snapshot_service)


@router.post(
    "/api/activation/confirm-provisioning",
    response_model=ActivationSnapshotResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def confirm_provisioning(
    request: Request,
    identity: AuthenticatedUser = Depends(require_user_identity),
    service: ActivationProvisioningService = Depends(
        get_activation_provisioning_service
    ),
) -> ActivationSnapshotResponse:
    try:
        return await service.confirm(
            identity.internal_user_id,
            arq_pool=get_api_runtime(request.app).arq_pool,
        )
    except AccountStateBlockedError as error:
        raise _account_state_blocked_error(error) from None
    except ActivationProvisioningBlockedError as error:
        raise _provisioning_blocked_error(error.code) from None


@router.post(
    "/api/activation/retry-provisioning",
    response_model=ActivationSnapshotResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_provisioning(
    request: Request,
    identity: AuthenticatedUser = Depends(require_user_identity),
    service: ActivationProvisioningService = Depends(
        get_activation_provisioning_service
    ),
) -> ActivationSnapshotResponse:
    try:
        return await service.retry(
            identity.internal_user_id,
            arq_pool=get_api_runtime(request.app).arq_pool,
        )
    except AccountStateBlockedError as error:
        raise _account_state_blocked_error(error) from None
    except ActivationProvisioningBlockedError as error:
        raise _provisioning_blocked_error(error.code) from None


@router.post(
    "/api/activation/open-verification-window",
    response_model=ActivationSnapshotResponse,
)
async def open_verification_window(
    identity: AuthenticatedUser = Depends(require_user_identity),
    service: ForwardingVerificationService = Depends(
        get_forwarding_verification_service
    ),
    snapshot_service: ActivationSnapshotService = Depends(
        get_activation_snapshot_service
    ),
) -> ActivationSnapshotResponse:
    try:
        await service.open_window(identity.internal_user_id)
    except AccountStateBlockedError as error:
        raise _account_state_blocked_error(error) from None
    except ForwardingVerificationConflictError as error:
        raise _verification_conflict_error(error.code) from None
    return await _get_activation_snapshot(
        identity.internal_user_id,
        snapshot_service,
    )


@router.post(
    "/api/activation/go-live",
    response_model=ActivationSnapshotResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def go_live(
    request: Request,
    identity: AuthenticatedUser = Depends(require_user_identity),
    service: ActivationGoLiveService = Depends(get_activation_go_live_service),
) -> ActivationSnapshotResponse:
    try:
        return await service.go_live(
            identity.internal_user_id,
            arq_pool=get_api_runtime(request.app).arq_pool,
        )
    except AccountStateBlockedError as error:
        raise _account_state_blocked_error(error) from None
    except ActivationGoLiveBlockedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "go_live_blocked",
                "blockers": list(error.blockers),
            },
        ) from None


@router.post(
    "/api/activation/verification/{session_id}/complete",
    response_model=VerificationCompletionAcknowledgement,
    openapi_extra=contract_request_openapi(VerificationCompletionRequest),
)
async def complete_forwarding_verification(
    session_id: str,
    request: Request,
    x_verification_token: str | None = Header(
        default=None,
        alias="x-verification-token",
    ),
    session: AsyncSession = Depends(get_session),
    service: ForwardingVerificationService = Depends(
        get_forwarding_verification_service
    ),
) -> VerificationCompletionAcknowledgement:
    activation = await CustomerActivationRepository(
        session
    ).get_by_verification_session_id(session_id)
    if activation is None:
        raise _verification_auth_error()
    try:
        verify_verification_token(
            x_verification_token or "",
            expected_session_id=session_id,
            expected_user_id=str(activation.user_id),
            config=dispatch_token_config(get_api_runtime(request.app).settings),
        )
    except (VerificationTokenError, DispatchTokenConfigurationError):
        raise _verification_auth_error() from None

    await parse_contract_request(request, VerificationCompletionRequest)

    try:
        await service.complete(session_id=session_id)
    except ForwardingVerificationConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "verification_not_claimable"},
        ) from None
    return create_contract(
        VerificationCompletionAcknowledgement,
        status="verified",
        session_id=session_id,
    )


async def _get_activation_snapshot(
    user_id: UUID,
    service: ActivationSnapshotService,
) -> ActivationSnapshotResponse:
    try:
        return await service.get(user_id)
    except ActivationSnapshotUnavailableError:
        raise _profile_unavailable_error() from None


def _profile_unavailable_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "profile_unavailable"},
    )


def _account_state_blocked_error(error: AccountStateBlockedError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": error.code},
    )


def _provisioning_blocked_error(code: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code},
    )


def _verification_conflict_error(code: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code},
    )


def _verification_auth_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid verification token",
    )
