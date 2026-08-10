from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.domain import ExternalUserProfile
from app.repositories.webhook_event_repository import WebhookEventRepository
from app.services.user_provisioning import UserProvisioning


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.webhook_event_repository = WebhookEventRepository(session)
        self.user_provisioning = UserProvisioning(session)

    async def provision_user_from_event(
        self,
        *,
        profile: ExternalUserProfile,
        provider: str,
        payload: dict,
        event_id: str,
        event_type: str,
    ) -> bool:
        is_new_event = await self.webhook_event_repository.record_if_new(
            provider=provider,
            external_event_id=event_id,
            event_type=event_type,
            payload=payload,
        )
        if is_new_event:
            await self.user_provisioning.ensure_user(profile)

        await self.session.commit()
        return is_new_event
