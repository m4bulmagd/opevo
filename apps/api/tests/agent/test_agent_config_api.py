import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_config import AgentConfig
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.user import User


async def seed_agent_config(
    database_url: str,
    *,
    clerk_user_id: str,
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
        user = User(clerk_user_id=clerk_user_id, email=email)
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


async def seed_phone_number(database_url: str, *, clerk_user_id: str, is_active: bool) -> None:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = (
            await session.execute(select(User).where(User.clerk_user_id == clerk_user_id))
        ).scalar_one()
        session.add(
            PhoneNumber(
                user_id=user.id,
                e164="+33123456789",
                country_code="FR",
                provider="telnyx",
                provider_number_id="pn_123",
                provider_connection_name="app-active" if is_active else "app-disabled",
                is_active=is_active,
            )
        )
        await session.commit()
    await engine.dispose()


async def seed_subscription(database_url: str, *, clerk_user_id: str, status: str = "active", plan_tier: str = "starter") -> None:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = (
            await session.execute(select(User).where(User.clerk_user_id == clerk_user_id))
        ).scalar_one()
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id=f"cus_{clerk_user_id}",
                stripe_subscription_id=f"sub_{clerk_user_id}",
                plan_tier=plan_tier,
                status=status,
                allocated_minutes=60,
                current_period_start=None,
                current_period_end=None,
            )
        )
        await session.commit()
    await engine.dispose()


async def seed_provisioning(database_url: str, *, clerk_user_id: str, status: str, can_retry: bool = False) -> None:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = (
            await session.execute(select(User).where(User.clerk_user_id == clerk_user_id))
        ).scalar_one()
        phone_number = (
            await session.execute(select(PhoneNumber).where(PhoneNumber.user_id == user.id))
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


async def fetch_agent_config(database_url: str, *, clerk_user_id: str) -> AgentConfig:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(
            select(AgentConfig)
            .join(User, AgentConfig.user_id == User.id)
            .where(User.clerk_user_id == clerk_user_id)
        )
        config = result.scalar_one()
    await engine.dispose()
    return config


async def fetch_phone_number(database_url: str, *, clerk_user_id: str) -> PhoneNumber:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(
            select(PhoneNumber)
            .join(User, PhoneNumber.user_id == User.id)
            .where(User.clerk_user_id == clerk_user_id)
        )
        phone_number = result.scalar_one()
    await engine.dispose()
    return phone_number


@pytest.mark.anyio
async def test_get_agent_config_returns_full_config(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    await seed_agent_config(
        client_database_url,
        clerk_user_id="user_agent_cfg",
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
        session.add(User(clerk_user_id="user_bootstrap_cfg", email="boot@example.com"))
        await session.commit()
    await engine.dispose()

    response = await async_client.get(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_bootstrap_cfg')}"},
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
        clerk_user_id="user_agent_cfg",
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
async def test_patch_agent_config_enables_number_when_is_enabled_changes(
    async_client, client_database_url, rs256_clerk_token_for
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
        clerk_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Presvo Front Desk",
        owner_context="Dental office reception",
        system_prompt="Handle inbound calls professionally.",
        knowledge_base="Open weekdays",
        is_enabled=False,
    )
    await seed_phone_number(client_database_url, clerk_user_id="user_agent_cfg", is_active=False)
    await seed_subscription(client_database_url, clerk_user_id="user_agent_cfg")
    await seed_provisioning(client_database_url, clerk_user_id="user_agent_cfg", status="succeeded")

    from app.main import app
    from app.providers.telephony.telnyx import get_telephony_provider

    fake_provider = FakeTelephonyProvider()
    app.dependency_overrides[get_telephony_provider] = lambda: fake_provider
    try:
        response = await async_client.patch(
            "/api/agent/config",
            headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
            json={"is_enabled": True},
        )
    finally:
        app.dependency_overrides.pop(get_telephony_provider, None)

    config = await fetch_agent_config(client_database_url, clerk_user_id="user_agent_cfg")
    phone_number = await fetch_phone_number(client_database_url, clerk_user_id="user_agent_cfg")

    assert response.status_code == 200
    assert response.json()["is_enabled"] is True
    assert fake_provider.enabled_provider_number_ids == ["pn_123"]
    assert config.is_enabled is True
    assert phone_number.is_active is True
    assert phone_number.provider_connection_name == "app-active"


@pytest.mark.anyio
async def test_patch_agent_config_toggle_without_phone_number_returns_409(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    await seed_agent_config(
        client_database_url,
        clerk_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Presvo Front Desk",
        owner_context="Dental office reception",
        system_prompt="Handle inbound calls professionally.",
        knowledge_base="Open weekdays",
        is_enabled=False,
    )
    await seed_subscription(client_database_url, clerk_user_id="user_agent_cfg")

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
        json={"is_enabled": True},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Agent setup incomplete"


@pytest.mark.anyio
async def test_patch_agent_config_enable_without_active_subscription_returns_409(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    await seed_agent_config(
        client_database_url,
        clerk_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Presvo Front Desk",
        owner_context="Dental office reception",
        system_prompt="Handle inbound calls professionally.",
        knowledge_base="Open weekdays",
        is_enabled=False,
    )
    await seed_phone_number(client_database_url, clerk_user_id="user_agent_cfg", is_active=False)
    await seed_provisioning(client_database_url, clerk_user_id="user_agent_cfg", status="succeeded")

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
        json={"is_enabled": True},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Agent setup incomplete"


@pytest.mark.anyio
async def test_patch_agent_config_enable_without_successful_provisioning_returns_409(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    await seed_agent_config(
        client_database_url,
        clerk_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Presvo Front Desk",
        owner_context="Dental office reception",
        system_prompt="Handle inbound calls professionally.",
        knowledge_base="Open weekdays",
        is_enabled=False,
    )
    await seed_phone_number(client_database_url, clerk_user_id="user_agent_cfg", is_active=False)
    await seed_subscription(client_database_url, clerk_user_id="user_agent_cfg")
    await seed_provisioning(client_database_url, clerk_user_id="user_agent_cfg", status="failed", can_retry=True)

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
        json={"is_enabled": True},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Agent setup incomplete"


@pytest.mark.anyio
async def test_patch_agent_config_enable_with_incomplete_setup_returns_409(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    await seed_agent_config(
        client_database_url,
        clerk_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Assistant",
        owner_context="",
        system_prompt="",
        knowledge_base="",
        is_enabled=False,
    )
    await seed_phone_number(client_database_url, clerk_user_id="user_agent_cfg", is_active=False)
    await seed_subscription(client_database_url, clerk_user_id="user_agent_cfg")
    await seed_provisioning(client_database_url, clerk_user_id="user_agent_cfg", status="succeeded")

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
        json={"is_enabled": True},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Agent setup incomplete"


@pytest.mark.anyio
async def test_patch_agent_config_rolls_back_when_telephony_switch_fails(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    class FailingTelephonyProvider:
        async def provision_number(self, *, country_code: str) -> dict:
            raise AssertionError("provision_number should not be called")

        async def enable_number(self, *, provider_number_id: str) -> str:
            raise RuntimeError("telnyx unavailable")

        async def disable_number(self, *, provider_number_id: str) -> str:
            raise AssertionError("disable_number should not be called")

    await seed_agent_config(
        client_database_url,
        clerk_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Presvo Front Desk",
        owner_context="Dental office reception",
        system_prompt="Handle inbound calls professionally.",
        knowledge_base="Open weekdays",
        is_enabled=False,
    )
    await seed_phone_number(client_database_url, clerk_user_id="user_agent_cfg", is_active=False)
    await seed_subscription(client_database_url, clerk_user_id="user_agent_cfg")
    await seed_provisioning(client_database_url, clerk_user_id="user_agent_cfg", status="succeeded")

    from app.main import app
    from app.providers.telephony.telnyx import get_telephony_provider

    app.dependency_overrides[get_telephony_provider] = lambda: FailingTelephonyProvider()
    try:
        response = await async_client.patch(
            "/api/agent/config",
            headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
            json={"is_enabled": True},
        )
    finally:
        app.dependency_overrides.pop(get_telephony_provider, None)

    config = await fetch_agent_config(client_database_url, clerk_user_id="user_agent_cfg")
    phone_number = await fetch_phone_number(client_database_url, clerk_user_id="user_agent_cfg")

    assert response.status_code == 502
    assert response.json()["detail"] == "Failed to update telephony state"
    assert config.is_enabled is False
    assert phone_number.is_active is False
    assert phone_number.provider_connection_name == "app-disabled"
