import logging

from arq.worker import Retry
from redis.exceptions import LockError

from app.core.database import get_session_factory
from app.providers.notifications.firebase import FirebaseNotificationProvider
from app.providers.storage.s3 import get_s3_storage
from app.providers.summaries.gemini import GeminiSummaryProvider
from app.repositories.call_repository import CallRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.notification_service import NotificationService
from app.services.recording_service import RecordingService
from app.services.summary_service import SummaryService
from app.services.telephony_service import TelephonyService
from app.services.usage_accounting_service import UsageAccountingService


logger = logging.getLogger(__name__)


def _build_lifecycle_service(session) -> CallLifecycleService:
    return CallLifecycleService(
        session,
        call_repository=CallRepository(session),
        message_repository=MessageRepository(session),
        usage_accounting_service=UsageAccountingService(session),
        phone_number_repository=PhoneNumberRepository(session),
        telephony_service=TelephonyService(session),
        summary_service=SummaryService(provider=GeminiSummaryProvider()),
        recording_service=RecordingService(provider=get_s3_storage()),
        notification_service=NotificationService(
            provider=FirebaseNotificationProvider(),
            notification_repository=NotificationRepository(session),
        ),
    )


async def call_finalization_job(ctx, payload: dict) -> dict:
    call_id = payload.get("call_id")
    redis = ctx["redis"]
    lock_key = f"ai_call:finalization:lock:{call_id}"

    try:
        async with redis.lock(lock_key, timeout=60, blocking_timeout=5):
            session_factory = get_session_factory()
            async with session_factory() as session:
                service = _build_lifecycle_service(session)
                result = await service.finalize_call(payload)
            return {
                "status": "skipped" if result.already_completed else "completed",
                "minutes_charged": result.minutes_charged,
                "summary_text": result.summary_text,
                "recording_key": result.recording_key,
                "number_disabled": result.number_disabled,
            }
    except LockError as exc:
        logger.warning(f"Could not acquire lock {lock_key}. Retrying...")
        raise Retry(defer=10) from exc
