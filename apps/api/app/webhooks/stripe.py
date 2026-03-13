from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.providers.telephony.base import TelephonyProvider
from app.providers.telephony.telnyx import get_telephony_provider
from app.schemas.billing import StripeWebhookEnvelope
from app.services.billing_service import BillingService
from app.services.telephony_service import TelephonyService


router = APIRouter(prefix="/webhooks", tags=["stripe"])


@router.post("/stripe", status_code=status.HTTP_202_ACCEPTED)
async def handle_stripe_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    telephony_provider: TelephonyProvider = Depends(get_telephony_provider),
) -> Response:
    payload = await request.body()
    billing_service = BillingService(
        session,
        telephony_service=TelephonyService(session, provider=telephony_provider),
    )
    billing_service.verify_signature(payload, request.headers.get("stripe-signature"))
    envelope = StripeWebhookEnvelope.model_validate_json(payload)
    await billing_service.handle_event(envelope.model_dump())
    return Response(status_code=status.HTTP_202_ACCEPTED)
