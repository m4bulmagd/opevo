from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models.agent_config import AgentConfig
from app.models.business_profile import BusinessProfile
from app.models.customer_activation import CustomerActivation
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.user import User
from app.services.activation_policy import ActivationStage
from app.services.activation_snapshot_service import ActivationSnapshotService
from app.services.routing_fingerprint import routing_fingerprint


NOW = datetime(2026, 7, 17, 10, tzinfo=UTC)


class FakeByIdRepository:
    def __init__(self, value) -> None:
        self.value = value
        self.calls: list[object] = []

    async def get_by_id(self, user_id):
        self.calls.append(user_id)
        return self.value


class FakeByUserRepository:
    def __init__(self, value) -> None:
        self.value = value
        self.calls: list[object] = []

    async def get_by_user_id(self, user_id):
        self.calls.append(user_id)
        return self.value


class FakeUsageRepository:
    def __init__(self, balance: int) -> None:
        self.balance = balance
        self.calls: list[object] = []

    async def get_current_balance(self, *, user_id) -> int:
        self.calls.append(user_id)
        return self.balance


def complete_hours() -> dict[str, dict[str, object]]:
    return {
        day: {"closed": True, "intervals": []}
        for day in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
    }


def build_records():
    user_id = uuid4()
    user = User(
        id=user_id,
        clerk_user_id="snapshot-user",
        email="snapshot@example.com",
        status="active",
    )
    profile = BusinessProfile(
        user_id=user_id,
        owner_name="Sam",
        business_name="Sam Plumbing",
        business_type="Plumber",
        public_description="Emergency and maintenance plumbing in Lyon.",
        timezone="Europe/Paris",
        business_hours=complete_hours(),
        existing_phone_e164="+33612345678",
        confirmed_carrier="orange",
        receptionist_name="Ava",
        faqs=[],
        content_revision=4,
        routing_revision=2,
    )
    phone = PhoneNumber(
        user_id=user_id,
        e164="+33912345678",
        country_code="FR",
        provider="telnyx",
        provider_number_id="pn_snapshot",
        provider_connection_name="app-active",
        is_active=True,
    )
    activation = CustomerActivation(
        user_id=user_id,
        workflow_version=1,
        profile_confirmed_revision=4,
        profile_confirmed_at=NOW,
        provisioning_consented_at=NOW,
        verification_status="succeeded",
        forwarding_verified_at=NOW,
        go_live_approved_at=NOW,
        activated_at=NOW,
    )
    activation.verified_routing_fingerprint = routing_fingerprint(profile, phone)
    subscription = Subscription(
        user_id=user_id,
        stripe_customer_id="cus_snapshot",
        stripe_subscription_id="sub_snapshot",
        plan_tier="starter",
        status="active",
        allocated_minutes=60,
        current_period_start=datetime(2026, 7, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 8, 1, tzinfo=UTC),
    )
    provisioning = PhoneNumberProvisioning(
        user_id=user_id,
        phone_number_id=phone.id,
        target_country_code="FR",
        status="succeeded",
        attempt_count=1,
        can_retry=False,
        last_error_payload={"secret_provider_detail": "must-not-leak"},
    )
    agent = AgentConfig(
        user_id=user_id,
        agent_name="Ava",
        business_display_name="Sam Plumbing",
        profile_projection_revision=4,
        owner_context="Sam runs Sam Plumbing.",
        system_prompt="Be concise.",
        knowledge_base="Open weekdays.",
        pipeline_mode="stt_llm_tts",
        is_enabled=True,
    )
    return user, profile, activation, subscription, provisioning, phone, agent


def build_service(*, records, balance: int = 30):
    user, profile, activation, subscription, provisioning, phone, agent = records
    repositories = {
        "user_repository": FakeByIdRepository(user),
        "business_profile_repository": FakeByUserRepository(profile),
        "activation_repository": FakeByUserRepository(activation),
        "subscription_repository": FakeByUserRepository(subscription),
        "usage_repository": FakeUsageRepository(balance),
        "provisioning_repository": FakeByUserRepository(provisioning),
        "phone_number_repository": FakeByUserRepository(phone),
        "agent_config_repository": FakeByUserRepository(agent),
    }
    return ActivationSnapshotService(**repositories), repositories


@pytest.mark.anyio
async def test_get_loads_each_authoritative_row_once_and_returns_active_snapshot() -> None:
    records = build_records()
    service, repositories = build_service(records=records)
    user = records[0]

    snapshot = await service.get(user.id, now=NOW)

    assert snapshot.stage is ActivationStage.ACTIVE
    assert snapshot.workflow_version == 1
    assert snapshot.profile.business_name == "Sam Plumbing"
    assert snapshot.profile_constraints.phone_country == "FR"
    assert snapshot.activation.activated_at == NOW
    assert snapshot.billing.eligible is True
    assert snapshot.billing.minutes_remaining == 30
    assert snapshot.number.assigned_e164 == "+33912345678"
    assert snapshot.number.provider_ready is True
    assert snapshot.runtime_readiness.can_route is True
    assert snapshot.runtime_readiness.policy_version == "runtime-v2"
    assert "last_error_payload" not in snapshot.model_dump_json()
    assert "secret_provider_detail" not in snapshot.model_dump_json()
    for repository in repositories.values():
        assert repository.calls == [user.id]


@pytest.mark.anyio
async def test_get_returns_safe_profile_required_snapshot_for_missing_domain_rows() -> None:
    user = User(
        id=uuid4(),
        clerk_user_id="new-user",
        email="new@example.com",
        status="active",
    )
    empty_records = (user, None, None, None, None, None, None)
    service, _repositories = build_service(records=empty_records, balance=0)

    snapshot = await service.get(user.id, now=NOW)

    assert snapshot.stage is ActivationStage.PROFILE_REQUIRED
    assert snapshot.profile.owner_name is None
    assert snapshot.profile.content_revision == 1
    assert snapshot.activation.profile_confirmed_at is None
    assert snapshot.billing.eligible is False
    assert snapshot.number.assigned_e164 is None
    assert snapshot.blockers == ["profile_not_confirmed"]


@pytest.mark.anyio
async def test_expired_verification_window_is_not_reported_open() -> None:
    records = list(build_records())
    activation = records[2]
    activation.forwarding_verified_at = None
    activation.verified_routing_fingerprint = None
    activation.go_live_approved_at = None
    activation.activated_at = None
    activation.verification_window_started_at = datetime(2026, 7, 17, 9, tzinfo=UTC)
    activation.verification_window_expires_at = NOW
    activation.verification_status = "open"
    service, _repositories = build_service(records=tuple(records))

    snapshot = await service.get(records[0].id, now=NOW)

    assert snapshot.stage is ActivationStage.FORWARDING_REQUIRED
