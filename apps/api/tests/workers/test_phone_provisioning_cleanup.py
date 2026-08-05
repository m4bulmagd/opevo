from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.provider_cleanup_operation import ProviderCleanupOperation
from app.models.user import User
from app.services.account_access_policy import AccountStateBlockedError
from app.services.outbox_service import OutboxService
from app.services.provider_work_policy import UnresolvedProviderWorkError
from app.workers.outbox.delivery import (
    OUTBOX_RETRY_DELAYS,
    outbox_delivery_job,
)
from app.workers.outbox.failures import OutboxDeliveryError
from app.workers.jobs.outbox_topics import deliver_phone_provision
from app.workers.jobs.phone_provisioning import phone_provisioning_job
from app.workers.outbox.provider_cleanup import deliver_provider_cleanup


class LateProvisioningProvider:
    def __init__(self, session_factory, user_id: UUID) -> None:
        self.session_factory = session_factory
        self.user_id = user_id
        self.operation_keys: list[str | None] = []
        self.recovery_keys: list[str] = []

    async def provision_number(
        self,
        *,
        country_code: str,
        operation_key: str | None = None,
    ) -> dict:
        assert country_code == "FR"
        self.operation_keys.append(operation_key)
        async with self.session_factory() as session:
            user = await session.get(User, self.user_id)
            assert user is not None
            if user.status == "active":
                user.status = "inactive"
                user.lifecycle_generation += 1
                await session.commit()
        return {
            "e164": "+33123456789",
            "provider_number_id": "pn-late-acquired",
            "provider_connection_name": "app-disabled",
        }

    async def recover_provisioned_number(
        self,
        *,
        country_code: str,
        operation_key: str,
    ) -> dict | None:
        assert country_code == "FR"
        self.recovery_keys.append(operation_key)
        return {
            "e164": "+33123456789",
            "provider_number_id": "pn-late-acquired",
            "provider_connection_name": "app-disabled",
        }


class SimulatedWorkerCrash(BaseException):
    pass


class AcceptedThenRecoverableProvider:
    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        self.provision_keys: list[str | None] = []
        self.recovery_keys: list[str] = []
        self.disabled_ids: list[str] = []
        self.released_ids: list[str] = []
        self.accepted = {
            "e164": "+33123456788",
            "provider_number_id": f"pn-recovered-{user_id.hex}",
            "provider_connection_name": "app-disabled",
        }

    async def provision_number(
        self,
        *,
        country_code: str,
        operation_key: str | None = None,
    ) -> dict:
        assert country_code == "FR"
        self.provision_keys.append(operation_key)
        raise SimulatedWorkerCrash

    async def recover_provisioned_number(
        self,
        *,
        country_code: str,
        operation_key: str,
    ) -> dict | None:
        assert country_code == "FR"
        self.recovery_keys.append(operation_key)
        return self.accepted

    async def disable_number(self, *, provider_number_id: str) -> str:
        self.disabled_ids.append(provider_number_id)
        return "app-disabled"

    async def release_number(self, *, provider_number_id: str) -> None:
        self.released_ids.append(provider_number_id)


@pytest.mark.anyio
async def test_phone_provisioning_admission_waits_for_prior_provider_cleanup(
    db_session,
    active_user,
) -> None:
    active_user.country_code = "FR"
    db_session.add(
        ProviderCleanupOperation(
            user_id=active_user.id,
            lifecycle_generation=active_user.lifecycle_generation,
            resource_type="stripe_subscription",
            provider_resource_id="sub-prior-provider-work",
            status="pending",
        )
    )
    await db_session.commit()
    provider_calls: list[str] = []

    class ForbiddenProvider:
        async def provision_number(self, **_kwargs):
            provider_calls.append("provision")
            raise AssertionError("unresolved cleanup must block provider I/O")

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    with pytest.raises(UnresolvedProviderWorkError) as raised:
        await phone_provisioning_job(
            {
                "session_factory": session_factory,
                "telephony_provider": ForbiddenProvider(),
            },
            {
                "user_id": str(active_user.id),
                "lifecycle_generation": active_user.lifecycle_generation,
            },
            provider_operation_key="activation:phone.provision:new-lifecycle",
        )

    assert raised.value.code == "reactivation_not_ready"
    assert provider_calls == []
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(PhoneNumberProvisioning)
            .where(PhoneNumberProvisioning.user_id == active_user.id)
        )
        == 0
    )


@pytest.mark.anyio
async def test_late_provisioning_adopts_exact_identity_for_durable_cleanup(
    db_session,
    active_user,
) -> None:
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    user_id = active_user.id
    provider = LateProvisioningProvider(session_factory, user_id)

    with pytest.raises(AccountStateBlockedError):
        await phone_provisioning_job(
            {
                "session_factory": session_factory,
                "telephony_provider": provider,
            },
            {
                "user_id": str(user_id),
                "lifecycle_generation": 1,
            },
            provider_operation_key="activation:phone.provision:late-boundary",
        )

    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(PhoneNumber)
            .where(PhoneNumber.user_id == user_id)
        )
        == 0
    )
    cleanup = await db_session.scalar(
        select(ProviderCleanupOperation).where(
            ProviderCleanupOperation.user_id == user_id
        )
    )
    assert cleanup is not None
    assert cleanup.provider_resource_id == "pn-late-acquired"
    assert cleanup.resource_type == "phone_number"
    event = await db_session.scalar(
        select(OutboxEvent).where(OutboxEvent.aggregate_id == cleanup.id)
    )
    assert event is not None
    assert event.payload == {"cleanup_operation_id": str(cleanup.id)}


@pytest.mark.anyio
async def test_crash_before_cleanup_adoption_recovers_same_provider_order(
    db_session,
    active_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    user_id = active_user.id
    provider = LateProvisioningProvider(session_factory, user_id)
    original_add = OutboxService.add
    crash_once = True

    async def crash_after_add(self, **kwargs):
        nonlocal crash_once
        event = await original_add(self, **kwargs)
        if crash_once:
            crash_once = False
            raise RuntimeError("simulated crash before cleanup adoption commit")
        return event

    monkeypatch.setattr(OutboxService, "add", crash_after_add)
    payload = {
        "user_id": str(user_id),
        "lifecycle_generation": 1,
    }
    ctx = {
        "session_factory": session_factory,
        "telephony_provider": provider,
    }

    with pytest.raises(RuntimeError, match="simulated crash"):
        await phone_provisioning_job(
            ctx,
            payload,
            provider_operation_key="activation:phone.provision:crash-boundary",
        )
    assert (
        await db_session.scalar(
            select(func.count()).select_from(ProviderCleanupOperation)
        )
        == 0
    )

    with pytest.raises(AccountStateBlockedError):
        await phone_provisioning_job(
            ctx,
            payload,
            provider_operation_key="activation:phone.provision:crash-boundary",
        )

    assert provider.operation_keys == ["activation:phone.provision:crash-boundary"]
    assert provider.recovery_keys == ["activation:phone.provision:crash-boundary"]
    cleanup = await db_session.scalar(select(ProviderCleanupOperation))
    assert cleanup is not None
    assert cleanup.provider_resource_id == "pn-late-acquired"


@pytest.mark.anyio
async def test_delivery_wrapper_recovers_exact_crashed_order_after_deactivation(
    db_session,
    active_user,
) -> None:
    user_id = active_user.id
    active_user.country_code = "FR"
    operation_key = f"activation:phone.provision:wrapper-crash:{user_id}"
    db_session.add(
        PhoneNumberProvisioning(
            user_id=user_id,
            target_country_code="FR",
            status="queued",
            attempt_count=0,
            can_retry=False,
            provider_operation_key=operation_key,
        )
    )
    await db_session.commit()
    event = OutboxEvent(
        id=uuid4(),
        idempotency_key=operation_key,
        topic="phone.provision",
        aggregate_type="user",
        aggregate_id=user_id,
        payload={"user_id": str(user_id), "lifecycle_generation": 1},
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    provider = AcceptedThenRecoverableProvider(user_id)
    ctx = {
        "session_factory": session_factory,
        "telephony_provider": provider,
    }

    with pytest.raises(SimulatedWorkerCrash):
        await deliver_phone_provision(ctx, event)

    await db_session.refresh(active_user)
    active_user.status = "inactive"
    active_user.lifecycle_generation = 2
    await db_session.commit()

    await deliver_phone_provision(ctx, event)

    assert provider.provision_keys == [operation_key]
    assert provider.recovery_keys == [operation_key]
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(PhoneNumber)
            .where(PhoneNumber.user_id == user_id)
        )
        == 0
    )
    cleanup = await db_session.scalar(
        select(ProviderCleanupOperation).where(
            ProviderCleanupOperation.user_id == user_id
        )
    )
    assert cleanup is not None
    assert cleanup.provider_resource_id == provider.accepted["provider_number_id"]
    cleanup_event = await db_session.scalar(
        select(OutboxEvent).where(OutboxEvent.aggregate_id == cleanup.id)
    )
    assert cleanup_event is not None

    await deliver_provider_cleanup(ctx, cleanup_event)

    await db_session.refresh(cleanup)
    assert cleanup.status == "completed"
    assert cleanup.completed_at is not None
    assert provider.disabled_ids == [provider.accepted["provider_number_id"]]
    assert provider.released_ids == [provider.accepted["provider_number_id"]]
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(PhoneNumberProvisioning)
            .where(PhoneNumberProvisioning.user_id == user_id)
        )
        == 0
    )


@pytest.mark.anyio
async def test_missing_stale_order_lookup_retries_outbox_without_ordering_again(
    db_session,
    active_user,
) -> None:
    user_id = active_user.id
    active_user.country_code = "FR"
    active_user.status = "inactive"
    active_user.lifecycle_generation = 2
    operation_key = f"activation:phone.provision:lookup-pending:{user_id}"
    db_session.add(
        PhoneNumberProvisioning(
            user_id=user_id,
            target_country_code="FR",
            status="running",
            attempt_count=1,
            can_retry=False,
            provider_operation_key=operation_key,
        )
    )
    event = await OutboxService(db_session).add(
        topic="phone.provision",
        aggregate_type="user",
        aggregate_id=user_id,
        idempotency_key=operation_key,
        payload={"user_id": str(user_id), "lifecycle_generation": 1},
    )
    await db_session.commit()
    current_time = event.next_attempt_at + timedelta(seconds=1)
    provision_calls: list[str] = []
    recovery_keys: list[str] = []

    class LookupPendingProvider:
        async def provision_number(self, **_kwargs):
            provision_calls.append("provision")
            raise AssertionError("stale recovery must never create another order")

        async def recover_provisioned_number(
            self,
            *,
            country_code: str,
            operation_key: str,
        ) -> None:
            assert country_code == "FR"
            recovery_keys.append(operation_key)

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    ctx = {
        "session_factory": session_factory,
        "telephony_provider": LookupPendingProvider(),
        "outbox_now": lambda: current_time,
    }
    results: list[dict[str, int]] = []
    for _ in range(len(OUTBOX_RETRY_DELAYS) + 1):
        results.append(await outbox_delivery_job(ctx))
        async with session_factory() as session:
            stored_event = await session.get(OutboxEvent, event.id)
            assert stored_event is not None
            current_time = stored_event.next_attempt_at + timedelta(seconds=1)

    assert results[0] == {
        "claimed": 1,
        "delivered": 0,
        "retried": 1,
        "failed": 0,
    }
    assert results[:-1] == [
        {"claimed": 1, "delivered": 0, "retried": 1, "failed": 0}
    ] * len(OUTBOX_RETRY_DELAYS)
    assert results[-1] == {
        "claimed": 1,
        "delivered": 0,
        "retried": 0,
        "failed": 1,
    }
    stored_event = await db_session.get(OutboxEvent, event.id)
    assert stored_event is not None
    await db_session.refresh(stored_event)
    assert stored_event.status == "failed"
    assert stored_event.attempt_count == len(OUTBOX_RETRY_DELAYS) + 1
    assert stored_event.last_error_code == "provider_retryable"
    assert recovery_keys == [operation_key] * (len(OUTBOX_RETRY_DELAYS) + 1)
    assert provision_calls == []
    assert (
        await db_session.scalar(
            select(func.count()).select_from(ProviderCleanupOperation)
        )
        == 0
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "event_key_matches", "aggregate_matches"),
    [
        ("queued", True, True),
        ("failed", True, True),
        ("succeeded", True, True),
        ("running", False, True),
        ("running", True, False),
    ],
)
async def test_stale_delivery_only_admits_matching_running_attempt(
    db_session,
    active_user,
    status: str,
    event_key_matches: bool,
    aggregate_matches: bool,
) -> None:
    user_id = active_user.id
    active_user.country_code = "FR"
    active_user.status = "inactive"
    active_user.lifecycle_generation = 2
    operation_key = f"activation:phone.provision:narrow:{user_id}"
    db_session.add(
        PhoneNumberProvisioning(
            user_id=user_id,
            target_country_code="FR",
            status=status,
            attempt_count=1,
            can_retry=False,
            provider_operation_key=operation_key,
        )
    )
    await db_session.commit()
    event = OutboxEvent(
        id=uuid4(),
        idempotency_key=(
            operation_key if event_key_matches else f"{operation_key}:unrelated"
        ),
        topic="phone.provision",
        aggregate_type="user",
        aggregate_id=user_id if aggregate_matches else uuid4(),
        payload={"user_id": str(user_id), "lifecycle_generation": 1},
    )
    provider_calls: list[str] = []

    class ForbiddenProvider:
        async def provision_number(self, **_kwargs):
            provider_calls.append("provision")
            raise AssertionError("ineligible stale work must not order")

        async def recover_provisioned_number(self, **_kwargs):
            provider_calls.append("recover")
            raise AssertionError("ineligible stale work must not look up")

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    with pytest.raises(OutboxDeliveryError) as raised:
        await deliver_phone_provision(
            {
                "session_factory": session_factory,
                "telephony_provider": ForbiddenProvider(),
            },
            event,
        )

    assert raised.value.error_code == "dispatch_ineligible"
    assert raised.value.retryable is False
    assert provider_calls == []
