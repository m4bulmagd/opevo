from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook_event import WebhookEvent


WEBHOOK_EVENT_IDENTITY_CONSTRAINT = "uq_webhook_events_provider_external_event_id"


def _integrity_constraint_name(error: IntegrityError) -> str | None:
    original = error.orig
    for candidate in (original, getattr(original, "__cause__", None)):
        if candidate is None:
            continue
        diagnostic = getattr(candidate, "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        if constraint_name:
            return str(constraint_name)
        constraint_name = getattr(candidate, "constraint_name", None)
        if constraint_name:
            return str(constraint_name)
    return None


def _is_webhook_identity_conflict(error: IntegrityError) -> bool:
    constraint_name = _integrity_constraint_name(error)
    if constraint_name is not None:
        return constraint_name == WEBHOOK_EVENT_IDENTITY_CONSTRAINT

    return str(error.orig) == (
        "UNIQUE constraint failed: "
        "webhook_events.provider, webhook_events.external_event_id"
    )


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
        await self._ensure_sqlite_outer_transaction()
        event = WebhookEvent(
            provider=provider,
            external_event_id=external_event_id,
            event_type=event_type,
            payload=payload,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(event)
                await self.session.flush()
        except IntegrityError as error:
            if _is_webhook_identity_conflict(error):
                return False
            raise

        return True

    async def _ensure_sqlite_outer_transaction(self) -> None:
        bind = getattr(self.session, "bind", None)
        if bind is None or bind.dialect.name != "sqlite":
            return

        connection = await self.session.connection()
        raw_connection = await connection.get_raw_connection()
        driver_connection = raw_connection.driver_connection
        if driver_connection is not None and not driver_connection.in_transaction:
            await connection.exec_driver_sql("BEGIN")
