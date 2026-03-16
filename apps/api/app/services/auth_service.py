from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import extract_primary_email
from app.repositories.user_repository import UserRepository
from app.repositories.webhook_event_repository import WebhookEventRepository


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.user_repository = UserRepository(session)
        self.webhook_event_repository = WebhookEventRepository(session)

    async def sync_clerk_user(self, payload: dict, event_id: str, event_type: str) -> None:
        is_new_event = await self.webhook_event_repository.record_if_new(
            provider="clerk",
            external_event_id=event_id,
            event_type=event_type,
            payload=payload,
        )
        if not is_new_event:
            await self.session.commit()
            return

        user_data = payload["data"]
        clerk_user_id = user_data["id"]
        existing_user = await self.user_repository.get_by_clerk_user_id(clerk_user_id)
        if existing_user is None:
            await self.user_repository.create(
                clerk_user_id=clerk_user_id,
                email=extract_primary_email(user_data),
            )

        await self.session.commit()
