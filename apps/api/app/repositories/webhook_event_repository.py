from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook_event import WebhookEvent


class WebhookEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_if_new(
        self,
        *,
        provider: str,
        external_event_id: str,
        event_type: str,
        payload: dict,
    ) -> bool:
        existing = await self.session.execute(
            select(WebhookEvent).where(
                WebhookEvent.provider == provider,
                WebhookEvent.external_event_id == external_event_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return False

        event = WebhookEvent(
            provider=provider,
            external_event_id=external_event_id,
            event_type=event_type,
            payload=payload,
        )
        self.session.add(event)
        await self.session.flush()
        return True
