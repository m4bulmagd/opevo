from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import limiter
from app.core.provider_failures import ProviderFailure

from app.auth.domain import AuthenticatedUser
from app.core.auth import require_user_identity
from app.core.database import get_session
from app.composition.runtime import get_api_runtime
from app.repositories.user_repository import UserRepository
from app.schemas.billing_api import (
    CheckoutSessionRequest,
    HostedSessionResponse,
    PortalSessionRequest,
    SubscriptionResponse,
    UsageLedgerListResponse,
    UsageSnapshotResponse,
)
from app.services.billing_query_service import BillingQueryService
from app.services.billing_session_service import (
    BillingPortalReturnUrlError,
    BillingSessionService,
    BillingSessionStateError,
)


router = APIRouter(prefix="/api/billing", tags=["billing"])


def get_billing_query_service(
    session: AsyncSession = Depends(get_session),
) -> BillingQueryService:
    return BillingQueryService(session)


def get_billing_session_service(request: Request) -> BillingSessionService:
    runtime = get_api_runtime(request.app)
    return BillingSessionService(
        settings=runtime.settings,
        observability=runtime.observability,
    )


async def get_current_user(
    identity: AuthenticatedUser = Depends(require_user_identity),
    session: AsyncSession = Depends(get_session),
):
    user = await UserRepository(session).get_by_id(identity.internal_user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not synced"
        )
    return user


@router.get("/subscription", response_model=SubscriptionResponse | None)
async def get_subscription(
    identity: AuthenticatedUser = Depends(require_user_identity),
    service: BillingQueryService = Depends(get_billing_query_service),
) -> SubscriptionResponse | None:
    return await service.get_subscription(identity.internal_user_id)


@router.get("/usage", response_model=UsageSnapshotResponse)
async def get_usage(
    identity: AuthenticatedUser = Depends(require_user_identity),
    service: BillingQueryService = Depends(get_billing_query_service),
) -> UsageSnapshotResponse:
    return await service.get_usage_snapshot(identity.internal_user_id)


@router.get("/usage-ledger", response_model=UsageLedgerListResponse)
async def get_usage_ledger(
    identity: AuthenticatedUser = Depends(require_user_identity),
    service: BillingQueryService = Depends(get_billing_query_service),
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> UsageLedgerListResponse:
    return await service.get_usage_ledger(identity.internal_user_id, limit=limit)


@router.post("/checkout-session", response_model=HostedSessionResponse)
@limiter.limit("10/minute")
async def create_checkout_session(
    request: Request,
    payload: CheckoutSessionRequest,
    identity: AuthenticatedUser = Depends(require_user_identity),
    service: BillingSessionService = Depends(get_billing_session_service),
    query_service: BillingQueryService = Depends(get_billing_query_service),
    user=Depends(get_current_user),
) -> HostedSessionResponse:
    preparation = await query_service.prepare_checkout_attempt(
        identity.internal_user_id
    )
    if not preparation.allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Subscription is not eligible for checkout",
        )

    customer_email = str(user.email)
    if (
        preparation.attempt_id is None
        or preparation.idempotency_key is None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Subscription is not eligible for checkout",
        )
    try:
        session = await service.create_checkout_session(
            user_id=str(identity.internal_user_id),
            customer_email=customer_email,
            customer_id=preparation.stripe_customer_id,
            plan_tier=payload.plan_tier,
            lifecycle_generation=preparation.lifecycle_generation,
            idempotency_key=preparation.idempotency_key,
            existing_session_id=preparation.existing_session_id,
        )
    except BillingSessionStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ProviderFailure as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Stripe checkout session",
        ) from exc

    if session.provider_session_id is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Stripe checkout session",
        )
    try:
        await query_service.complete_checkout_attempt(
            attempt_id=preparation.attempt_id,
            stripe_checkout_session_id=session.provider_session_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Checkout session identity conflict",
        ) from exc

    return HostedSessionResponse(url=session.url)


@router.post("/portal-session", response_model=HostedSessionResponse)
@limiter.limit("10/minute")
async def create_portal_session(
    request: Request,
    payload: PortalSessionRequest,
    identity: AuthenticatedUser = Depends(require_user_identity),
    service: BillingSessionService = Depends(get_billing_session_service),
    query_service: BillingQueryService = Depends(get_billing_query_service),
) -> HostedSessionResponse:
    subscription = await query_service.get_subscription(identity.internal_user_id)
    if subscription is None or not subscription.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stripe customer not found",
        )

    customer_id = subscription.stripe_customer_id
    await query_service.end_business_transaction()
    try:
        session = await service.create_portal_session(
            customer_id=customer_id,
            return_url=payload.return_url,
        )
    except BillingPortalReturnUrlError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid billing portal return URL",
        ) from exc
    except BillingSessionStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ProviderFailure as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Stripe billing portal session",
        ) from exc

    return HostedSessionResponse(url=session.url)
