from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import extract_primary_email
from app.repositories.webhook_event_repository import WebhookEventRepository
from app.services.user_bootstrap_service import UserBootstrapService


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.webhook_event_repository = WebhookEventRepository(session)
        self.user_bootstrap_service = UserBootstrapService(session)

    async def sync_clerk_user(
        self,
        payload: dict,
        event_id: str,
        event_type: str,
    ) -> bool:
        is_new_event = await self.webhook_event_repository.record_if_new(
            provider="clerk",
            external_event_id=event_id,
            event_type=event_type,
            payload=payload,
        )
        if is_new_event:
            user_data = payload["data"]
            await self.user_bootstrap_service.ensure_user(
                external_user_id=user_data["id"],
                email=extract_primary_email(user_data),
            )

        await self.session.commit()
        return is_new_event
