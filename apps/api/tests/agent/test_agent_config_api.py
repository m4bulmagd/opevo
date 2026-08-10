from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.models.agent_config import AgentConfig
from app.models.business_profile import BusinessProfile
from app.models.customer_activation import CustomerActivation
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.models.user import User
from opevo_contracts import (
    AGENT_NAME_MAX_LENGTH,
    KNOWLEDGE_BASE_MAX_LENGTH,
    OWNER_CONTEXT_MAX_LENGTH,
    SYSTEM_PROMPT_MAX_LENGTH,
)


@pytest.fixture
def activation_runtime_enabled(test_app):
    runtime = test_app.state.runtime
    previous_settings = runtime.settings
    runtime.settings = previous_settings.model_copy(
        update={"activation_flow_enabled": True}
    )
    try:
        yield
    finally:
        runtime.settings = previous_settings


def test_activation_flow_defaults_off() -> None:
    settings = Settings(
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
    )

    assert settings.activation_flow_enabled is False


def test_api_agent_name_limit_matches_profile_receptionist_name_bound() -> None:
    assert AGENT_NAME_MAX_LENGTH == 100


async def seed_agent_config(
    database_url: str,
    *,
    external_user_id: str,
    email: str,
    agent_name: str = "Assistant",
    owner_context: str | None = None,
    system_prompt: str = "",
    knowledge_base: str = "",
    pipeline_mode: str = "stt_llm_tts",
    is_enabled: bool = False,
) -> None:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(external_user_id=external_user_id, email=email)
        session.add(user)
        await session.flush()
        session.add(
            AgentConfig(
                user_id=user.id,
                agent_name=agent_name,
                owner_context=owner_context,
                system_prompt=system_prompt,
                knowledge_base=knowledge_base,
                pipeline_mode=pipeline_mode,
                is_enabled=is_enabled,
            )
        )
        await session.commit()
    await engine.dispose()


async def seed_phone_number(
    database_url: str,
    *,
    external_user_id: str,
    is_active: bool,
    provider_number_id: str | None = "pn_123",
) -> None:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = (
            await session.execute(
                select(User).where(User.external_user_id == external_user_id)
            )
        ).scalar_one()
        session.add(
            PhoneNumber(
                user_id=user.id,
                e164="+33123456789",
                country_code="FR",
                provider="telnyx",
                provider_number_id=provider_number_id,
                provider_connection_name="app-active" if is_active else "app-disabled",
                is_active=is_active,
            )
        )
        await session.commit()
    await engine.dispose()


async def seed_subscription(
    database_url: str,
    *,
    external_user_id: str,
    status: str = "active",
    plan_tier: str = "starter",
    current_period_start: datetime | None = None,
    current_period_end: datetime | None = None,
) -> None:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = (
            await session.execute(
                select(User).where(User.external_user_id == external_user_id)
            )
        ).scalar_one()
        now = datetime.now(UTC)
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id=f"cus_{external_user_id}",
                stripe_subscription_id=f"sub_{external_user_id}",
                plan_tier=plan_tier,
                status=status,
                allocated_minutes=60,
                current_period_start=current_period_start or now - timedelta(days=1),
                current_period_end=current_period_end or now + timedelta(days=1),
            )
        )
        await session.commit()
    await engine.dispose()


async def seed_usage_balance(
    database_url: str,
    *,
    external_user_id: str,
    balance: int,
) -> None:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = (
            await session.execute(
                select(User).where(User.external_user_id == external_user_id)
            )
        ).scalar_one()
        session.add(
            UsageLedger(
                user_id=user.id,
                event_type="invoice_paid_reset",
                source_id=f"balance_{external_user_id}_{balance}",
                minutes_delta=balance,
                balance_after=balance,
            )
        )
        await session.commit()
    await engine.dispose()


async def seed_provisioning(
    database_url: str, *, external_user_id: str, status: str, can_retry: bool = False
) -> None:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = (
            await session.execute(
                select(User).where(User.external_user_id == external_user_id)
            )
        ).scalar_one()
        phone_number = (
            await session.execute(
                select(PhoneNumber).where(PhoneNumber.user_id == user.id)
            )
        ).scalar_one_or_none()
        session.add(
            PhoneNumberProvisioning(
                user_id=user.id,
                phone_number_id=phone_number.id if phone_number is not None else None,
                target_country_code="FR",
                status=status,
                attempt_count=1,
                can_retry=can_retry,
            )
        )
        await session.commit()
    await engine.dispose()


async def fetch_agent_config(database_url: str, *, external_user_id: str) -> AgentConfig:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(
            select(AgentConfig)
            .join(User, AgentConfig.user_id == User.id)
            .where(User.external_user_id == external_user_id)
        )
        config = result.scalar_one()
    await engine.dispose()
    return config


async def fetch_business_profile(
    database_url: str, *, external_user_id: str
) -> BusinessProfile:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(
            select(BusinessProfile)
            .join(User, BusinessProfile.user_id == User.id)
            .where(User.external_user_id == external_user_id)
        )
        profile = result.scalar_one()
    await engine.dispose()
    return profile


async def fetch_customer_activation(
    database_url: str, *, external_user_id: str
) -> CustomerActivation:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(
            select(CustomerActivation)
            .join(User, CustomerActivation.user_id == User.id)
            .where(User.external_user_id == external_user_id)
        )
        activation = result.scalar_one()
    await engine.dispose()
    return activation


async def fetch_phone_number(database_url: str, *, external_user_id: str) -> PhoneNumber:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(
            select(PhoneNumber)
            .join(User, PhoneNumber.user_id == User.id)
            .where(User.external_user_id == external_user_id)
        )
        phone_number = result.scalar_one()
    await engine.dispose()
    return phone_number


async def fetch_outbox_event(database_url: str) -> OutboxEvent:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        event = (await session.execute(select(OutboxEvent))).scalar_one()
    await engine.dispose()
    return event


async def fetch_outbox_event_count(database_url: str) -> int:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        count = await session.scalar(select(func.count(OutboxEvent.id)))
    await engine.dispose()
    return int(count or 0)


@pytest.mark.anyio
async def test_get_agent_config_returns_full_config(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    await seed_agent_config(
        client_database_url,
        external_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Ava",
        owner_context="Muhammad Abulmagd",
        system_prompt="Be helpful.",
        knowledge_base="Open weekdays",
        pipeline_mode="stt_llm_tts",
        is_enabled=False,
    )

    response = await async_client.get(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
    )

    assert response.status_code == 200
    assert response.json()["agent_name"] == "Ava"
    assert response.json()["owner_context"] == "Muhammad Abulmagd"
    assert response.json()["pipeline_mode"] == "stt_llm_tts"
    assert response.json()["is_enabled"] is False


@pytest.mark.anyio
async def test_get_agent_config_returns_bootstrapped_default_config(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(external_user_id="user_bootstrap_cfg", email="boot@example.com"))
        await session.commit()
    await engine.dispose()

    response = await async_client.get(
        "/api/agent/config",
        headers={
            "authorization": f"Bearer {rs256_clerk_token_for('user_bootstrap_cfg')}"
        },
    )

    assert response.status_code == 200
    assert response.json()["agent_name"] == "Assistant"
    assert response.json()["pipeline_mode"] == "stt_llm_tts"
    assert response.json()["is_enabled"] is False


@pytest.mark.anyio
async def test_patch_agent_config_updates_prompt_fields_without_toggle(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    await seed_agent_config(
        client_database_url,
        external_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Ava",
        pipeline_mode="stt_llm_tts",
        is_enabled=False,
    )

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
        json={
            "agent_name": "Reception",
            "knowledge_base": "Open weekdays",
            "pipeline_mode": "sts",
        },
    )

    assert response.status_code == 200
    assert response.json()["agent_name"] == "Reception"
    assert response.json()["knowledge_base"] == "Open weekdays"
    assert response.json()["pipeline_mode"] == "sts"
    assert response.json()["is_enabled"] is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("agent_name", "Reception"),
        ("owner_context", "Owner context"),
        ("system_prompt", "Customer prompt"),
        ("knowledge_base", "Customer knowledge"),
    ],
)
async def test_activation_flow_persists_profile_owned_assistant_content_patch(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
    activation_runtime_enabled,
    field_name: str,
    value: str,
) -> None:
    external_user_id = f"user_managed_{field_name}"
    await seed_agent_config(
        client_database_url,
        external_user_id=external_user_id,
        email=f"managed-{field_name}@example.com",
        agent_name="Ava",
        owner_context="Original owner context",
        system_prompt="Original system prompt",
        knowledge_base="Original knowledge",
        is_enabled=False,
    )
    # The controlled environment remains disabled; request composition is enabled.
    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for(external_user_id)}"},
        json={field_name: value},
    )

    config = await fetch_agent_config(
        client_database_url,
        external_user_id=external_user_id,
    )
    profile = await fetch_business_profile(
        client_database_url,
        external_user_id=external_user_id,
    )

    assert response.status_code == 200
    assert getattr(config, field_name) == value
    if field_name == "agent_name":
        assert profile.receptionist_name == value
    else:
        assert getattr(profile, f"{field_name}_override") == value
    assert config.profile_projection_revision == profile.content_revision


@pytest.mark.anyio
async def test_activation_flow_accepts_idempotent_enabled_value_with_content_patch(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
    activation_runtime_enabled,
) -> None:
    await seed_agent_config(
        client_database_url,
        external_user_id="user_active_content",
        email="active-content@example.com",
        agent_name="Léa",
        is_enabled=True,
    )
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = (
            await session.execute(
                select(User).where(User.external_user_id == "user_active_content")
            )
        ).scalar_one()
        session.add(
            BusinessProfile(
                user_id=user.id,
                receptionist_name="Léa",
                content_revision=3,
            )
        )
        session.add(
            CustomerActivation(
                user_id=user.id,
                profile_confirmed_revision=3,
                profile_confirmed_at=datetime.now(UTC),
            )
        )
        await session.commit()
    await engine.dispose()
    # The controlled environment remains disabled; request composition is enabled.
    response = await async_client.patch(
        "/api/agent/config",
        headers={
            "authorization": f"Bearer {rs256_clerk_token_for('user_active_content')}"
        },
        json={
            "agent_name": "Léa Verified",
            "owner_context": "Atelier Martin reception",
            "system_prompt": "Handle calls professionally.",
            "knowledge_base": "Open weekdays.",
            "pipeline_mode": "stt_llm_tts",
            "is_enabled": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["agent_name"] == "Léa Verified"
    assert response.json()["is_enabled"] is True
    assert await fetch_outbox_event_count(client_database_url) == 0
    profile = await fetch_business_profile(
        client_database_url,
        external_user_id="user_active_content",
    )
    activation = await fetch_customer_activation(
        client_database_url,
        external_user_id="user_active_content",
    )
    assert activation.profile_confirmed_revision == profile.content_revision


@pytest.mark.anyio
async def test_activation_flow_allows_non_projected_patch_fields(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
    activation_runtime_enabled,
) -> None:
    external_user_id = "user_managed_non_projected"
    await seed_agent_config(
        client_database_url,
        external_user_id=external_user_id,
        email="managed-non-projected@example.com",
        agent_name="Ava",
        pipeline_mode="stt_llm_tts",
        is_enabled=False,
    )
    # The controlled environment remains disabled; request composition is enabled.
    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for(external_user_id)}"},
        json={"pipeline_mode": "sts"},
    )

    assert response.status_code == 200
    assert response.json()["pipeline_mode"] == "sts"


@pytest.mark.anyio
async def test_activation_flow_rejects_direct_enable_without_mutation_or_outbox(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
    activation_runtime_enabled,
) -> None:
    external_user_id = "user_activation_managed_enable"
    await seed_agent_config(
        client_database_url,
        external_user_id=external_user_id,
        email="activation-managed-enable@example.com",
        agent_name="Léa",
        owner_context="Atelier Martin reception",
        system_prompt="Answer missed calls professionally.",
        knowledge_base="Open weekdays.",
        is_enabled=False,
    )
    # The controlled environment remains disabled; request composition is enabled.
    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for(external_user_id)}"},
        json={"is_enabled": True},
    )

    stored = await fetch_agent_config(
        client_database_url,
        external_user_id=external_user_id,
    )
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "agent_enable_managed_by_go_live"}}
    assert stored.is_enabled is False
    assert await fetch_outbox_event_count(client_database_url) == 0


@pytest.mark.anyio
async def test_activation_flow_still_allows_customer_to_disable_routing(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
    activation_runtime_enabled,
) -> None:
    external_user_id = "user_activation_disable"
    await seed_agent_config(
        client_database_url,
        external_user_id=external_user_id,
        email="activation-disable@example.com",
        agent_name="Léa",
        owner_context="Atelier Martin reception",
        system_prompt="Answer missed calls professionally.",
        knowledge_base="Open weekdays.",
        is_enabled=True,
    )
    # The controlled environment remains disabled; request composition is enabled.
    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for(external_user_id)}"},
        json={"is_enabled": False},
    )

    stored = await fetch_agent_config(
        client_database_url,
        external_user_id=external_user_id,
    )
    assert response.status_code == 200
    assert response.json()["is_enabled"] is False
    assert stored.is_enabled is False
    event = await fetch_outbox_event(client_database_url)
    assert event.topic == "phone.disable"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field_name", "maximum"),
    [
        ("agent_name", AGENT_NAME_MAX_LENGTH),
        ("owner_context", OWNER_CONTEXT_MAX_LENGTH),
        ("system_prompt", SYSTEM_PROMPT_MAX_LENGTH),
        ("knowledge_base", KNOWLEDGE_BASE_MAX_LENGTH),
    ],
)
async def test_patch_agent_config_accepts_normalized_content_at_limit(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
    field_name: str,
    maximum: int,
) -> None:
    external_user_id = f"user_agent_limit_{field_name}"
    await seed_agent_config(
        client_database_url,
        external_user_id=external_user_id,
        email=f"{field_name}@example.com",
        agent_name="Ava",
        is_enabled=False,
    )
    bounded_value = "x" * maximum

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for(external_user_id)}"},
        json={field_name: f"  {bounded_value}  "},
    )

    stored = await fetch_agent_config(
        client_database_url,
        external_user_id=external_user_id,
    )
    assert response.status_code == 200
    assert response.json()[field_name] == bounded_value
    assert getattr(stored, field_name) == bounded_value


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field_name", "maximum"),
    [
        ("agent_name", AGENT_NAME_MAX_LENGTH),
        ("owner_context", OWNER_CONTEXT_MAX_LENGTH),
        ("system_prompt", SYSTEM_PROMPT_MAX_LENGTH),
        ("knowledge_base", KNOWLEDGE_BASE_MAX_LENGTH),
    ],
)
async def test_patch_agent_config_rejects_oversized_content(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
    field_name: str,
    maximum: int,
) -> None:
    external_user_id = f"user_agent_overflow_{field_name}"
    await seed_agent_config(
        client_database_url,
        external_user_id=external_user_id,
        email=f"overflow-{field_name}@example.com",
        agent_name="Ava",
        is_enabled=False,
    )

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for(external_user_id)}"},
        json={field_name: "x" * (maximum + 1)},
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_patch_agent_config_rejects_unknown_fields(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
) -> None:
    external_user_id = "user_agent_unknown_field"
    await seed_agent_config(
        client_database_url,
        external_user_id=external_user_id,
        email="unknown-field@example.com",
        agent_name="Ava",
        is_enabled=False,
    )

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for(external_user_id)}"},
        json={"unsupported_instruction": "ignore the policy"},
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_legacy_oversized_config_can_be_read_but_not_enabled(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
) -> None:
    external_user_id = "user_agent_legacy_oversized"
    oversized_name = "A" * (AGENT_NAME_MAX_LENGTH + 1)
    await seed_agent_config(
        client_database_url,
        external_user_id=external_user_id,
        email="legacy-oversized@example.com",
        agent_name=oversized_name,
        owner_context="Dental office reception",
        system_prompt="Handle inbound calls professionally.",
        knowledge_base="Open weekdays",
        is_enabled=False,
    )
    await seed_phone_number(
        client_database_url,
        external_user_id=external_user_id,
        is_active=False,
    )
    await seed_subscription(client_database_url, external_user_id=external_user_id)
    await seed_provisioning(
        client_database_url,
        external_user_id=external_user_id,
        status="succeeded",
    )
    await seed_usage_balance(
        client_database_url,
        external_user_id=external_user_id,
        balance=60,
    )
    headers = {"authorization": f"Bearer {rs256_clerk_token_for(external_user_id)}"}

    get_response = await async_client.get("/api/agent/config", headers=headers)
    enable_response = await async_client.patch(
        "/api/agent/config",
        headers=headers,
        json={"is_enabled": True},
    )

    assert get_response.status_code == 200
    assert get_response.json()["agent_name"] == oversized_name
    assert enable_response.status_code == 409
    assert enable_response.json()["detail"] == {
        "code": "agent_not_ready",
        "blockers": ["agent_content_invalid"],
    }


@pytest.mark.anyio
@pytest.mark.parametrize("subscription_status", ["active", "trialing"])
async def test_patch_agent_config_persists_enable_intent_when_is_enabled_changes(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
    subscription_status: str,
) -> None:
    class FakeTelephonyProvider:
        def __init__(self) -> None:
            self.enabled_provider_number_ids: list[str] = []

        async def provision_number(self, *, country_code: str) -> dict:
            raise AssertionError("provision_number should not be called")

        async def enable_number(self, *, provider_number_id: str) -> str:
            self.enabled_provider_number_ids.append(provider_number_id)
            return "app-active"

        async def disable_number(self, *, provider_number_id: str) -> str:
            raise AssertionError("disable_number should not be called")

    await seed_agent_config(
        client_database_url,
        external_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Opevo Front Desk",
        owner_context="Dental office reception",
        system_prompt="Handle inbound calls professionally.",
        knowledge_base="Open weekdays",
        is_enabled=False,
    )
    await seed_phone_number(
        client_database_url, external_user_id="user_agent_cfg", is_active=False
    )
    await seed_subscription(
        client_database_url,
        external_user_id="user_agent_cfg",
        status=subscription_status,
    )
    await seed_usage_balance(
        client_database_url,
        external_user_id="user_agent_cfg",
        balance=60,
    )
    await seed_provisioning(
        client_database_url, external_user_id="user_agent_cfg", status="succeeded"
    )

    response = await async_client.patch(
        "/api/agent/config",
        headers={
            "authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"
        },
        json={"is_enabled": True},
    )

    config = await fetch_agent_config(
        client_database_url, external_user_id="user_agent_cfg"
    )
    phone_number = await fetch_phone_number(
        client_database_url, external_user_id="user_agent_cfg"
    )

    assert response.status_code == 200
    assert response.json()["is_enabled"] is True
    event = await fetch_outbox_event(client_database_url)
    assert config.is_enabled is True
    assert phone_number.is_active is False
    assert phone_number.provider_connection_name == "app-disabled"
    assert event.topic == "phone.enable"
    assert event.aggregate_type == "user"
    assert event.aggregate_id == config.user_id
    assert event.payload == {
        "user_id": str(config.user_id),
        "lifecycle_generation": 1,
    }


@pytest.mark.anyio
async def test_patch_agent_config_toggle_without_phone_number_returns_409(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    await seed_agent_config(
        client_database_url,
        external_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Opevo Front Desk",
        owner_context="Dental office reception",
        system_prompt="Handle inbound calls professionally.",
        knowledge_base="Open weekdays",
        is_enabled=False,
    )
    await seed_subscription(client_database_url, external_user_id="user_agent_cfg")
    await seed_usage_balance(
        client_database_url,
        external_user_id="user_agent_cfg",
        balance=60,
    )

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
        json={"is_enabled": True},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "agent_not_ready",
        "blockers": ["phone_missing"],
    }
    assert await fetch_outbox_event_count(client_database_url) == 0


@pytest.mark.anyio
async def test_patch_agent_config_enable_without_active_subscription_returns_409(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    await seed_agent_config(
        client_database_url,
        external_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Opevo Front Desk",
        owner_context="Dental office reception",
        system_prompt="Handle inbound calls professionally.",
        knowledge_base="Open weekdays",
        is_enabled=False,
    )
    await seed_phone_number(
        client_database_url, external_user_id="user_agent_cfg", is_active=False
    )
    await seed_usage_balance(
        client_database_url,
        external_user_id="user_agent_cfg",
        balance=60,
    )
    await seed_provisioning(
        client_database_url, external_user_id="user_agent_cfg", status="succeeded"
    )

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
        json={"is_enabled": True},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "agent_not_ready",
        "blockers": ["subscription_missing"],
    }
    assert await fetch_outbox_event_count(client_database_url) == 0


@pytest.mark.anyio
async def test_patch_agent_config_enable_with_zero_balance_returns_blocker_and_rolls_back(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
) -> None:
    await seed_agent_config(
        client_database_url,
        external_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Opevo Front Desk",
        owner_context="Dental office reception",
        system_prompt="Handle inbound calls professionally.",
        knowledge_base="Open weekdays",
        is_enabled=False,
    )
    await seed_phone_number(
        client_database_url,
        external_user_id="user_agent_cfg",
        is_active=False,
    )
    await seed_subscription(client_database_url, external_user_id="user_agent_cfg")
    await seed_provisioning(
        client_database_url,
        external_user_id="user_agent_cfg",
        status="succeeded",
    )
    await seed_usage_balance(
        client_database_url,
        external_user_id="user_agent_cfg",
        balance=0,
    )

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
        json={"is_enabled": True},
    )

    config = await fetch_agent_config(
        client_database_url,
        external_user_id="user_agent_cfg",
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "agent_not_ready",
        "blockers": ["minutes_exhausted"],
    }
    assert config.is_enabled is False
    assert await fetch_outbox_event_count(client_database_url) == 0


@pytest.mark.anyio
async def test_patch_agent_config_enable_with_expired_period_returns_blocker_and_rolls_back(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
) -> None:
    now = datetime.now(UTC)
    await seed_agent_config(
        client_database_url,
        external_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Opevo Front Desk",
        owner_context="Dental office reception",
        system_prompt="Handle inbound calls professionally.",
        knowledge_base="Open weekdays",
        is_enabled=False,
    )
    await seed_phone_number(
        client_database_url,
        external_user_id="user_agent_cfg",
        is_active=False,
    )
    await seed_subscription(
        client_database_url,
        external_user_id="user_agent_cfg",
        current_period_start=now - timedelta(days=2),
        current_period_end=now - timedelta(days=1),
    )
    await seed_provisioning(
        client_database_url,
        external_user_id="user_agent_cfg",
        status="succeeded",
    )
    await seed_usage_balance(
        client_database_url,
        external_user_id="user_agent_cfg",
        balance=60,
    )

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
        json={"is_enabled": True},
    )

    config = await fetch_agent_config(
        client_database_url,
        external_user_id="user_agent_cfg",
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "agent_not_ready",
        "blockers": ["subscription_period_inactive"],
    }
    assert config.is_enabled is False
    assert await fetch_outbox_event_count(client_database_url) == 0


@pytest.mark.anyio
async def test_patch_agent_config_enable_with_default_name_is_case_insensitive(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
) -> None:
    await seed_agent_config(
        client_database_url,
        external_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name=" assistant ",
        owner_context="Dental office reception",
        system_prompt="Handle inbound calls professionally.",
        knowledge_base="Open weekdays",
        is_enabled=False,
    )
    await seed_phone_number(
        client_database_url,
        external_user_id="user_agent_cfg",
        is_active=False,
    )
    await seed_subscription(client_database_url, external_user_id="user_agent_cfg")
    await seed_provisioning(
        client_database_url,
        external_user_id="user_agent_cfg",
        status="succeeded",
    )
    await seed_usage_balance(
        client_database_url,
        external_user_id="user_agent_cfg",
        balance=60,
    )

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
        json={"is_enabled": True},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "agent_not_ready",
        "blockers": ["agent_setup_incomplete"],
    }
    assert await fetch_outbox_event_count(client_database_url) == 0


@pytest.mark.anyio
async def test_patch_agent_config_enable_without_provider_number_id_returns_409(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    await seed_agent_config(
        client_database_url,
        external_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Opevo Front Desk",
        owner_context="Dental office reception",
        system_prompt="Handle inbound calls professionally.",
        knowledge_base="Open weekdays",
        is_enabled=False,
    )
    await seed_phone_number(
        client_database_url,
        external_user_id="user_agent_cfg",
        is_active=False,
        provider_number_id=None,
    )
    await seed_subscription(client_database_url, external_user_id="user_agent_cfg")
    await seed_provisioning(
        client_database_url,
        external_user_id="user_agent_cfg",
        status="failed",
        can_retry=True,
    )
    await seed_usage_balance(
        client_database_url,
        external_user_id="user_agent_cfg",
        balance=60,
    )

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
        json={"is_enabled": True},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "agent_not_ready",
        "blockers": ["phone_provider_id_missing"],
    }
    assert await fetch_outbox_event_count(client_database_url) == 0


@pytest.mark.anyio
async def test_patch_agent_config_enable_with_incomplete_setup_returns_409(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    await seed_agent_config(
        client_database_url,
        external_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Assistant",
        owner_context="",
        system_prompt="",
        knowledge_base="",
        is_enabled=False,
    )
    await seed_phone_number(
        client_database_url, external_user_id="user_agent_cfg", is_active=False
    )
    await seed_subscription(client_database_url, external_user_id="user_agent_cfg")
    await seed_provisioning(
        client_database_url, external_user_id="user_agent_cfg", status="succeeded"
    )
    await seed_usage_balance(
        client_database_url,
        external_user_id="user_agent_cfg",
        balance=60,
    )

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
        json={"is_enabled": True},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "agent_not_ready",
        "blockers": ["agent_setup_incomplete"],
    }
    assert await fetch_outbox_event_count(client_database_url) == 0


@pytest.mark.anyio
async def test_patch_agent_config_commit_survives_redis_wakeup_failure(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    class FailingPool:
        async def enqueue_job(self, _name, _payload, **_kwargs):
            raise ConnectionError("redis unavailable")

    await seed_agent_config(
        client_database_url,
        external_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Opevo Front Desk",
        owner_context="Dental office reception",
        system_prompt="Handle inbound calls professionally.",
        knowledge_base="Open weekdays",
        is_enabled=False,
    )
    await seed_phone_number(
        client_database_url, external_user_id="user_agent_cfg", is_active=False
    )
    await seed_subscription(client_database_url, external_user_id="user_agent_cfg")
    await seed_provisioning(
        client_database_url, external_user_id="user_agent_cfg", status="succeeded"
    )
    await seed_usage_balance(
        client_database_url,
        external_user_id="user_agent_cfg",
        balance=60,
    )

    from app.main import app

    original_pool = app.state.runtime.arq_pool
    app.state.runtime.arq_pool = FailingPool()
    try:
        response = await async_client.patch(
            "/api/agent/config",
            headers={
                "authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"
            },
            json={"is_enabled": True},
        )
    finally:
        app.state.runtime.arq_pool = original_pool

    config = await fetch_agent_config(
        client_database_url, external_user_id="user_agent_cfg"
    )
    phone_number = await fetch_phone_number(
        client_database_url, external_user_id="user_agent_cfg"
    )

    event = await fetch_outbox_event(client_database_url)
    assert response.status_code == 200
    assert config.is_enabled is True
    assert phone_number.is_active is False
    assert phone_number.provider_connection_name == "app-disabled"
    assert event.status == "pending"
