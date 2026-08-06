from dataclasses import FrozenInstanceError, is_dataclass
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
from app.services.customer_readiness_policy import (
    CustomerReadinessStage,
    ReadinessBlocker,
)
from app.services.customer_readiness_service import (
    CustomerReadinessService,
    activation_readiness_prerequisites,
)


NOW = datetime(2026, 7, 16, 12, tzinfo=UTC)


class FakeUserRepository:
    def __init__(self, value: User | None) -> None:
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


def build_records():
    user_id = uuid4()
    user = User(
        id=user_id,
        clerk_user_id="user-ready",
        email="ready@example.com",
        status="active",
    )
    subscription = Subscription(
        user_id=user_id,
        stripe_customer_id="cus_ready",
        stripe_subscription_id="sub_ready",
        plan_tier="starter",
        status="active",
        allocated_minutes=60,
        current_period_start=datetime(2026, 7, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 8, 1, tzinfo=UTC),
    )
    phone = PhoneNumber(
        user_id=user_id,
        e164="+33123456789",
        country_code="FR",
        provider="telnyx",
        provider_number_id="pn_ready",
        provider_connection_name="app-active",
        is_active=True,
    )
    provisioning = PhoneNumberProvisioning(
        user_id=user_id,
        target_country_code="FR",
        status="succeeded",
        attempt_count=1,
        can_retry=False,
    )
    agent_config = AgentConfig(
        user_id=user_id,
        agent_name="Ava",
        owner_context="Sam's plumbing business",
        system_prompt="Be concise.",
        knowledge_base="Open weekdays.",
        pipeline_mode="stt_llm_tts",
        is_enabled=True,
    )
    return user, subscription, phone, provisioning, agent_config


def build_service(
    *,
    user,
    subscription,
    balance: int,
    phone,
    provisioning,
    agent_config,
    activation_flow_enabled: bool = False,
    profile=None,
    activation=None,
):
    repositories = {
        "user_repository": FakeUserRepository(user),
        "subscription_repository": FakeByUserRepository(subscription),
        "usage_repository": FakeUsageRepository(balance),
        "phone_number_repository": FakeByUserRepository(phone),
        "provisioning_repository": FakeByUserRepository(provisioning),
        "agent_config_repository": FakeByUserRepository(agent_config),
    }
    if activation_flow_enabled:
        repositories["business_profile_repository"] = FakeByUserRepository(profile)
        repositories["activation_repository"] = FakeByUserRepository(activation)
    return (
        CustomerReadinessService(
            None,
            **repositories,
            activation_flow_enabled=activation_flow_enabled,
        ),
        repositories,
    )


@pytest.mark.anyio
async def test_evaluate_loads_each_record_once_and_returns_live_context() -> None:
    user, subscription, phone, provisioning, agent_config = build_records()
    service, repositories = build_service(
        user=user,
        subscription=subscription,
        balance=30,
        phone=phone,
        provisioning=provisioning,
        agent_config=agent_config,
    )

    context = await service.evaluate(user.id, now=NOW)

    assert context.user is user
    assert context.subscription is subscription
    assert context.balance == 30
    assert context.phone_number is phone
    assert context.provisioning is provisioning
    assert context.agent_config is agent_config
    assert context.result.stage is CustomerReadinessStage.LIVE
    assert context.result.can_route is True
    for repository in repositories.values():
        assert repository.calls == [user.id]


@pytest.mark.anyio
async def test_evaluate_uses_agent_override_without_reloading_agent() -> None:
    user, subscription, phone, provisioning, stored_config = build_records()
    stored_config.is_enabled = False
    service, repositories = build_service(
        user=user,
        subscription=subscription,
        balance=30,
        phone=phone,
        provisioning=provisioning,
        agent_config=stored_config,
    )
    override = AgentConfig(
        user_id=user.id,
        agent_name="Override Ava",
        owner_context="Override business context",
        system_prompt="Be concise.",
        knowledge_base="Open weekdays.",
        pipeline_mode="stt_llm_tts",
        is_enabled=True,
    )

    context = await service.evaluate(
        user.id,
        agent_config_override=override,
        now=NOW,
    )

    assert context.agent_config is override
    assert context.result.can_route is True
    assert repositories["agent_config_repository"].calls == []


@pytest.mark.anyio
async def test_missing_user_returns_fail_closed_readiness_result() -> None:
    _user, subscription, phone, provisioning, agent_config = build_records()
    service, _repositories = build_service(
        user=None,
        subscription=subscription,
        balance=30,
        phone=phone,
        provisioning=provisioning,
        agent_config=agent_config,
    )

    context = await service.evaluate(uuid4(), now=NOW)

    assert context.user is None
    assert ReadinessBlocker.ACCOUNT_INACTIVE in context.result.blockers
    assert context.result.can_activate is False
    assert context.result.can_route is False


@pytest.mark.anyio
async def test_enabled_activation_flow_loads_prerequisites_once_and_blocks_stale_projection() -> (
    None
):
    user, subscription, phone, provisioning, agent_config = build_records()
    profile = BusinessProfile(
        user_id=user.id,
        owner_name="Sam",
        business_name="Sam Plumbing",
        business_type="Plumber",
        public_description="Emergency and maintenance plumbing.",
        timezone="Europe/Paris",
        business_hours={"monday": {"closed": True, "intervals": []}},
        existing_phone_e164="+33612345678",
        confirmed_carrier="orange",
        receptionist_name="Ava",
        content_revision=3,
        routing_revision=2,
    )
    activation = CustomerActivation(
        user_id=user.id,
        profile_confirmed_revision=3,
        profile_confirmed_at=NOW,
        forwarding_verified_at=NOW,
        verified_routing_fingerprint="stale",
        go_live_approved_at=NOW,
        activated_at=NOW,
    )
    agent_config.profile_projection_revision = 2
    service, repositories = build_service(
        user=user,
        subscription=subscription,
        balance=30,
        phone=phone,
        provisioning=provisioning,
        agent_config=agent_config,
        activation_flow_enabled=True,
        profile=profile,
        activation=activation,
    )

    context = await service.evaluate(user.id, now=NOW)

    assert ReadinessBlocker.PROFILE_PROJECTION_STALE in context.result.blockers
    assert ReadinessBlocker.FORWARDING_NOT_VERIFIED in context.result.blockers
    assert context.result.can_route is False
    for repository in repositories.values():
        if hasattr(repository, "calls"):
            assert repository.calls == [user.id]


def test_activation_prerequisites_are_named_immutable_and_reject_null_fingerprints() -> (
    None
):
    activation = CustomerActivation(
        user_id=uuid4(),
        forwarding_verified_at=NOW,
        verified_routing_fingerprint=None,
    )

    prerequisites = activation_readiness_prerequisites(
        profile=None,
        activation=activation,
        phone_number=None,
        agent_config=None,
    )

    assert is_dataclass(prerequisites)
    assert not hasattr(prerequisites, "current_routing_fingerprint")
    assert prerequisites.business_profile_complete is False
    assert prerequisites.profile_projection_current is False
    assert prerequisites.forwarding_verified is False
    assert prerequisites.go_live_approved is False
    assert prerequisites.go_live_activated is False
    with pytest.raises(FrozenInstanceError):
        prerequisites.forwarding_verified = True
