"""Cross-tenant authorization tests.

Verify that authenticated users cannot access or mutate other users' data.
Each test seeds data for "user B", authenticates as "user A", and asserts
that the response contains only user A's data (or a 404 when the resource
belongs to user B).
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_config import AgentConfig
from app.models.call import Call
from app.models.subscription import Subscription
from app.models.user import User


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _create_user(session, *, external_user_id: str, email: str) -> User:
    user = User(external_user_id=external_user_id, email=email)
    session.add(user)
    await session.flush()
    return user


async def seed_call_for_user(
    database_url: str,
    *,
    external_user_id: str,
    email: str,
) -> UUID:
    """Create a user with one completed call; return the call id."""
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = await _create_user(session, external_user_id=external_user_id, email=email)
        call = Call(
            user_id=user.id,
            caller_number="+33100000001",
            status="completed",
            started_at=datetime(2026, 3, 28, 10, 0, tzinfo=UTC),
            ended_at=datetime(2026, 3, 28, 10, 1, tzinfo=UTC),
            duration_seconds=60,
            minutes_charged=1,
            summary_text="Test call summary",
        )
        session.add(call)
        await session.commit()
        call_id = call.id
    await engine.dispose()
    return call_id


async def seed_subscription_for_user(
    database_url: str,
    *,
    external_user_id: str,
    email: str,
) -> None:
    """Create a user with an active subscription."""
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = await _create_user(session, external_user_id=external_user_id, email=email)
        subscription = Subscription(
            user_id=user.id,
            plan_tier="starter",
            status="active",
            allocated_minutes=60,
            stripe_customer_id="cus_userB",
            stripe_subscription_id="sub_userB",
            current_period_start=datetime(2026, 3, 1, tzinfo=UTC),
            current_period_end=datetime(2026, 4, 1, tzinfo=UTC),
        )
        session.add(subscription)
        await session.commit()
    await engine.dispose()


async def seed_agent_config_for_user(
    database_url: str,
    *,
    external_user_id: str,
    email: str,
    agent_name: str = "Assistant",
) -> None:
    """Create a user with an agent config."""
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = await _create_user(session, external_user_id=external_user_id, email=email)
        session.add(
            AgentConfig(
                user_id=user.id,
                agent_name=agent_name,
                system_prompt="Be helpful",
                knowledge_base="Open 9-5",
                pipeline_mode="stt_llm_tts",
                is_enabled=False,
            )
        )
        await session.commit()
    await engine.dispose()


async def seed_bare_user(
    database_url: str,
    *,
    external_user_id: str,
    email: str,
) -> None:
    """Create a user with no associated data (user A in cross-tenant tests)."""
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(external_user_id=external_user_id, email=email))
        await session.commit()
    await engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_call_detail_returns_404_for_other_users_call(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    """User A must not read User B's call — response is 404, not 403, to
    avoid leaking the existence of the resource."""
    # Seed user A (no calls) and user B with a call.
    await seed_bare_user(
        client_database_url,
        external_user_id="cross_tenant_user_a",
        email="user_a@example.com",
    )
    user_b_call_id = await seed_call_for_user(
        client_database_url,
        external_user_id="cross_tenant_user_b",
        email="user_b@example.com",
    )

    response = await async_client.get(
        f"/api/calls/{user_b_call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('cross_tenant_user_a')}"},
    )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_subscription_returns_null_not_other_users_subscription(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    """User A must not see User B's subscription data.
    The endpoint scopes queries by the authenticated identity, so user A
    gets null (no subscription) even though user B has an active one."""
    # Seed user A (no subscription) and user B with an active subscription.
    await seed_bare_user(
        client_database_url,
        external_user_id="cross_tenant_user_a",
        email="user_a@example.com",
    )
    await seed_subscription_for_user(
        client_database_url,
        external_user_id="cross_tenant_user_b",
        email="user_b@example.com",
    )

    response = await async_client.get(
        "/api/billing/subscription",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('cross_tenant_user_a')}"},
    )

    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.anyio
async def test_patch_agent_config_does_not_affect_other_users_config(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    """A PATCH by user A must only modify user A's config; user B's config
    must remain unchanged.  The endpoint resolves config ownership from the
    JWT identity, so user A can never write to user B's row."""
    # Seed user A with their own config and user B with a distinct config.
    await seed_agent_config_for_user(
        client_database_url,
        external_user_id="cross_tenant_user_a",
        email="user_a@example.com",
        agent_name="AgentA",
    )
    await seed_agent_config_for_user(
        client_database_url,
        external_user_id="cross_tenant_user_b",
        email="user_b@example.com",
        agent_name="AgentB",
    )

    # User A updates their own agent name.
    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('cross_tenant_user_a')}"},
        json={"agent_name": "AgentA_updated"},
    )

    assert response.status_code == 200
    assert response.json()["agent_name"] == "AgentA_updated"

    # Now read user B's config — it must be unchanged.
    response_b = await async_client.get(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('cross_tenant_user_b')}"},
    )

    assert response_b.status_code == 200
    assert response_b.json()["agent_name"] == "AgentB"
