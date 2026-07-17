from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUserIdentity, require_user_identity
from app.core.database import get_session
from app.schemas.activation import ActivationSnapshotResponse
from app.schemas.business_profile import BusinessProfileDraft, BusinessProfileResponse
from app.services.activation_snapshot_service import (
    ActivationSnapshotService,
    ActivationSnapshotUnavailableError,
)
from app.services.business_profile_service import (
    BusinessProfileIncompleteError,
    BusinessProfileNotFoundError,
    BusinessProfileService,
)
from app.services.receptionist_projection_service import (
    ReceptionistProjectionTooLargeError,
)


router = APIRouter(tags=["activation"])


def get_activation_snapshot_service(
    session: AsyncSession = Depends(get_session),
) -> ActivationSnapshotService:
    return ActivationSnapshotService(session)


def get_business_profile_service(
    session: AsyncSession = Depends(get_session),
) -> BusinessProfileService:
    return BusinessProfileService(session)


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
