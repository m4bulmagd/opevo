from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUserIdentity, require_user_identity
from app.core.database import get_session
from app.schemas.activation import ActivationSnapshotResponse, CarrierLookupResponse
from app.schemas.business_profile import BusinessProfileDraft, BusinessProfileResponse
from app.services.activation_snapshot_service import (
    ActivationSnapshotService,
    ActivationSnapshotUnavailableError,
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
from app.services.receptionist_projection_service import (
    ReceptionistProjectionTooLargeError,
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


def get_business_profile_service(
    session: AsyncSession = Depends(get_session),
) -> BusinessProfileService:
    return BusinessProfileService(session)


def get_carrier_lookup_service(
    session: AsyncSession = Depends(get_session),
) -> CarrierLookupService:
    return CarrierLookupService(session)


@router.get("/api/activation", response_model=ActivationSnapshotResponse)
async def get_activation(
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: ActivationSnapshotService = Depends(get_activation_snapshot_service),
) -> ActivationSnapshotResponse:
    return await _get_activation_snapshot(identity.internal_user_id, service)


@router.put("/api/business-profile", response_model=BusinessProfileResponse)
async def put_business_profile(
    payload: BusinessProfileDraft,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: BusinessProfileService = Depends(get_business_profile_service),
) -> BusinessProfileResponse:
    try:
        profile = await service.save_draft(identity.internal_user_id, payload)
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
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: CarrierLookupService = Depends(get_carrier_lookup_service),
) -> CarrierLookupResponse:
    try:
        result = await service.lookup_for_user(identity.internal_user_id)
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
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    command_service: BusinessProfileService = Depends(get_business_profile_service),
    snapshot_service: ActivationSnapshotService = Depends(
        get_activation_snapshot_service
    ),
) -> ActivationSnapshotResponse:
    try:
        await command_service.confirm_profile(identity.internal_user_id)
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
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: ActivationProvisioningService = Depends(
        get_activation_provisioning_service
    ),
) -> ActivationSnapshotResponse:
    try:
        return await service.confirm(
            identity.internal_user_id,
            arq_pool=getattr(request.app.state, "arq_pool", None),
        )
    except ActivationProvisioningBlockedError as error:
        raise _provisioning_blocked_error(error.code) from None


@router.post(
    "/api/activation/retry-provisioning",
    response_model=ActivationSnapshotResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_provisioning(
    request: Request,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: ActivationProvisioningService = Depends(
        get_activation_provisioning_service
    ),
) -> ActivationSnapshotResponse:
    try:
        return await service.retry(
            identity.internal_user_id,
            arq_pool=getattr(request.app.state, "arq_pool", None),
        )
    except ActivationProvisioningBlockedError as error:
        raise _provisioning_blocked_error(error.code) from None


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


def _provisioning_blocked_error(code: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code},
    )
