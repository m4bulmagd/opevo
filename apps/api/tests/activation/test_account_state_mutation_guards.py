from collections.abc import Mapping

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.activation_event import ActivationEvent
from app.models.agent_config import AgentConfig
from app.models.business_profile import BusinessProfile
from app.models.customer_activation import CustomerActivation
from app.models.outbox_event import OutboxEvent
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.user import User


MUTATION_ROUTES = (
    ("PUT", "/api/business-profile", {}),
    ("POST", "/api/activation/confirm-profile", None),
    ("PATCH", "/api/agent/config", {"pipeline_mode": "sts"}),
    ("POST", "/api/activation/lookup-carrier", None),
    ("POST", "/api/activation/confirm-provisioning", None),
    ("POST", "/api/activation/retry-provisioning", None),
    ("POST", "/api/activation/open-verification-window", None),
    ("POST", "/api/activation/go-live", None),
)


async def _seed_blocked_user(
    database_url: str,
    *,
    status: str,
) -> tuple[str, str]:
    clerk_user_id = f"blocked_{status}"
    email = f"blocked-{status}@example.invalid"
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(
                clerk_user_id=clerk_user_id,
                email=email,
                status=status,
            )
        )
        await session.commit()
    await engine.dispose()
    return clerk_user_id, email


async def _mutation_counts(database_url: str) -> Mapping[str, int]:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    models = {
        "profile": BusinessProfile,
        "activation": CustomerActivation,
        "agent_config": AgentConfig,
        "provisioning": PhoneNumberProvisioning,
        "activation_event": ActivationEvent,
        "outbox": OutboxEvent,
    }
    async with session_factory() as session:
        counts = {
            name: int(await session.scalar(select(func.count(model.id))) or 0)
            for name, model in models.items()
        }
    await engine.dispose()
    return counts


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("deactivating", "account_deactivating"),
        ("inactive", "account_inactive"),
    ],
)
@pytest.mark.parametrize(("method", "path", "payload"), MUTATION_ROUTES)
async def test_blocked_owner_mutation_returns_stable_conflict_without_writes(
    async_client,
    client_database_url: str,
    rs256_clerk_token_for,
    status: str,
    code: str,
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    clerk_user_id, _email = await _seed_blocked_user(
        client_database_url,
        status=status,
    )
    before = await _mutation_counts(client_database_url)

    response = await async_client.request(
        method,
        path,
        headers={
            "Authorization": f"Bearer {rs256_clerk_token_for(clerk_user_id)}"
        },
        json=payload,
    )

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": code}}
    assert await _mutation_counts(client_database_url) == before


@pytest.mark.anyio
async def test_inactive_owner_can_read_billing_and_account_state(
    async_client,
    client_database_url: str,
    rs256_clerk_token_for,
) -> None:
    clerk_user_id, _email = await _seed_blocked_user(
        client_database_url,
        status="inactive",
    )
    headers = {
        "Authorization": f"Bearer {rs256_clerk_token_for(clerk_user_id)}"
    }

    account = await async_client.get("/api/account", headers=headers)
    subscription = await async_client.get(
        "/api/billing/subscription",
        headers=headers,
    )
    usage = await async_client.get("/api/billing/usage", headers=headers)

    assert account.status_code == 200
    assert account.json()["status"] == "inactive"
    assert subscription.status_code == 200
    assert subscription.json() is None
    assert usage.status_code == 200
