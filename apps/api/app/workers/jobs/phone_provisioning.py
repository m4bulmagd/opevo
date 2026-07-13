import logging
from typing import Any
from uuid import UUID

from app.core.database import get_session_factory
from app.core.logging import report_safe_exception
from app.providers.telephony.base import TelephonyProvisioningReviewRequired
from app.repositories.notification_repository import NotificationRepository
from app.repositories.phone_number_provisioning_repository import PhoneNumberProvisioningRepository
from app.repositories.user_repository import UserRepository
from app.services.telephony_service import TelephonyService


logger = logging.getLogger(__name__)


async def phone_provisioning_job(ctx: dict[str, Any], payload: dict[str, Any]) -> None:
    user_id_str = payload.get("user_id")
    if not user_id_str:
        logger.error("phone_provisioning_job: missing user_id in payload")
        return
        
    user_id = UUID(user_id_str)
    
    session_factory = ctx.get("session_factory") or get_session_factory()
    
    async with session_factory() as session:
        user_repo = UserRepository(session)
        provisioning_repo = PhoneNumberProvisioningRepository(session)
        user = await user_repo.get_by_id(user_id)
        if not user:
            logger.error(f"phone_provisioning_job: user {user_id} not found")
            return

        country_code = (user.country_code or "FR").upper()
        if country_code != "FR":
            await provisioning_repo.mark_failed(
                user_id=user_id,
                target_country_code=country_code,
                reason="unsupported_country",
                payload={"country_code": country_code},
                can_retry=False,
            )
            await session.commit()
            logger.warning(f"Phone provisioning unsupported for user {user_id} country={country_code}")
            return

        telephony_service = TelephonyService(session, provider=ctx.get("telephony_provider"))
        await provisioning_repo.mark_running(user_id=user_id, target_country_code=country_code)

        try:
            phone_number = await telephony_service.provision_number(user.id, country_code=country_code)
            await provisioning_repo.mark_succeeded(
                user_id=user_id,
                phone_number_id=phone_number.id,
                target_country_code=country_code,
            )
            await session.commit()
            logger.info(f"Successfully provisioned phone number for user {user_id}")
        except TelephonyProvisioningReviewRequired as exc:
            await provisioning_repo.mark_failed(
                user_id=user_id,
                target_country_code=country_code,
                reason=exc.reason,
                payload=exc.payload,
                can_retry=True,
            )
            report_safe_exception(
                logger,
                event="phone_provisioning_review_required",
                operation="provision_phone_number",
                error=exc,
                user_id=user_id,
                status="review_required",
                level=logging.WARNING,
            )
            notification_repo = NotificationRepository(session)
            await notification_repo.create(
                user_id=user_id,
                call_id=None,
                notification_type="phone_number_provisioning_review_required",
                status="pending",
                payload=exc.payload,
            )
            await session.commit()
        except Exception as exc:
            error_type = type(exc).__name__
            await provisioning_repo.mark_failed(
                user_id=user_id,
                target_country_code=country_code,
                reason=error_type,
                payload={"error_type": error_type},
                can_retry=True,
            )
            report_safe_exception(
                logger,
                event="phone_provisioning_failed",
                operation="provision_phone_number",
                error=exc,
                user_id=user_id,
                status="failed",
            )
            await session.commit()
            raise RuntimeError(
                f"phone_provisioning_failed error_type={error_type}"
            ) from None
