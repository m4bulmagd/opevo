from unittest.mock import AsyncMock, patch

import pytest

from app.auth.domain import ExternalUserProfile
from app.services.auth_service import AuthService
from app.services.user_provisioning import UserProvisioning


@pytest.mark.anyio
async def test_clerk_user_created_webhook_upserts_local_user(
    async_client,
    signed_clerk_headers,
    clerk_user_created_payload,
    clerk_user_created_payload_bytes,
    client_database_url,
) -> None:
    response = await async_client.post(
        "/webhooks/clerk",
        content=clerk_user_created_payload_bytes,
        headers=signed_clerk_headers,
    )

    assert response.status_code == 202

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.agent_config import AgentConfig
    from app.models.user import User

    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(select(User).where(User.external_user_id == clerk_user_created_payload["data"]["id"]))
        user = result.scalar_one_or_none()
        config = (
            await session.execute(select(AgentConfig).where(AgentConfig.user_id == user.id))
        ).scalar_one_or_none()
        
    await engine.dispose()
    
    assert user is not None
    assert user.email == clerk_user_created_payload["data"]["email_addresses"][0]["email_address"]
    assert config is not None
    assert config.pipeline_mode == "stt_llm_tts"
    assert config.is_enabled is False


@pytest.mark.anyio
async def test_clerk_user_created_bootstraps_activation_aggregate_idempotently(
    async_client,
    signed_clerk_headers,
    clerk_user_created_payload,
    clerk_user_created_payload_bytes,
    client_database_url,
) -> None:
    first = await async_client.post(
        "/webhooks/clerk",
        content=clerk_user_created_payload_bytes,
        headers=signed_clerk_headers,
    )
    duplicate = await async_client.post(
        "/webhooks/clerk",
        content=clerk_user_created_payload_bytes,
        headers=signed_clerk_headers,
    )

    assert first.status_code == 202
    assert duplicate.status_code == 202

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.agent_config import AgentConfig
    from app.models.business_profile import BusinessProfile
    from app.models.customer_activation import CustomerActivation
    from app.models.user import User

    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = await session.scalar(
            select(User).where(
                User.external_user_id == clerk_user_created_payload["data"]["id"]
            )
        )
        assert user is not None
        aggregate_counts = {
            "agent_config": await session.scalar(
                select(func.count(AgentConfig.id)).where(
                    AgentConfig.user_id == user.id
                )
            ),
            "business_profile": await session.scalar(
                select(func.count(BusinessProfile.id)).where(
                    BusinessProfile.user_id == user.id
                )
            ),
            "customer_activation": await session.scalar(
                select(func.count(CustomerActivation.id)).where(
                    CustomerActivation.user_id == user.id
                )
            ),
        }
    await engine.dispose()

    assert aggregate_counts == {
        "agent_config": 1,
        "business_profile": 1,
        "customer_activation": 1,
    }


@pytest.mark.anyio
async def test_shared_user_bootstrap_flushes_without_committing(db_session) -> None:
    service = UserProvisioning(db_session)

    commit = AsyncMock(wraps=db_session.commit)
    flush = AsyncMock(wraps=db_session.flush)
    with (
        patch.object(db_session, "commit", commit),
        patch.object(db_session, "flush", flush),
    ):
        user = await service.ensure_user(ExternalUserProfile(
            external_user_id="shared_bootstrap_user",
            email="shared@example.com",
        ))

    assert user.external_user_id == "shared_bootstrap_user"
    flush.assert_awaited()
    commit.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["deactivating", "inactive"])
async def test_clerk_resynchronization_preserves_existing_account_status(
    db_session,
    status: str,
) -> None:
    service = UserProvisioning(db_session)
    user = await service.ensure_user(ExternalUserProfile(
        external_user_id=f"resync_{status}",
        email=f"resync-{status}@example.invalid",
    ))
    user.status = status
    await db_session.commit()

    resynchronized = await service.ensure_user(
        ExternalUserProfile(
            external_user_id=user.external_user_id,
            email=user.email,
        )
    )
    await db_session.commit()

    assert resynchronized.id == user.id
    assert resynchronized.status == status


@pytest.mark.anyio
async def test_clerk_sync_delegates_once_and_keeps_one_final_commit_per_event(
    db_session,
    clerk_user_created_payload,
) -> None:
    service = AuthService(db_session)
    provisioning = getattr(service, "user_provisioning", None)
    assert provisioning is not None, "AuthService must delegate to UserProvisioning"
    provisioning.ensure_user = AsyncMock()
    commit = AsyncMock(wraps=db_session.commit)

    with patch.object(db_session, "commit", commit):
        profile = ExternalUserProfile(
            external_user_id=clerk_user_created_payload["data"]["id"],
            email=clerk_user_created_payload["data"]["email_addresses"][0][
                "email_address"
            ],
        )
        first = await service.provision_user_from_event(
            profile=profile,
            provider="clerk",
            payload=clerk_user_created_payload,
            event_id="evt_shared_bootstrap",
            event_type="user.created",
        )
        duplicate = await service.provision_user_from_event(
            profile=profile,
            provider="clerk",
            payload=clerk_user_created_payload,
            event_id="evt_shared_bootstrap",
            event_type="user.created",
        )

    assert first is True
    assert duplicate is False
    provisioning.ensure_user.assert_awaited_once_with(profile)
    assert commit.await_count == 2
