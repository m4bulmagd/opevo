import time

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.providers.clerk import ClerkAuthProvider, extract_clerk_user_profile
from app.core.auth import get_auth_provider
from app.core.database import get_session
from app.core.observability import get_request_observability
from app.schemas.auth import ClerkWebhookEnvelope
from app.services.auth_service import AuthService


router = APIRouter(prefix="/webhooks", tags=["clerk"])


@router.post("/clerk", status_code=status.HTTP_202_ACCEPTED)
async def handle_clerk_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    auth_provider: ClerkAuthProvider = Depends(get_auth_provider),
) -> Response:
    started = time.monotonic()
    outcome = "rejected"
    telemetry = get_request_observability(request)
    try:
        payload_bytes = await request.body()
        event_id = auth_provider.verify_webhook(payload_bytes, dict(request.headers))
        envelope = ClerkWebhookEnvelope.model_validate_json(payload_bytes)
        outcome = "error"
        payload = envelope.model_dump()
        is_new = await AuthService(session).provision_user_from_event(
            profile=extract_clerk_user_profile(payload["data"]),
            provider="clerk",
            payload=envelope.model_dump(),
            event_id=event_id,
            event_type=envelope.type,
        )
        outcome = "duplicate" if is_new is False else "accepted"
        return Response(status_code=status.HTTP_202_ACCEPTED)
    finally:
        telemetry.record_webhook("clerk", outcome, time.monotonic() - started)
