import pytest


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
        result = await session.execute(select(User).where(User.clerk_user_id == clerk_user_created_payload["data"]["id"]))
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
                User.clerk_user_id == clerk_user_created_payload["data"]["id"]
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
