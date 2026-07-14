from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import limiter

from app.core.auth import UserIdentity, require_user_identity
from app.core.database import get_session
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
    BillingSessionProviderError,
    BillingSessionService,
    BillingSessionStateError,
)
from app.services.subscription_access_policy import SubscriptionAccessPolicy


router = APIRouter(prefix="/api/billing", tags=["billing"])


def get_billing_query_service(session: AsyncSession = Depends(get_session)) -> BillingQueryService:
    return BillingQueryService(session)


def get_billing_session_service() -> BillingSessionService:
    return BillingSessionService()


async def get_current_user(
    identity: UserIdentity = Depends(require_user_identity),
    session: AsyncSession = Depends(get_session),
):
    user = await UserRepository(session).get_by_clerk_user_id(identity.clerk_user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not synced")
    return user


@router.get("/subscription", response_model=SubscriptionResponse | None)
async def get_subscription(
    identity: UserIdentity = Depends(require_user_identity),
    service: BillingQueryService = Depends(get_billing_query_service),
) -> SubscriptionResponse | None:
    return await service.get_subscription(identity.internal_user_id)


@router.get("/usage", response_model=UsageSnapshotResponse)
async def get_usage(
    identity: UserIdentity = Depends(require_user_identity),
    service: BillingQueryService = Depends(get_billing_query_service),
) -> UsageSnapshotResponse:
    return await service.get_usage_snapshot(identity.internal_user_id)


@router.get("/usage-ledger", response_model=UsageLedgerListResponse)
async def get_usage_ledger(
    identity: UserIdentity = Depends(require_user_identity),
    service: BillingQueryService = Depends(get_billing_query_service),
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> UsageLedgerListResponse:
    return await service.get_usage_ledger(identity.internal_user_id, limit=limit)


@router.post("/checkout-session", response_model=HostedSessionResponse)
@limiter.limit("10/minute")
async def create_checkout_session(
    request: Request,
    payload: CheckoutSessionRequest,
    identity: UserIdentity = Depends(require_user_identity),
    service: BillingSessionService = Depends(get_billing_session_service),
    query_service: BillingQueryService = Depends(get_billing_query_service),
    user=Depends(get_current_user),
) -> HostedSessionResponse:
    existing_subscription = await query_service.get_subscription(identity.internal_user_id)
    subscription_status = (
        existing_subscription.status if existing_subscription is not None else None
    )
    if not SubscriptionAccessPolicy.can_start_checkout(subscription_status):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Subscription is not eligible for checkout",
        )

    customer_email = str(user.email)
    await query_service.end_business_transaction()
    try:
        session = await service.create_checkout_session(
            user_id=str(identity.internal_user_id),
            customer_email=customer_email,
            clerk_user_id=identity.clerk_user_id,
            plan_tier=payload.plan_tier,
        )
    except BillingSessionStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except BillingSessionProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Stripe checkout session",
        ) from exc

    return HostedSessionResponse(url=session.url)


@router.post("/portal-session", response_model=HostedSessionResponse)
@limiter.limit("10/minute")
async def create_portal_session(
    request: Request,
    payload: PortalSessionRequest,
    identity: UserIdentity = Depends(require_user_identity),
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
    except BillingSessionProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Stripe billing portal session",
        ) from exc

    return HostedSessionResponse(url=session.url)
