from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.user import User
from app.repositories.phone_number_provisioning_repository import (
    PhoneNumberProvisioningRepository,
    ProvisioningStateConflictError,
)


async def _user(db_session: AsyncSession, label: str) -> User:
    marker = f"{label}-{uuid4().hex}"
    user = User(
        external_user_id=f"provisioning-repository-{marker}",
        email=f"{marker}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _commit_and_reload(
    db_session: AsyncSession,
    user_id: UUID,
) -> PhoneNumberProvisioning:
    await db_session.commit()
    db_session.expunge_all()
    stored = await PhoneNumberProvisioningRepository(db_session).get_by_user_id(
        user_id
    )
    assert stored is not None
    return stored


@pytest.mark.anyio
async def test_mark_running_rejects_changed_stable_key_without_persisting_state(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "running-conflict")
    user_id = user.id
    db_session.add(
        PhoneNumberProvisioning(
            user_id=user_id,
            target_country_code="FR",
            status="queued",
            attempt_count=0,
            can_retry=False,
            last_error_reason="prior-state",
            last_error_payload={"source": "prior-state"},
            provider_operation_key="provider-operation-original",
        )
    )
    await db_session.commit()

    with pytest.raises(ProvisioningStateConflictError):
        await PhoneNumberProvisioningRepository(db_session).mark_running(
            user_id=user_id,
            target_country_code="BE",
            provider_operation_key="provider-operation-conflicting",
        )
    await db_session.rollback()
    db_session.expunge_all()

    stored = await PhoneNumberProvisioningRepository(db_session).get_by_user_id(
        user_id
    )
    assert stored is not None
    assert stored.target_country_code == "FR"
    assert stored.status == "queued"
    assert stored.attempt_count == 0
    assert stored.can_retry is False
    assert stored.last_error_reason == "prior-state"
    assert stored.last_error_payload == {"source": "prior-state"}
    assert stored.provider_operation_key == "provider-operation-original"


@pytest.mark.anyio
async def test_mark_succeeded_creates_and_persists_a_missing_provisioning_row(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "succeeded-fallback")
    phone_number = PhoneNumber(
        user_id=user.id,
        e164="+33123456789",
        country_code="FR",
        provider="telnyx",
        provider_number_id="provider-number-succeeded-fallback",
        is_active=True,
    )
    db_session.add(phone_number)
    await db_session.flush()

    created = await PhoneNumberProvisioningRepository(db_session).mark_succeeded(
        user_id=user.id,
        phone_number_id=phone_number.id,
        target_country_code="FR",
    )
    created_id = created.id
    stored = await _commit_and_reload(db_session, user.id)

    assert stored.id == created_id
    assert stored.target_country_code == "FR"
    assert stored.status == "succeeded"
    assert stored.attempt_count == 1
    assert stored.can_retry is False
    assert stored.phone_number_id == phone_number.id
    assert stored.last_error_reason is None
    assert stored.last_error_payload is None
    assert stored.provider_operation_key is None


@pytest.mark.anyio
async def test_mark_pending_creates_and_persists_a_missing_provisioning_row(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "pending-fallback")
    error_payload = {"provider_state": "pending"}

    created = await PhoneNumberProvisioningRepository(db_session).mark_pending(
        user_id=user.id,
        target_country_code="BE",
        reason="provider_pending",
        payload=error_payload,
    )
    created_id = created.id
    stored = await _commit_and_reload(db_session, user.id)

    assert stored.id == created_id
    assert stored.target_country_code == "BE"
    assert stored.status == "running"
    assert stored.attempt_count == 1
    assert stored.can_retry is False
    assert stored.phone_number_id is None
    assert stored.last_error_reason == "provider_pending"
    assert stored.last_error_payload == error_payload
    assert stored.provider_operation_key is None


@pytest.mark.anyio
async def test_mark_failed_creates_and_persists_a_missing_provisioning_row(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "failed-fallback")
    error_payload = {"error_class": "timeout"}

    created = await PhoneNumberProvisioningRepository(db_session).mark_failed(
        user_id=user.id,
        target_country_code="BE",
        reason="provider_timeout",
        payload=error_payload,
        can_retry=True,
    )
    created_id = created.id
    stored = await _commit_and_reload(db_session, user.id)

    assert stored.id == created_id
    assert stored.target_country_code == "BE"
    assert stored.status == "failed"
    assert stored.attempt_count == 1
    assert stored.can_retry is True
    assert stored.phone_number_id is None
    assert stored.last_error_reason == "provider_timeout"
    assert stored.last_error_payload == error_payload
    assert stored.provider_operation_key is None
