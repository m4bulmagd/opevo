import logging
from typing import Any
from uuid import UUID

from app.core.database import get_session_factory
from app.core.logging import report_safe_exception
from app.core.redaction import safe_log_label
from app.providers.telephony.base import TelephonyProvisioningReviewRequired
from app.repositories.notification_repository import NotificationRepository
from app.repositories.phone_number_provisioning_repository import PhoneNumberProvisioningRepository
from app.repositories.user_repository import UserRepository
from app.services.telephony_service import TelephonyService


logger = logging.getLogger(__name__)


def _safe_error_type(error: BaseException) -> str:
    return safe_log_label(type(error).__name__) or "Exception"


async def phone_provisioning_job(
    ctx: dict[str, Any],
    payload: dict[str, Any],
    *,
    operation_key: str | None = None,
) -> None:
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
        start_persist_error_type: str | None = None
        try:
            # Release the provisioning-row write lock before provider I/O. The
            # stable outbox operation key makes the provider call replayable.
            await session.commit()
        except Exception as exc:
            start_persist_error_type = _safe_error_type(exc)
        if start_persist_error_type is not None:
            report_safe_exception(
                logger,
                event="phone_provisioning_start_persist_failed",
                operation="persist_phone_provisioning_start",
                error_type=start_persist_error_type,
                user_id=user_id,
                status="failed",
            )
            raise RuntimeError(
                "phone_provisioning_start_persist_failed "
                f"error_type={start_persist_error_type}"
            ) from None

        review_failure: tuple[str, dict[str, Any], str] | None = None
        unexpected_error_type: str | None = None
        try:
            service_kwargs = {"country_code": country_code}
            if operation_key is not None:
                service_kwargs["operation_key"] = operation_key
            phone_number = await telephony_service.provision_number(
                user.id,
                **service_kwargs,
            )
        except TelephonyProvisioningReviewRequired as exc:
            review_failure = (exc.reason, dict(exc.payload), _safe_error_type(exc))
        except Exception as exc:
            unexpected_error_type = _safe_error_type(exc)
        else:
            await provisioning_repo.mark_succeeded(
                user_id=user_id,
                phone_number_id=phone_number.id,
                target_country_code=country_code,
            )
            await session.commit()
            logger.info(f"Successfully provisioned phone number for user {user_id}")
            return

        secondary_error_type: str | None = None
        if review_failure is not None:
            reason, review_payload, review_error_type = review_failure
            try:
                await provisioning_repo.mark_failed(
                    user_id=user_id,
                    target_country_code=country_code,
                    reason=reason,
                    payload=review_payload,
                    can_retry=True,
                )
                report_safe_exception(
                    logger,
                    event="phone_provisioning_review_required",
                    operation="provision_phone_number",
                    error_type=review_error_type,
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
                    payload=review_payload,
                )
                await session.commit()
            except Exception as exc:
                secondary_error_type = _safe_error_type(exc)
            if secondary_error_type is None:
                return
        else:
            assert unexpected_error_type is not None
            try:
                await provisioning_repo.mark_failed(
                    user_id=user_id,
                    target_country_code=country_code,
                    reason=unexpected_error_type,
                    payload={"error_type": unexpected_error_type},
                    can_retry=True,
                )
                report_safe_exception(
                    logger,
                    event="phone_provisioning_failed",
                    operation="provision_phone_number",
                    error_type=unexpected_error_type,
                    user_id=user_id,
                    status="failed",
                )
                await session.commit()
            except Exception as exc:
                secondary_error_type = _safe_error_type(exc)
            if secondary_error_type is None:
                raise RuntimeError(
                    f"phone_provisioning_failed error_type={unexpected_error_type}"
                ) from None

        report_safe_exception(
            logger,
            event="phone_provisioning_failure_handling_failed",
            operation="persist_phone_provisioning_failure",
            error_type=secondary_error_type,
            user_id=user_id,
            status="failed",
        )
        raise RuntimeError(
            f"phone_provisioning_failure_handling_failed error_type={secondary_error_type}"
        ) from None
