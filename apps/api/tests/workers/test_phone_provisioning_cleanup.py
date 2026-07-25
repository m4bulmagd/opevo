from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.provider_cleanup_operation import ProviderCleanupOperation
from app.models.user import User
from app.services.account_access_policy import AccountStateBlockedError
from app.services.outbox_service import OutboxService
from app.workers.jobs.phone_provisioning import phone_provisioning_job


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
    assert await db_session.scalar(select(func.count()).select_from(ProviderCleanupOperation)) == 0

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
