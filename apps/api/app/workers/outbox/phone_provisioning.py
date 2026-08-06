import logging
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from app.core.database import get_session_factory
from app.core.config import get_settings
from app.core.logging import report_safe_exception
from app.core.observability import get_observability
from app.core.provider_failures import ProviderFailure
from app.core.redaction import safe_log_label
from app.providers.telephony.base import (
    TelephonyProvisioningPending,
    TelephonyProvisioningReviewRequired,
)
from app.providers.telephony.factory import create_telephony_provider
from app.repositories.notification_repository import NotificationRepository
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.phone_number_provisioning_repository import (
    PhoneNumberProvisioningRepository,
)
from app.repositories.user_repository import UserRepository
from app.repositories.provider_cleanup_repository import ProviderCleanupRepository
from app.services.account_access_policy import (
    AccountLifecycleGenerationMismatchError,
    AccountStateBlockedError,
    require_current_account_lifecycle,
)
from app.services.outbox_service import OutboxService
from app.services.provider_work_policy import (
    UnresolvedProviderWorkError,
    unresolved_provider_work_blocker,
)
from app.services.telephony_service import TelephonyService
from app.workers.provider_single_flight import (
    ProviderSingleFlight,
    provider_single_flight,
)


logger = logging.getLogger(__name__)


def _safe_error_type(error: BaseException) -> str:
    return safe_log_label(type(error).__name__) or "Exception"


@asynccontextmanager
async def _provider_operation_lock(session_factory, operation_key: str | None):
    async with provider_single_flight(
        session_factory,
        (
            f"provider.phone-provision:{operation_key}"
            if operation_key is not None
            else None
        ),
    ) as guard:
        yield guard


async def _run_provider_attempt(
    *,
    session,
    user_id: UUID,
    country_code: str,
    provider_operation_key: str | None,
    telephony_service: TelephonyService,
    provisioning_repo: PhoneNumberProvisioningRepository,
    lifecycle_generation: int | None = None,
    provider_guard: ProviderSingleFlight | None = None,
) -> None:
    review_failure: tuple[str, dict[str, Any], str] | None = None
    provider_failure: ProviderFailure | None = None
    unexpected_error: Exception | None = None
    try:
        if provider_guard is not None:
            provider_guard.assert_transaction_free(session)
        service_kwargs = {"country_code": country_code}
        if provider_operation_key is not None:
            service_kwargs["operation_key"] = provider_operation_key
        acquire_number = getattr(telephony_service, "acquire_number", None)
        if acquire_number is None:
            phone_number = await telephony_service.provision_number(
                user_id,
                **service_kwargs,
            )
            acquired = None
        else:
            acquired = await acquire_number(**service_kwargs)
            phone_number = None
    except TelephonyProvisioningPending as exc:
        pending_reason = exc.reason
        try:
            await provisioning_repo.mark_pending(
                user_id=user_id,
                target_country_code=country_code,
                reason=pending_reason,
                payload={"event": "phone_number_provisioning_pending"},
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
                f"phone_provisioning_failure_handling_failed error_type={error_type}"
            ) from None
        raise TelephonyProvisioningPending(reason=pending_reason) from None
    except TelephonyProvisioningReviewRequired as exc:
        review_failure = (exc.reason, dict(exc.payload), _safe_error_type(exc))
    except ProviderFailure as exc:
        provider_failure = exc
    except Exception as exc:
        unexpected_error = exc
    else:
        if acquired is not None:
            current_user = await UserRepository(session).get_by_id_for_update(user_id)
            if current_user is None:
                await session.rollback()
                raise RuntimeError("phone_provisioning_owner_missing")
            try:
                require_current_account_lifecycle(
                    current_user,
                    lifecycle_generation=(
                        lifecycle_generation
                        if lifecycle_generation is not None
                        else current_user.lifecycle_generation
                    ),
                )
            except (
                AccountStateBlockedError,
                AccountLifecycleGenerationMismatchError,
            ) as lifecycle_error:
                cleanup = await ProviderCleanupRepository(session).adopt(
                    user_id=user_id,
                    lifecycle_generation=(
                        lifecycle_generation
                        if lifecycle_generation is not None
                        else current_user.lifecycle_generation
                    ),
                    resource_type="phone_number",
                    provider_resource_id=acquired.provider_number_id,
                )
                await OutboxService(session).add(
                    topic="provider.cleanup",
                    aggregate_type="provider-cleanup-operation",
                    aggregate_id=cleanup.id,
                    idempotency_key=f"provider.cleanup:{cleanup.id}",
                    payload={"cleanup_operation_id": str(cleanup.id)},
                )
                await session.commit()
                raise lifecycle_error
            cleanup_operations = await ProviderCleanupRepository(
                session
            ).list_incomplete_by_user_id_for_update(user_id)
            current_provisioning = await provisioning_repo.get_by_user_id_for_update(
                user_id
            )
            blocker = unresolved_provider_work_blocker(
                cleanup_operations=cleanup_operations,
                provisioning=current_provisioning,
                allowed_provisioning_operation_key=provider_operation_key,
                allow_matching_provisioning=True,
            )
            if blocker is not None:
                cleanup = await ProviderCleanupRepository(session).adopt(
                    user_id=user_id,
                    lifecycle_generation=current_user.lifecycle_generation,
                    resource_type="phone_number",
                    provider_resource_id=acquired.provider_number_id,
                )
                await OutboxService(session).add(
                    topic="provider.cleanup",
                    aggregate_type="provider-cleanup-operation",
                    aggregate_id=cleanup.id,
                    idempotency_key=f"provider.cleanup:{cleanup.id}",
                    payload={"cleanup_operation_id": str(cleanup.id)},
                )
                await session.commit()
                raise UnresolvedProviderWorkError(blocker)
            phone_repo = PhoneNumberRepository(session)
            phone_number = await phone_repo.get_by_user_id_for_update(user_id)
            if phone_number is None:
                phone_number = await phone_repo.create(
                    user_id=user_id,
                    e164=acquired.e164,
                    country_code=country_code,
                    provider_number_id=acquired.provider_number_id,
                    provider_connection_name=acquired.provider_connection_name,
                    is_active=acquired.provider_connection_name == "app-active",
                )
            elif phone_number.provider_number_id != acquired.provider_number_id:
                cleanup = await ProviderCleanupRepository(session).adopt(
                    user_id=user_id,
                    lifecycle_generation=current_user.lifecycle_generation,
                    resource_type="phone_number",
                    provider_resource_id=acquired.provider_number_id,
                )
                await OutboxService(session).add(
                    topic="provider.cleanup",
                    aggregate_type="provider-cleanup-operation",
                    aggregate_id=cleanup.id,
                    idempotency_key=f"provider.cleanup:{cleanup.id}",
                    payload={"cleanup_operation_id": str(cleanup.id)},
                )
                await session.commit()
                raise ProviderFailure(
                    provider="telnyx",
                    operation="provision_number",
                    disposition="terminal",
                    error_class="conflict",
                )
        assert phone_number is not None
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
            error_code = (
                "provider_retryable"
                if provider_failure.retryable
                else "provider_terminal"
            )
            error_type = provider_failure.error_class
            can_retry = provider_failure.retryable
        else:
            assert unexpected_error is not None
            error_code = "internal_defect"
            error_type = "internal_defect"
            can_retry = False
        try:
            await provisioning_repo.mark_failed(
                user_id=user_id,
                target_country_code=country_code,
                reason=error_code,
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
                raise provider_failure
            raise RuntimeError("phone_provisioning_internal_defect") from unexpected_error

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


async def provision_phone_number(
    ctx: dict[str, Any],
    payload: dict[str, Any],
    *,
    provider_operation_key: str | None = None,
) -> None:
    user_id_str = payload.get("user_id")
    if not user_id_str:
        logger.error("provision_phone_number: missing user_id in payload")
        return

    user_id = UUID(user_id_str)
    lifecycle_generation = payload.get("lifecycle_generation")
    if type(lifecycle_generation) is not int or lifecycle_generation < 1:
        raise ValueError("Invalid lifecycle generation")

    session_factory = ctx.get("session_factory") or get_session_factory()
    settings = get_settings()
    observability = get_observability()

    async with session_factory() as session:
        user_repo = UserRepository(session)
        provisioning_repo = PhoneNumberProvisioningRepository(session)
        cleanup_repo = ProviderCleanupRepository(session)
        user = await user_repo.get_by_id_for_update(user_id)
        if not user:
            logger.error(f"provision_phone_number: user {user_id} not found")
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

        provider = ctx.get("telephony_provider")
        if provider is None:
            provider = create_telephony_provider(
                settings,
                observability=observability,
            )
        telephony_service = TelephonyService(session, provider=provider)
        recovery_lifecycle_error: (
            AccountStateBlockedError | AccountLifecycleGenerationMismatchError | None
        ) = None
        try:
            require_current_account_lifecycle(
                user,
                lifecycle_generation=lifecycle_generation,
            )
        except (
            AccountStateBlockedError,
            AccountLifecycleGenerationMismatchError,
        ) as lifecycle_error:
            prior_attempt = await provisioning_repo.get_by_user_id_for_update(user_id)
            if (
                prior_attempt is None
                or prior_attempt.status != "running"
                or prior_attempt.provider_operation_key != provider_operation_key
                or provider_operation_key is None
            ):
                await session.rollback()
                raise
            recovery_lifecycle_error = lifecycle_error
            await session.rollback()
        else:
            cleanup_operations = (
                await cleanup_repo.list_incomplete_by_user_id_for_update(user_id)
            )
            prior_attempt = await provisioning_repo.get_by_user_id_for_update(user_id)
            blocker = unresolved_provider_work_blocker(
                cleanup_operations=cleanup_operations,
                provisioning=prior_attempt,
                allowed_provisioning_operation_key=provider_operation_key,
                allow_matching_provisioning=provider_operation_key is not None,
            )
            if blocker is not None:
                await session.rollback()
                raise UnresolvedProviderWorkError(blocker)
            provisioning = await provisioning_repo.mark_running(
                user_id=user_id,
                target_country_code=country_code,
                provider_operation_key=provider_operation_key,
            )
            provider_operation_key = provisioning.provider_operation_key
            start_persist_error_type: str | None = None
            try:
                # Release the provisioning-row write lock before provider I/O.
                # The stable operation key makes the provider call replayable.
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
        ) as provider_guard:
            if recovery_lifecycle_error is not None:
                if provider_guard is not None:
                    provider_guard.assert_transaction_free(session)
                assert provider_operation_key is not None
                recovered = await telephony_service.recover_acquired_number(
                    country_code=country_code,
                    operation_key=provider_operation_key,
                )
                if recovered is None:
                    raise ProviderFailure(
                        provider="telnyx",
                        operation="recover_provisioned_number",
                        disposition="retryable",
                        error_class="unavailable",
                    )
                current_user = await user_repo.get_by_id_for_update(user_id)
                if current_user is None:
                    await session.rollback()
                    raise RuntimeError("phone_provisioning_owner_missing")
                cleanup = await cleanup_repo.adopt(
                    user_id=user_id,
                    lifecycle_generation=lifecycle_generation,
                    resource_type="phone_number",
                    provider_resource_id=recovered.provider_number_id,
                )
                await OutboxService(session).add(
                    topic="provider.cleanup",
                    aggregate_type="provider-cleanup-operation",
                    aggregate_id=cleanup.id,
                    idempotency_key=f"provider.cleanup:{cleanup.id}",
                    payload={"cleanup_operation_id": str(cleanup.id)},
                )
                await session.commit()
                raise recovery_lifecycle_error
            current_user = await user_repo.get_by_id_for_update(user_id)
            if current_user is None:
                await session.rollback()
                return
            require_current_account_lifecycle(
                current_user,
                lifecycle_generation=lifecycle_generation,
            )
            cleanup_operations = (
                await cleanup_repo.list_incomplete_by_user_id_for_update(user_id)
            )
            current_provisioning = await provisioning_repo.get_by_user_id_for_update(
                user_id
            )
            blocker = unresolved_provider_work_blocker(
                cleanup_operations=cleanup_operations,
                provisioning=current_provisioning,
                allowed_provisioning_operation_key=provider_operation_key,
                allow_matching_provisioning=True,
            )
            if blocker is not None:
                await session.rollback()
                raise UnresolvedProviderWorkError(blocker)
            existing_number = await PhoneNumberRepository(
                session
            ).get_by_user_id_for_update(user_id)
            if existing_number is not None:
                await provisioning_repo.mark_succeeded(
                    user_id=user_id,
                    phone_number_id=existing_number.id,
                    target_country_code=country_code,
                )
                await session.commit()
                return
            await session.rollback()
            await _run_provider_attempt(
                session=session,
                user_id=user_id,
                country_code=country_code,
                provider_operation_key=provider_operation_key,
                telephony_service=telephony_service,
                provisioning_repo=provisioning_repo,
                lifecycle_generation=lifecycle_generation,
                provider_guard=provider_guard,
            )
