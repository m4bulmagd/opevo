from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.schemas.billing import StripeWebhookEnvelope
from app.services.billing_service import BillingService, UnsupportedStripeLifecycleError


router = APIRouter(prefix="/webhooks", tags=["stripe"])


@router.post("/stripe", status_code=status.HTTP_202_ACCEPTED)
async def handle_stripe_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    payload = await request.body()
    arq_pool = getattr(request.app.state, "arq_pool", None)
    billing_service = BillingService(
        session,
        arq_pool=arq_pool,
    )
    billing_service.verify_signature(payload, request.headers.get("stripe-signature"))
    envelope = StripeWebhookEnvelope.model_validate_json(payload)
    try:
        await billing_service.handle_event(envelope.model_dump())
    except UnsupportedStripeLifecycleError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported Stripe subscription data",
        ) from None
    return Response(status_code=status.HTTP_202_ACCEPTED)
