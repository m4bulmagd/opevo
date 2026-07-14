import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.observability import get_request_observability
from app.schemas.billing import StripeWebhookEnvelope
from app.services.billing_service import (
    BillingService,
    StripeLifecycleConflictError,
    UnsupportedStripeLifecycleError,
)


router = APIRouter(prefix="/webhooks", tags=["stripe"])


@router.post("/stripe", status_code=status.HTTP_202_ACCEPTED)
async def handle_stripe_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    started = time.monotonic()
    outcome = "rejected"
    telemetry = get_request_observability(request)
    try:
        payload = await request.body()
        arq_pool = getattr(request.app.state, "arq_pool", None)
        billing_service = BillingService(
            session,
            arq_pool=arq_pool,
        )
        billing_service.verify_signature(
            payload,
            request.headers.get("stripe-signature"),
        )
        envelope = StripeWebhookEnvelope.model_validate_json(payload)
        outcome = "error"
        try:
            is_new = await billing_service.handle_event(envelope.model_dump())
        except UnsupportedStripeLifecycleError:
            outcome = "rejected"
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported Stripe subscription data",
            ) from None
        except StripeLifecycleConflictError:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Stripe subscription state conflict",
            ) from None
        outcome = "duplicate" if is_new is False else "accepted"
        return Response(status_code=status.HTTP_202_ACCEPTED)
    finally:
        telemetry.record_webhook("stripe", outcome, time.monotonic() - started)
