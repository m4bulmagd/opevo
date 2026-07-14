import logging
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.core.database import get_session_factory
from app.core.logging import report_safe_exception
from app.core.redaction import safe_log_label
from app.providers.telephony.base import (
    TelephonyProviderError,
    TelephonyProvisioningPending,
    TelephonyProvisioningReviewRequired,
)
from app.repositories.notification_repository import NotificationRepository
from app.repositories.phone_number_provisioning_repository import (
    PhoneNumberProvisioningRepository,
)
from app.repositories.user_repository import UserRepository
from app.services.telephony_service import TelephonyService


logger = logging.getLogger(__name__)


def _safe_error_type(error: BaseException) -> str:
    return safe_log_label(type(error).__name__) or "Exception"


@asynccontextmanager
async def _provider_operation_lock(session_factory, operation_key: str | None):
    if operation_key is None:
        yield
        return

    async with session_factory() as lock_session:
        if lock_session.get_bind().dialect.name != "postgresql":
            yield
            return
        # A transaction-scoped advisory lock uses a dedicated connection and
        # survives an outbox lease reclaim without holding business-row locks.
        # PostgreSQL releases it automatically on rollback or connection loss.
        async with lock_session.begin():
            await lock_session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(CAST(:operation_key AS text), 0))"
                ),
                {"operation_key": operation_key},
            )
            yield


async def _run_provider_attempt(
    *,
    session,
    user_id: UUID,
    country_code: str,
    provider_operation_key: str | None,
    telephony_service: TelephonyService,
    provisioning_repo: PhoneNumberProvisioningRepository,
) -> None:
    review_failure: tuple[str, dict[str, Any], str] | None = None
    provider_failure: tuple[str, bool] | None = None
    unexpected_error_type: str | None = None
    try:
        service_kwargs = {"country_code": country_code}
        if provider_operation_key is not None:
            service_kwargs["operation_key"] = provider_operation_key
        phone_number = await telephony_service.provision_number(
            user_id,
            **service_kwargs,
        )
    except TelephonyProvisioningPending as exc:
        pending_reason = exc.reason
        try:
            await provisioning_repo.mark_failed(
                user_id=user_id,
                target_country_code=country_code,
                reason=pending_reason,
                payload={"event": "phone_number_provisioning_pending"},
                can_retry=False,
            )
            await session.commit()
        except Exception as persist_error:
            error_type = _safe_error_type(persist_error)
            report_safe_exception(
                logger,
                event="phone_provisioning_failure_handling_failed",
                operation="persist_phone_provisioning_pending",
                error_type=error_type,
                user_id=user_id,
                status="failed",
            )
            raise RuntimeError(
                "phone_provisioning_failure_handling_failed "
                f"error_type={error_type}"
            ) from None
        raise TelephonyProvisioningPending(reason=pending_reason) from None
    except TelephonyProvisioningReviewRequired as exc:
        review_failure = (exc.reason, dict(exc.payload), _safe_error_type(exc))
    except TelephonyProviderError as exc:
        provider_failure = (exc.category, exc.retryable)
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
                can_retry=reason == "no_affordable_number",
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
        if provider_failure is not None:
            error_type, can_retry = provider_failure
        else:
            assert unexpected_error_type is not None
            error_type = unexpected_error_type
            can_retry = True
        try:
            await provisioning_repo.mark_failed(
                user_id=user_id,
                target_country_code=country_code,
                reason=error_type,
                payload={"error_type": error_type},
                can_retry=can_retry,
            )
            report_safe_exception(
                logger,
                event="phone_provisioning_failed",
                operation="provision_phone_number",
                error_type=error_type,
                user_id=user_id,
                status="failed",
            )
            await session.commit()
        except Exception as exc:
            secondary_error_type = _safe_error_type(exc)
        if secondary_error_type is None:
            if provider_failure is not None:
                raise TelephonyProviderError(provider_failure[0]) from None
            raise RuntimeError(
                f"phone_provisioning_failed error_type={error_type}"
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
            logger.warning(
                "Phone provisioning unsupported for user %s country=%s",
                user_id,
                country_code,
            )
            return

        telephony_service = TelephonyService(
            session,
            provider=ctx.get("telephony_provider"),
        )
        provisioning = await provisioning_repo.mark_running(
            user_id=user_id,
            target_country_code=country_code,
            operation_key=operation_key,
        )
        provider_operation_key = provisioning.provider_operation_key
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

        async with _provider_operation_lock(
            session_factory,
            provider_operation_key,
        ):
            await _run_provider_attempt(
                session=session,
                user_id=user.id,
                country_code=country_code,
                provider_operation_key=provider_operation_key,
                telephony_service=telephony_service,
                provisioning_repo=provisioning_repo,
            )
