from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from app.models.activation_event import ActivationEvent
from app.models.business_profile import BusinessProfile
from app.models.customer_activation import CustomerActivation
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.schemas.business_profile import WEEKDAYS
from app.services.activation_provisioning_service import (
    ActivationProvisioningBlockedError,
    ActivationProvisioningService,
)


def _complete_business_hours() -> dict[str, dict[str, object]]:
    return {
        day: {
            "closed": day in {"saturday", "sunday"},
            "intervals": (
                []
                if day in {"saturday", "sunday"}
                else [{"start": "09:00", "end": "18:00"}]
            ),
        }
        for day in WEEKDAYS
    }


async def _seed_eligible_customer(db_session, user) -> CustomerActivation:
    user.country_code = "FR"
    profile = BusinessProfile(
        user_id=user.id,
        owner_name="Camille Martin",
        business_name="Atelier Martin",
        business_type="Plomberie",
        public_description="Dépannage et installation de plomberie.",
        timezone="Europe/Paris",
        business_hours=_complete_business_hours(),
        existing_phone_e164="+33612345678",
        confirmed_carrier="orange",
        receptionist_name="Léa",
        content_revision=3,
        routing_revision=2,
    )
    activation = CustomerActivation(
        user_id=user.id,
        profile_confirmed_revision=3,
        profile_confirmed_at=datetime(2026, 7, 18, 8, 0, tzinfo=UTC),
    )
    db_session.add_all(
        [
            profile,
            activation,
            Subscription(
                user_id=user.id,
                stripe_customer_id=f"cus_{uuid4().hex}",
                stripe_subscription_id=f"sub_{uuid4().hex}",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                current_period_start=datetime(2026, 7, 1, tzinfo=UTC),
                current_period_end=datetime(2026, 8, 1, tzinfo=UTC),
            ),
            UsageLedger(
                user_id=user.id,
                event_type="subscription_activated",
                source_id=f"in_{uuid4().hex}",
                minutes_delta=60,
                balance_after=60,
            ),
        ]
    )
    await db_session.commit()
    return activation


@pytest.mark.anyio
async def test_confirm_records_one_consent_and_one_outbox_across_duplicate_calls(
    db_session,
    active_user,
) -> None:
    activation = await _seed_eligible_customer(db_session, active_user)
    activation_id = activation.id
    service = ActivationProvisioningService(db_session)

    first = await service.confirm(active_user.id, arq_pool=None)
    first_consent_at = first.activation.provisioning_consented_at
    second = await service.confirm(active_user.id, arq_pool=None)

    provisioning = await db_session.scalar(
        select(PhoneNumberProvisioning).where(
            PhoneNumberProvisioning.user_id == active_user.id
        )
    )
    assert provisioning is not None
    operation_key = f"activation:phone.provision:{activation_id}"
    assert provisioning.status == "queued"
    assert provisioning.attempt_count == 0
    assert provisioning.provider_operation_key == operation_key
    assert first_consent_at is not None
    assert second.activation.provisioning_consented_at == first_consent_at
    assert first.number.provisioning_status == "queued"
    assert second.number.provisioning_status == "queued"
    assert await db_session.scalar(
        select(func.count()).select_from(PhoneNumberProvisioning)
    ) == 1
    assert await db_session.scalar(
        select(func.count())
        .select_from(OutboxEvent)
        .where(OutboxEvent.topic == "phone.provision")
    ) == 1
    assert await db_session.scalar(
        select(func.count())
        .select_from(ActivationEvent)
        .where(ActivationEvent.event_type == "provisioning_consented")
    ) == 1
    outbox = await db_session.scalar(select(OutboxEvent))
    event = await db_session.scalar(select(ActivationEvent))
    assert outbox is not None
    assert event is not None
    assert outbox.idempotency_key == operation_key
    assert outbox.payload == {"user_id": str(active_user.id)}
    assert event.idempotency_key == f"activation-event:{operation_key}"
    assert event.event_metadata == {"country_code": "FR"}


class _FailingArqPool:
    async def enqueue_job(self, _name: str, _payload: dict) -> None:
        raise ConnectionError("redis unavailable")


@pytest.mark.anyio
async def test_confirm_redis_wakeup_failure_keeps_committed_canonical_snapshot(
    db_session,
    active_user,
) -> None:
    await _seed_eligible_customer(db_session, active_user)

    result = await ActivationProvisioningService(db_session).confirm(
        active_user.id,
        arq_pool=_FailingArqPool(),
    )

    assert result.activation.provisioning_consented_at is not None
    assert result.number.provisioning_status == "queued"
    assert await db_session.scalar(
        select(func.count()).select_from(OutboxEvent)
    ) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("profile_unconfirmed", "profile_not_confirmed"),
        ("profile_stale", "profile_confirmation_stale"),
        ("profile_incomplete", "profile_incomplete"),
        ("user_inactive", "user_inactive"),
        ("non_fr", "unsupported_country"),
        ("subscription_missing", "subscription_missing"),
        ("subscription_inactive", "subscription_status_ineligible"),
        ("subscription_expired", "subscription_period_inactive"),
        ("minutes_exhausted", "minutes_exhausted"),
        ("phone_exists", "phone_already_assigned"),
    ],
)
async def test_confirm_rejects_ineligible_state_with_stable_blocker_code(
    db_session,
    active_user,
    case: str,
    expected_code: str,
) -> None:
    await _seed_eligible_customer(db_session, active_user)
    activation = await db_session.scalar(
        select(CustomerActivation).where(CustomerActivation.user_id == active_user.id)
    )
    profile = await db_session.scalar(
        select(BusinessProfile).where(BusinessProfile.user_id == active_user.id)
    )
    subscription = await db_session.scalar(
        select(Subscription).where(Subscription.user_id == active_user.id)
    )
    assert activation is not None
    assert profile is not None
    assert subscription is not None

    if case == "profile_unconfirmed":
        activation.profile_confirmed_at = None
        activation.profile_confirmed_revision = None
    elif case == "profile_stale":
        activation.profile_confirmed_revision = profile.content_revision - 1
    elif case == "profile_incomplete":
        profile.business_name = None
    elif case == "user_inactive":
        active_user.status = "disabled"
    elif case == "non_fr":
        active_user.country_code = "IE"
    elif case == "subscription_missing":
        await db_session.delete(subscription)
    elif case == "subscription_inactive":
        subscription.status = "past_due"
    elif case == "subscription_expired":
        subscription.current_period_end = datetime(2026, 7, 17, tzinfo=UTC)
    elif case == "minutes_exhausted":
        ledger = await db_session.scalar(
            select(UsageLedger).where(UsageLedger.user_id == active_user.id)
        )
        assert ledger is not None
        ledger.minutes_delta = 0
        ledger.balance_after = 0
    elif case == "phone_exists":
        db_session.add(
            PhoneNumber(
                user_id=active_user.id,
                e164="+33123456789",
                country_code="FR",
                provider="telnyx",
                provider_number_id="pn_existing",
                provider_connection_name="app-disabled",
                is_active=False,
            )
        )
    await db_session.commit()

    with pytest.raises(ActivationProvisioningBlockedError) as exc_info:
        await ActivationProvisioningService(db_session).confirm(
            active_user.id,
            arq_pool=None,
        )

    assert exc_info.value.code == expected_code
    assert await db_session.scalar(
        select(func.count()).select_from(PhoneNumberProvisioning)
    ) == 0
    assert await db_session.scalar(
        select(func.count()).select_from(OutboxEvent)
    ) == 0


@pytest.mark.anyio
async def test_confirm_rejects_unsupported_plan_with_stable_blocker_code(
    db_session,
    active_user,
) -> None:
    await _seed_eligible_customer(db_session, active_user)
    await db_session.execute(text("PRAGMA ignore_check_constraints = ON"))
    subscription = await db_session.scalar(
        select(Subscription).where(Subscription.user_id == active_user.id)
    )
    assert subscription is not None
    subscription.plan_tier = "professional"
    await db_session.commit()

    with pytest.raises(ActivationProvisioningBlockedError) as exc_info:
        await ActivationProvisioningService(db_session).confirm(
            active_user.id,
            arq_pool=None,
        )

    assert exc_info.value.code == "plan_unsupported"


@pytest.mark.anyio
async def test_retry_queues_new_delivery_identity_but_keeps_provider_identity(
    db_session,
    active_user,
) -> None:
    activation = await _seed_eligible_customer(db_session, active_user)
    service = ActivationProvisioningService(db_session)
    await service.confirm(active_user.id, arq_pool=None)
    provisioning = await db_session.scalar(
        select(PhoneNumberProvisioning).where(
            PhoneNumberProvisioning.user_id == active_user.id
        )
    )
    initial_outbox = await db_session.scalar(select(OutboxEvent))
    assert provisioning is not None
    assert initial_outbox is not None
    provider_operation_key = provisioning.provider_operation_key
    initial_outbox.status = "delivered"
    initial_outbox.delivered_at = datetime.now(UTC)
    provisioning.status = "failed"
    provisioning.attempt_count = 1
    provisioning.can_retry = True
    provisioning.last_error_reason = "provider_retryable"
    provisioning.last_error_payload = {"error_type": "provider_retryable"}
    await db_session.commit()

    result = await service.retry(active_user.id, arq_pool=None)

    await db_session.refresh(provisioning)
    outbox_events = list(
        (
            await db_session.execute(select(OutboxEvent))
        ).scalars()
    )
    assert result.number.provisioning_status == "queued"
    assert provisioning.status == "queued"
    assert provisioning.can_retry is False
    assert provisioning.attempt_count == 1
    assert provisioning.last_error_reason is None
    assert provisioning.last_error_payload is None
    assert provisioning.provider_operation_key == provider_operation_key
    assert len(outbox_events) == 2
    retry_outbox_key = f"activation:phone.provision:{activation.id}:attempt:2"
    assert {event.idempotency_key for event in outbox_events} == {
        provider_operation_key,
        retry_outbox_key,
    }


@pytest.mark.anyio
async def test_retry_requires_failed_retryable_state(
    db_session,
    active_user,
) -> None:
    await _seed_eligible_customer(db_session, active_user)
    await ActivationProvisioningService(db_session).confirm(
        active_user.id,
        arq_pool=None,
    )
    provisioning = await db_session.scalar(
        select(PhoneNumberProvisioning).where(
            PhoneNumberProvisioning.user_id == active_user.id
        )
    )
    assert provisioning is not None
    provisioning.status = "failed"
    provisioning.can_retry = False
    await db_session.commit()

    with pytest.raises(ActivationProvisioningBlockedError) as exc_info:
        await ActivationProvisioningService(db_session).retry(
            active_user.id,
            arq_pool=None,
        )

    assert exc_info.value.code == "provisioning_retry_not_allowed"


@pytest.mark.anyio
async def test_confirm_rolls_back_consent_when_outbox_insert_fails(
    db_session,
    active_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_eligible_customer(db_session, active_user)
    user_id = active_user.id
    service = ActivationProvisioningService(db_session)

    async def fail_add(**_kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(service.outbox_service, "add", fail_add)

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await service.confirm(user_id, arq_pool=None)

    activation = await db_session.scalar(
        select(CustomerActivation).where(CustomerActivation.user_id == user_id)
    )
    assert activation is not None
    assert activation.provisioning_consented_at is None
    assert activation.provisioning_idempotency_key is None
    assert await db_session.scalar(
        select(func.count()).select_from(PhoneNumberProvisioning)
    ) == 0
    assert await db_session.scalar(
        select(func.count()).select_from(ActivationEvent)
    ) == 0


@pytest.mark.anyio
async def test_confirm_acquires_command_locks_in_required_order() -> None:
    events: list[str] = []
    user_id = uuid4()
    activation_id = uuid4()
    profile = SimpleNamespace(
        owner_name="Camille Martin",
        business_name="Atelier Martin",
        business_type="Plomberie",
        public_description="Dépannage.",
        timezone="Europe/Paris",
        business_hours=_complete_business_hours(),
        existing_phone_e164="+33612345678",
        confirmed_carrier="orange",
        receptionist_name="Léa",
        content_revision=3,
    )
    activation = SimpleNamespace(
        id=activation_id,
        user_id=user_id,
        profile_confirmed_revision=3,
        profile_confirmed_at=datetime(2026, 7, 18, 8, 0, tzinfo=UTC),
        provisioning_consented_at=None,
        provisioning_idempotency_key=None,
    )
    subscription = SimpleNamespace(
        plan_tier="starter",
        status="active",
        current_period_start=datetime(2026, 7, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 8, 1, tzinfo=UTC),
    )

    class Session:
        async def commit(self) -> None:
            events.append("commit")

        async def rollback(self) -> None:
            events.append("rollback")

    class Users:
        async def get_by_id_for_update(self, requested_user_id):
            assert requested_user_id == user_id
            events.append("user")
            return SimpleNamespace(id=user_id, status="active", country_code="FR")

    class Activations:
        async def get_by_user_id_for_update(self, requested_user_id):
            assert requested_user_id == user_id
            events.append("activation")
            return activation

    class Profiles:
        async def get_by_user_id_for_update(self, requested_user_id):
            assert requested_user_id == user_id
            events.append("profile")
            return profile

    class Subscriptions:
        async def get_by_user_id_for_update(self, requested_user_id):
            assert requested_user_id == user_id
            events.append("subscription")
            return subscription

    class Usage:
        async def get_current_balance(self, *, user_id):
            return 60

    class Provisionings:
        async def queue_initial(self, *, user_id, operation_key):
            events.append("provisioning")
            return SimpleNamespace(provider_operation_key=operation_key)

    class Phones:
        async def get_by_user_id_for_update(self, requested_user_id):
            assert requested_user_id == user_id
            events.append("phone")
            return None

    class Outbox:
        async def add(self, **_kwargs):
            return SimpleNamespace()

    class ActivationEvents:
        async def append(self, **_kwargs):
            return SimpleNamespace()

    canonical_snapshot = object()

    class Snapshots:
        async def get(self, requested_user_id):
            assert requested_user_id == user_id
            return canonical_snapshot

    service = ActivationProvisioningService(
        Session(),
        user_repository=Users(),
        activation_repository=Activations(),
        business_profile_repository=Profiles(),
        subscription_repository=Subscriptions(),
        usage_repository=Usage(),
        provisioning_repository=Provisionings(),
        phone_number_repository=Phones(),
        outbox_service=Outbox(),
        activation_event_repository=ActivationEvents(),
        snapshot_service=Snapshots(),
        now=lambda: datetime(2026, 7, 18, 9, 0, tzinfo=UTC),
    )

    result = await service.confirm(user_id, arq_pool=None)

    assert result is canonical_snapshot
    assert events == [
        "user",
        "activation",
        "profile",
        "subscription",
        "provisioning",
        "phone",
        "commit",
    ]
