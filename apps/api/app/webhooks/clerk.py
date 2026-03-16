from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import ClerkAuthProvider, get_auth_provider
from app.core.database import get_session
from app.schemas.auth import ClerkWebhookEnvelope
from app.services.auth_service import AuthService


router = APIRouter(prefix="/webhooks", tags=["clerk"])


@router.post("/clerk", status_code=status.HTTP_202_ACCEPTED)
async def handle_clerk_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    auth_provider: ClerkAuthProvider = Depends(get_auth_provider),
) -> Response:
    payload_bytes = await request.body()
    event_id = auth_provider.verify_webhook(payload_bytes, dict(request.headers))
    envelope = ClerkWebhookEnvelope.model_validate_json(payload_bytes)

    await AuthService(session).sync_clerk_user(
        payload=envelope.model_dump(),
        event_id=event_id,
        event_type=envelope.type,
    )

    return Response(status_code=status.HTTP_202_ACCEPTED)
