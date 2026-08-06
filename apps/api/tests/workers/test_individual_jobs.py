"""Unit tests for individual ARQ worker jobs."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace
import traceback
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.notification import Notification
from app.models.phone_number import PhoneNumber
from app.providers.telephony.fake import FakeTelephonyProvider
from app.workers.outbox.phone_provisioning import (
    provision_phone_number as _provision_phone_number_explicit,
)


async def _provision_phone_number(
    dependencies: dict,
    payload: dict,
    *,
    provider_operation_key: str | None = None,
) -> None:
    await _provision_phone_number_explicit(
        payload,
        session_factory=dependencies["session_factory"],
        telephony_provider=dependencies.get(
            "telephony_provider", FakeTelephonyProvider()
        ),
        provider_operation_key=provider_operation_key,
    )
# ===========================================================================
# provision_phone_number tests
# ===========================================================================


class CapturingProvisioningProvider:
    def __init__(self) -> None:
        self.country_codes: list[str] = []
        self.operation_keys: list[str | None] = []

    async def provision_number(
        self,
        *,
        country_code: str,
        operation_key: str | None = None,
    ) -> dict:
        self.country_codes.append(country_code)
        self.operation_keys.append(operation_key)
        return {
            "e164": "+33123456789",
            "provider_number_id": "pn_123",
            "provider_connection_name": "app-active",
        }

    async def enable_number(self, *, provider_number_id: str) -> str:
        return "app-active"

    async def disable_number(self, *, provider_number_id: str) -> str:
        return "app-disabled"


class ReviewRequiredProvisioningProvider:
    async def provision_number(self, *, country_code: str) -> dict:
        from app.providers.telephony.base import TelephonyProvisioningReviewRequired

        raise TelephonyProvisioningReviewRequired(
            reason="no_affordable_number",
            payload={
                "event": "phone_number_provisioning_review_required",
                "country_code": country_code,
                "contact_support": True,
            },
        )

    async def enable_number(self, *, provider_number_id: str) -> str:
        return "app-active"

    async def disable_number(self, *, provider_number_id: str) -> str:
        return "app-disabled"


class FakePhoneProvisioningSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    def in_transaction(self) -> bool:
        return False


class FakePhoneProvisioningSessionContext:
    def __init__(self, session: FakePhoneProvisioningSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakePhoneProvisioningSession:
        return self.session

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class CapturingPhoneProvisioningRepository:
    def __init__(self) -> None:
        self.failed_calls: list[dict] = []
        self.current = None

    async def get_by_user_id_for_update(self, _user_id):
        return self.current

    async def mark_running(self, **kwargs):
        self.current = SimpleNamespace(
            status="running",
            provider_operation_key=kwargs.get("provider_operation_key"),
        )
        return self.current

    async def mark_failed(self, **kwargs) -> None:
        self.failed_calls.append(kwargs)


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["missing_row", "missing_key"])
async def test_phone_provision_outbox_missing_provider_identity_is_terminal_before_job(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    from app.workers.outbox import phone as phone_outbox
    from app.workers.outbox.failures import OutboxDeliveryError

    user_id = uuid4()
    job_called = False

    class Session:
        async def commit(self) -> None:
            return None

    class SessionContext:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

    class Users:
        def __init__(self, _session) -> None:
            pass

        async def get_by_id_for_update(self, requested_user_id):
            assert requested_user_id == user_id
            return SimpleNamespace(status="active", lifecycle_generation=1)

    class Provisionings:
        def __init__(self, _session) -> None:
            pass

        async def get_by_user_id_for_update(self, requested_user_id):
            assert requested_user_id == user_id
            if case == "missing_row":
                return None
            return SimpleNamespace(provider_operation_key=None)

    async def capture(*_args, **_kwargs) -> None:
        nonlocal job_called
        job_called = True

    monkeypatch.setattr(phone_outbox, "UserRepository", Users)
    monkeypatch.setattr(phone_outbox, "PhoneNumberProvisioningRepository", Provisionings)
    monkeypatch.setattr(phone_outbox, "provision_phone_number", capture)
    event = SimpleNamespace(
        payload={
            "user_id": str(user_id),
            "lifecycle_generation": 1,
        },
        idempotency_key=f"activation:phone.provision:{uuid4()}",
    )

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await phone_outbox.deliver_phone_provision(
            event,
            session_factory=SessionContext,
            telephony_provider=object(),
            activation_flow_enabled=False,
            now=lambda: datetime.now(UTC),
        )

    assert exc_info.value.error_code == "provider_terminal"
    assert exc_info.value.retryable is False
    assert job_called is False


@pytest.mark.anyio
async def test_phone_provision_outbox_uses_durable_provider_key_not_delivery_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers.outbox import phone as phone_outbox
    from app.workers.outbox.failures import OutboxDeliveryError

    user_id = uuid4()
    provider_operation_key = f"activation:phone.provision:{uuid4()}"
    delivery_key = f"{provider_operation_key}:attempt:2"
    captured: list[tuple[dict, str]] = []

    class Session:
        async def commit(self) -> None:
            return None

    class SessionContext:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

    class Users:
        def __init__(self, _session) -> None:
            pass

        async def get_by_id_for_update(self, requested_user_id):
            assert requested_user_id == user_id
            return SimpleNamespace(status="active", lifecycle_generation=1)

    class Provisionings:
        def __init__(self, _session) -> None:
            pass

        async def get_by_user_id_for_update(self, requested_user_id):
            return await self.get_by_user_id(requested_user_id)

        async def get_by_user_id(self, requested_user_id):
            assert requested_user_id == user_id
            return SimpleNamespace(
                provider_operation_key=provider_operation_key,
                can_retry=False,
                last_error_reason=None,
            )

    class Phones:
        def __init__(self, _session) -> None:
            pass

        async def get_by_user_id(self, requested_user_id):
            assert requested_user_id == user_id
            return None

    async def capture(
        payload,
        *,
        session_factory,
        telephony_provider,
        provider_operation_key,
    ):
        del session_factory, telephony_provider
        captured.append((payload, provider_operation_key))

    monkeypatch.setattr(phone_outbox, "UserRepository", Users)
    monkeypatch.setattr(phone_outbox, "PhoneNumberProvisioningRepository", Provisionings)
    monkeypatch.setattr(phone_outbox, "PhoneNumberRepository", Phones)
    monkeypatch.setattr(phone_outbox, "provision_phone_number", capture)
    event = SimpleNamespace(
        payload={
            "user_id": str(user_id),
            "lifecycle_generation": 1,
        },
        idempotency_key=delivery_key,
    )

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await phone_outbox.deliver_phone_provision(
            event,
            session_factory=SessionContext,
            telephony_provider=object(),
            activation_flow_enabled=False,
            now=lambda: datetime.now(UTC),
        )

    assert exc_info.value.error_code == "provider_terminal"
    assert captured == [
        (
            {
                "user_id": str(user_id),
                "lifecycle_generation": 1,
            },
            provider_operation_key,
        )
    ]


class CapturingPhoneProvisioningNotificationRepository:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs) -> None:
        self.calls.append(kwargs)


def install_provision_phone_number_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    error: Exception,
) -> tuple[
    FakePhoneProvisioningSession,
    CapturingPhoneProvisioningRepository,
    CapturingPhoneProvisioningNotificationRepository,
]:
    from app.workers.outbox import phone_provisioning as phone_provisioning_module

    session = FakePhoneProvisioningSession()
    provisioning_repository = CapturingPhoneProvisioningRepository()
    notification_repository = CapturingPhoneProvisioningNotificationRepository()

    class FakeUserRepository:
        def __init__(self, _session) -> None:
            pass

        async def get_by_id(self, user_id: UUID):
            return SimpleNamespace(
                id=user_id,
                country_code="FR",
                status="active",
                lifecycle_generation=1,
            )

        async def get_by_id_for_update(self, user_id: UUID):
            return await self.get_by_id(user_id)

    class FailingTelephonyService:
        def __init__(self, _session, *, provider=None) -> None:
            pass

        async def provision_number(self, user_id: UUID, *, country_code: str):
            raise error

    class NoPhoneNumberRepository:
        def __init__(self, _session) -> None:
            pass

        async def get_by_user_id_for_update(self, _user_id: UUID):
            return None

    class NoProviderCleanupRepository:
        def __init__(self, _session) -> None:
            pass

        async def list_incomplete_by_user_id_for_update(self, _user_id: UUID):
            return []

    monkeypatch.setattr(phone_provisioning_module, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(
        phone_provisioning_module,
        "PhoneNumberRepository",
        NoPhoneNumberRepository,
    )
    monkeypatch.setattr(
        phone_provisioning_module,
        "PhoneNumberProvisioningRepository",
        lambda _session: provisioning_repository,
    )
    monkeypatch.setattr(
        phone_provisioning_module,
        "ProviderCleanupRepository",
        NoProviderCleanupRepository,
    )
    monkeypatch.setattr(phone_provisioning_module, "TelephonyService", FailingTelephonyService)
    monkeypatch.setattr(
        phone_provisioning_module,
        "NotificationRepository",
        lambda _session: notification_repository,
    )

    return session, provisioning_repository, notification_repository


@pytest.mark.anyio
async def test_provision_phone_number_persists_successful_state_and_forces_fr_default(
    db_session, active_user
) -> None:
    from app.models.phone_number_provisioning import PhoneNumberProvisioning

    active_user.country_code = None
    await db_session.commit()

    provider = CapturingProvisioningProvider()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await _provision_phone_number(
        {
            "telephony_provider": provider,
            "session_factory": session_factory,
        },
        {
            "user_id": str(active_user.id),
            "lifecycle_generation": active_user.lifecycle_generation,
        },
        provider_operation_key="activation:phone.provision:evt_123",
    )

    provisionings = (
        await db_session.execute(
            select(PhoneNumberProvisioning).where(PhoneNumberProvisioning.user_id == active_user.id)
        )
    ).scalars().all()
    phone_numbers = (
        await db_session.execute(select(PhoneNumber).where(PhoneNumber.user_id == active_user.id))
    ).scalars().all()

    assert provider.country_codes == ["FR"]
    assert provider.operation_keys == ["activation:phone.provision:evt_123"]
    assert len(phone_numbers) == 1
    assert len(provisionings) == 1
    assert provisionings[0].status == "succeeded"
    assert provisionings[0].attempt_count == 1
    assert provisionings[0].can_retry is False
    assert provisionings[0].phone_number_id == phone_numbers[0].id
    assert (
        provisionings[0].provider_operation_key
        == "activation:phone.provision:evt_123"
    )


@pytest.mark.anyio
async def test_provision_phone_number_defaults_to_local_factory_without_credentials(
    db_session,
    active_user,
) -> None:
    from app.models.phone_number_provisioning import PhoneNumberProvisioning

    user_id = active_user.id
    active_user.country_code = "FR"
    await db_session.commit()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    operation_key = "activation:phone.provision:local-default"

    await _provision_phone_number(
        {"session_factory": session_factory},
        {
            "user_id": str(user_id),
            "lifecycle_generation": active_user.lifecycle_generation,
        },
        provider_operation_key=operation_key,
    )

    db_session.expire_all()
    provisioning = await db_session.scalar(
        select(PhoneNumberProvisioning).where(
            PhoneNumberProvisioning.user_id == user_id
        )
    )
    phone_number = await db_session.scalar(
        select(PhoneNumber).where(PhoneNumber.user_id == user_id)
    )
    assert provisioning is not None
    assert provisioning.status == "succeeded"
    assert provisioning.provider_operation_key == operation_key
    assert phone_number is not None
    assert phone_number.e164.startswith("+339")
    assert phone_number.provider_number_id.startswith("fake-")
    assert phone_number.provider_connection_name == "app-disabled"
    assert phone_number.is_active is False


@pytest.mark.anyio
async def test_phone_provisioning_reuses_first_provider_key_across_customer_retry(
    db_session, active_user
) -> None:
    from app.models.phone_number_provisioning import PhoneNumberProvisioning
    from app.providers.telephony.base import TelephonyProvisioningReviewRequired

    class RetryThenSucceedProvider(CapturingProvisioningProvider):
        async def provision_number(
            self,
            *,
            country_code: str,
            operation_key: str | None = None,
        ) -> dict:
            self.country_codes.append(country_code)
            self.operation_keys.append(operation_key)
            if len(self.operation_keys) == 1:
                raise TelephonyProvisioningReviewRequired(
                    reason="no_affordable_number",
                    payload={
                        "event": "phone_number_provisioning_review_required",
                        "country_code": country_code,
                        "contact_support": True,
                    },
                )
            return {
                "e164": "+33123456789",
                "provider_number_id": "pn_123",
                "provider_connection_name": "app-disabled",
            }

    provider = RetryThenSucceedProvider()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await _provision_phone_number(
        {
            "telephony_provider": provider,
            "session_factory": session_factory,
        },
        {
            "user_id": str(active_user.id),
            "lifecycle_generation": active_user.lifecycle_generation,
        },
        provider_operation_key="activation:phone.provision:stable",
    )
    await _provision_phone_number(
        {
            "telephony_provider": provider,
            "session_factory": session_factory,
        },
        {
            "user_id": str(active_user.id),
            "lifecycle_generation": active_user.lifecycle_generation,
        },
        provider_operation_key="activation:phone.provision:stable",
    )

    provisioning = await db_session.scalar(
        select(PhoneNumberProvisioning).where(
            PhoneNumberProvisioning.user_id == active_user.id
        )
    )
    assert provisioning is not None
    assert provider.operation_keys == [
        "activation:phone.provision:stable",
        "activation:phone.provision:stable",
    ]
    assert (
        provisioning.provider_operation_key
        == "activation:phone.provision:stable"
    )
    assert provisioning.status == "succeeded"


@pytest.mark.anyio
async def test_phone_provider_pending_attempt_uses_refreshable_running_state() -> None:
    from app.providers.telephony.base import TelephonyProvisioningPending
    from app.workers.outbox.phone_provisioning import _run_provider_attempt

    class Session:
        commits = 0

        async def commit(self) -> None:
            self.commits += 1

    class Provisionings:
        pending_calls: list[dict] = []

        async def mark_pending(self, **kwargs) -> None:
            self.pending_calls.append(kwargs)

    class Telephony:
        async def provision_number(self, _user_id, **_kwargs):
            raise TelephonyProvisioningPending(reason="existing_order_pending")

    session = Session()
    provisionings = Provisionings()
    user_id = uuid4()

    with pytest.raises(TelephonyProvisioningPending):
        await _run_provider_attempt(
            session=session,
            user_id=user_id,
            country_code="FR",
            provider_operation_key="activation:phone.provision:pending-unit",
            telephony_service=Telephony(),
            provisioning_repo=provisionings,
        )

    assert provisionings.pending_calls == [
        {
            "user_id": user_id,
            "target_country_code": "FR",
            "reason": "existing_order_pending",
            "payload": {"event": "phone_number_provisioning_pending"},
        }
    ]
    assert session.commits == 1


@pytest.mark.anyio
async def test_phone_provisioning_pending_order_keeps_customer_retry_disabled(
    db_session, active_user
) -> None:
    from app.models.phone_number_provisioning import PhoneNumberProvisioning
    from app.providers.telephony.base import TelephonyProvisioningPending

    class PendingProvider(CapturingProvisioningProvider):
        async def provision_number(
            self,
            *,
            country_code: str,
            operation_key: str | None = None,
        ) -> dict:
            self.operation_keys.append(operation_key)
            raise TelephonyProvisioningPending(reason="existing_order_pending")

    provider = PendingProvider()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(TelephonyProvisioningPending):
        await _provision_phone_number(
            {
                "telephony_provider": provider,
                "session_factory": session_factory,
            },
            {
                "user_id": str(active_user.id),
                "lifecycle_generation": active_user.lifecycle_generation,
            },
            provider_operation_key="activation:phone.provision:pending",
        )

    provisioning = await db_session.scalar(
        select(PhoneNumberProvisioning).where(
            PhoneNumberProvisioning.user_id == active_user.id
        )
    )
    assert provisioning is not None
    assert provisioning.status == "running"
    assert provisioning.can_retry is False
    assert provisioning.last_error_reason == "existing_order_pending"
    assert (
        provisioning.provider_operation_key
        == "activation:phone.provision:pending"
    )


@pytest.mark.anyio
async def test_provision_phone_number_persists_retryable_failure_state(
    db_session, active_user
) -> None:
    from app.models.phone_number_provisioning import PhoneNumberProvisioning

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await _provision_phone_number(
        {
            "telephony_provider": ReviewRequiredProvisioningProvider(),
            "session_factory": session_factory,
        },
        {
            "user_id": str(active_user.id),
            "lifecycle_generation": active_user.lifecycle_generation,
        },
    )

    provisionings = (
        await db_session.execute(
            select(PhoneNumberProvisioning).where(PhoneNumberProvisioning.user_id == active_user.id)
        )
    ).scalars().all()
    notifications = (
        await db_session.execute(
            select(Notification).where(Notification.user_id == active_user.id)
        )
    ).scalars().all()
    phone_numbers = (
        await db_session.execute(select(PhoneNumber).where(PhoneNumber.user_id == active_user.id))
    ).scalars().all()

    assert len(provisionings) == 1
    assert provisionings[0].status == "failed"
    assert provisionings[0].attempt_count == 1
    assert provisionings[0].can_retry is True
    assert provisionings[0].last_error_reason == "no_affordable_number"
    assert not phone_numbers
    assert notifications[0].notification_type == "phone_number_provisioning_review_required"


@pytest.mark.anyio
async def test_phone_provisioning_review_failure_does_not_log_exception_message(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    from app.providers.telephony.base import TelephonyProvisioningReviewRequired

    error = TelephonyProvisioningReviewRequired(
        reason="provider_review_required",
        payload={"event": "phone_number_provisioning_review_required"},
    )
    error.args = ("AUTHORIZATION_SENTINEL_FROM_REVIEW_EXCEPTION",)
    session, provisioning_repository, notification_repository = (
        install_provision_phone_number_fakes(monkeypatch, error=error)
    )

    with caplog.at_level(logging.WARNING):
        await _provision_phone_number(
            {"session_factory": lambda: FakePhoneProvisioningSessionContext(session)},
            {
                "user_id": "00000000-0000-0000-0000-000000000123",
                "lifecycle_generation": 1,
            },
        )

    assert "AUTHORIZATION_SENTINEL_FROM_REVIEW_EXCEPTION" not in caplog.text
    assert "event=phone_provisioning_review_required" in caplog.text
    assert "operation=provision_phone_number" in caplog.text
    assert "error_type=TelephonyProvisioningReviewRequired" in caplog.text
    assert provisioning_repository.failed_calls[0]["reason"] == "provider_review_required"
    assert notification_repository.calls
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.anyio
async def test_phone_provisioning_unexpected_failure_does_not_log_or_persist_exception_message(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:

    error_message = (
        "PHONE_SENTINEL_+33612345678 "
        "AUTHORIZATION_SENTINEL_FROM_PROVISIONING_PROVIDER"
    )
    session, provisioning_repository, _notification_repository = (
        install_provision_phone_number_fakes(
            monkeypatch,
            error=RuntimeError(error_message),
        )
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError) as exc_info:
            await _provision_phone_number(
                {"session_factory": lambda: FakePhoneProvisioningSessionContext(session)},
                {
                    "user_id": "00000000-0000-0000-0000-000000000123",
                    "lifecycle_generation": 1,
                },
            )

    assert error_message not in str(exc_info.value)
    assert error_message not in caplog.text
    assert "+33612345678" not in caplog.text
    assert "event=phone_provisioning_failed" in caplog.text
    assert "operation=provision_phone_number" in caplog.text
    assert "error_type=internal_defect" in caplog.text
    assert provisioning_repository.failed_calls[0]["reason"] == "internal_defect"
    assert provisioning_repository.failed_calls[0]["payload"] == {
        "error_type": "internal_defect",
    }
    assert provisioning_repository.failed_calls[0]["can_retry"] is False
    assert exc_info.value.__cause__ is not None
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("category", "can_retry"),
    [
        ("provider_retryable", True),
        ("provider_terminal", False),
    ],
)
async def test_phone_provisioning_preserves_safe_provider_category(
    monkeypatch: pytest.MonkeyPatch,
    category: str,
    can_retry: bool,
) -> None:
    from app.core.provider_failures import ProviderFailure

    session, provisioning_repository, _notification_repository = (
        install_provision_phone_number_fakes(
            monkeypatch,
            error=ProviderFailure(
                provider="telnyx",
                operation="provision_number",
                disposition=("retryable" if category == "provider_retryable" else "terminal"),
                error_class="unavailable",
            ),
        )
    )

    with pytest.raises(ProviderFailure) as exc_info:
        await _provision_phone_number(
            {"session_factory": lambda: FakePhoneProvisioningSessionContext(session)},
            {
                "user_id": "00000000-0000-0000-0000-000000000123",
                "lifecycle_generation": 1,
            },
        )

    assert exc_info.value.retryable is can_retry
    assert provisioning_repository.failed_calls[0]["reason"] == category
    assert provisioning_repository.failed_calls[0]["payload"] == {
        "error_type": "unavailable",
    }
    assert provisioning_repository.failed_calls[0]["can_retry"] is can_retry


@pytest.mark.anyio
async def test_phone_provisioning_sanitizes_sensitive_exception_class_name(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:

    sensitive_type_sentinel = "ProviderAuthorizationTokenSentinelError"
    sensitive_error_type = type(sensitive_type_sentinel, (RuntimeError,), {})
    session, provisioning_repository, _notification_repository = (
        install_provision_phone_number_fakes(
            monkeypatch,
            error=sensitive_error_type("provider failure"),
        )
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError) as exc_info:
            await _provision_phone_number(
                {"session_factory": lambda: FakePhoneProvisioningSessionContext(session)},
                {
                    "user_id": "00000000-0000-0000-0000-000000000123",
                    "lifecycle_generation": 1,
                },
            )

    assert sensitive_type_sentinel not in str(exc_info.value)
    assert sensitive_type_sentinel not in caplog.text
    assert provisioning_repository.failed_calls[0]["reason"] == "internal_defect"
    assert provisioning_repository.failed_calls[0]["payload"] == {
        "error_type": "internal_defect",
    }


def assert_exception_state_is_sanitized(
    error: BaseException,
    *sentinels: str,
) -> None:
    rendered_traceback = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    for sentinel in sentinels:
        assert sentinel not in str(error)
        assert sentinel not in rendered_traceback
    assert error.__context__ is None
    assert error.__cause__ is None


@pytest.mark.anyio
async def test_phone_provisioning_mark_failed_error_does_not_chain_provider_secrets(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:

    provider_sentinel = "PROVIDER_AUTHORIZATION_SENTINEL_FROM_MARK_FAILED_PATH"
    persistence_sentinel = "PERSISTENCE_TOKEN_SENTINEL_FROM_MARK_FAILED"
    session, provisioning_repository, _notification_repository = (
        install_provision_phone_number_fakes(
            monkeypatch,
            error=RuntimeError(provider_sentinel),
        )
    )

    async def fail_mark_failed(**_kwargs) -> None:
        raise RuntimeError(persistence_sentinel)

    monkeypatch.setattr(provisioning_repository, "mark_failed", fail_mark_failed)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError) as exc_info:
            await _provision_phone_number(
                {"session_factory": lambda: FakePhoneProvisioningSessionContext(session)},
                {
                    "user_id": "00000000-0000-0000-0000-000000000123",
                    "lifecycle_generation": 1,
                },
            )

    assert_exception_state_is_sanitized(
        exc_info.value,
        provider_sentinel,
        persistence_sentinel,
    )
    assert provider_sentinel not in caplog.text
    assert persistence_sentinel not in caplog.text


@pytest.mark.anyio
async def test_phone_provisioning_commit_error_does_not_chain_provider_secrets(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:

    provider_sentinel = "PROVIDER_AUTHORIZATION_SENTINEL_FROM_COMMIT_PATH"
    persistence_sentinel = "PERSISTENCE_TOKEN_SENTINEL_FROM_COMMIT"
    session, _provisioning_repository, _notification_repository = (
        install_provision_phone_number_fakes(
            monkeypatch,
            error=RuntimeError(provider_sentinel),
        )
    )

    async def fail_commit() -> None:
        raise RuntimeError(persistence_sentinel)

    monkeypatch.setattr(session, "commit", fail_commit)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError) as exc_info:
            await _provision_phone_number(
                {"session_factory": lambda: FakePhoneProvisioningSessionContext(session)},
                {
                    "user_id": "00000000-0000-0000-0000-000000000123",
                    "lifecycle_generation": 1,
                },
            )

    assert_exception_state_is_sanitized(
        exc_info.value,
        provider_sentinel,
        persistence_sentinel,
    )
    assert provider_sentinel not in caplog.text
    assert persistence_sentinel not in caplog.text
