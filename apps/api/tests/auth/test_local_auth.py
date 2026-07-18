import asyncio
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.auth import LocalAuthProvider
from app.core.config import Settings
from app.core.database import get_session
from app.main import create_app
from app.models import Base
from app.models.agent_config import AgentConfig
from app.models.business_profile import BusinessProfile
from app.models.customer_activation import CustomerActivation
from app.models.user import User


LOCAL_TOKEN = "presvo-local-development-token"
LOCAL_EXTERNAL_USER_ID = "local_presvo_user"
LOCAL_EMAIL = "local@presvo.invalid"


@pytest_asyncio.fixture
async def bootstrap_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Concurrent bootstrap test requires TEST_DATABASE_URL")
    if not database_url.startswith(("postgresql+asyncpg://", "postgresql://")):
        pytest.skip("TEST_DATABASE_URL must identify a PostgreSQL database")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )

    schema_name = f"task5_bootstrap_{uuid4().hex}"
    quoted_schema = f'"{schema_name}"'
    admin_engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    test_engine = None
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))

        test_engine = create_async_engine(
            database_url,
            connect_args={"server_settings": {"search_path": schema_name}},
        )
        async with test_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        yield async_sessionmaker(test_engine, expire_on_commit=False)
    finally:
        if test_engine is not None:
            await test_engine.dispose()
        async with admin_engine.connect() as connection:
            await connection.execute(
                text(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE")
            )
        await admin_engine.dispose()


@pytest.fixture
def local_settings() -> Settings:
    return Settings(
        app_env="development",
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
        auth_mode="local",
        local_auth_token=LOCAL_TOKEN,
    )


@pytest_asyncio.fixture
async def local_client(
    tmp_path: Path,
    local_settings: Settings,
) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'local_auth.db'}"
    engine = create_async_engine(database_url, future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    application = create_app(local_settings)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        request_engine = create_async_engine(database_url, future=True)
        session_factory = async_sessionmaker(request_engine, expire_on_commit=False)
        async with session_factory() as session:
            yield session
        await request_engine.dispose()

    application.dependency_overrides[get_session] = override_get_session
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client, database_url


def test_local_auth_accepts_only_exact_configured_token() -> None:
    provider = LocalAuthProvider(token=LOCAL_TOKEN)

    identity = provider.verify_token(LOCAL_TOKEN)

    assert identity.clerk_user_id == LOCAL_EXTERNAL_USER_ID
    for rejected_token in ("", "wrong-local-token", "é"):
        with pytest.raises(HTTPException) as error:
            provider.verify_token(rejected_token)
        assert error.value.status_code == 401
        message = str(error.value.detail)
        assert LOCAL_TOKEN not in message
        if rejected_token:
            assert rejected_token not in message


@pytest.mark.anyio
async def test_local_auth_rejects_missing_and_wrong_bearer_without_leaking_tokens(
    local_client: tuple[httpx.AsyncClient, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _ = local_client
    wrong_token = "wrong-local-token-sentinel"

    with caplog.at_level(logging.WARNING):
        missing = await client.get("/api/agent/config")
        wrong = await client.get(
            "/api/agent/config",
            headers={"Authorization": f"Bearer {wrong_token}"},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert LOCAL_TOKEN not in missing.text + wrong.text + caplog.text
    assert wrong_token not in wrong.text + caplog.text


async def _load_local_aggregate(database_url: str) -> tuple[User | None, dict[str, int]]:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = await session.scalar(
            select(User).where(User.clerk_user_id == LOCAL_EXTERNAL_USER_ID)
        )
        if user is None:
            counts = {
                "user": 0,
                "agent_config": 0,
                "business_profile": 0,
                "customer_activation": 0,
            }
        else:
            counts = {
                "user": int(
                    await session.scalar(
                        select(func.count(User.id)).where(
                            User.clerk_user_id == LOCAL_EXTERNAL_USER_ID
                        )
                    )
                    or 0
                ),
                "agent_config": int(
                    await session.scalar(
                        select(func.count(AgentConfig.id)).where(
                            AgentConfig.user_id == user.id
                        )
                    )
                    or 0
                ),
                "business_profile": int(
                    await session.scalar(
                        select(func.count(BusinessProfile.id)).where(
                            BusinessProfile.user_id == user.id
                        )
                    )
                    or 0
                ),
                "customer_activation": int(
                    await session.scalar(
                        select(func.count(CustomerActivation.id)).where(
                            CustomerActivation.user_id == user.id
                        )
                    )
                    or 0
                ),
            }
    await engine.dispose()
    return user, counts


@pytest.mark.anyio
async def test_first_local_request_durably_bootstraps_once_and_repeat_is_idempotent(
    local_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, database_url = local_client
    headers = {
        "Authorization": f"Bearer {LOCAL_TOKEN}",
        "X-User-Id": "caller_selected_user_must_be_ignored",
    }

    first = await client.get(
        "/api/agent/config?user_id=caller_selected_user_must_be_ignored",
        headers=headers,
    )

    assert first.status_code == 200
    first_user, first_counts = await _load_local_aggregate(database_url)
    assert first_user is not None
    assert first_user.clerk_user_id == LOCAL_EXTERNAL_USER_ID
    assert first_user.email == LOCAL_EMAIL
    assert first_counts == {
        "user": 1,
        "agent_config": 1,
        "business_profile": 1,
        "customer_activation": 1,
    }

    repeat = await client.get("/api/agent/config", headers=headers)

    assert repeat.status_code == 200
    repeat_user, repeat_counts = await _load_local_aggregate(database_url)
    assert repeat_user is not None
    assert repeat_user.id == first_user.id
    assert repeat_counts == first_counts


@pytest.mark.anyio
async def test_clerk_mode_rejects_local_token_and_does_not_bootstrap_local_user(
    async_client: httpx.AsyncClient,
    client_database_url: str,
) -> None:
    response = await async_client.get(
        "/api/agent/config",
        headers={"Authorization": f"Bearer {LOCAL_TOKEN}"},
    )

    assert response.status_code == 401
    user, counts = await _load_local_aggregate(client_database_url)
    assert user is None
    assert counts == {
        "user": 0,
        "agent_config": 0,
        "business_profile": 0,
        "customer_activation": 0,
    }


@pytest.mark.anyio
async def test_concurrent_first_bootstrap_creates_one_complete_aggregate(
    bootstrap_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.services.user_bootstrap_service import UserBootstrapService

    suffix = uuid4().hex
    external_user_id = f"concurrent_bootstrap_{suffix}"
    email = f"concurrent_bootstrap_{suffix}@example.com"
    start = asyncio.Event()

    async def bootstrap() -> User:
        async with bootstrap_session_factory() as session:
            await start.wait()
            user = await UserBootstrapService(session).ensure_user(
                external_user_id=external_user_id,
                email=email,
            )
            await session.commit()
            return user

    first_task = asyncio.create_task(bootstrap())
    second_task = asyncio.create_task(bootstrap())
    start.set()
    first, second = await asyncio.wait_for(
        asyncio.gather(first_task, second_task),
        timeout=3,
    )

    assert first.id == second.id
    async with bootstrap_session_factory() as session:
        user = await session.scalar(
            select(User).where(User.clerk_user_id == external_user_id)
        )
        assert user is not None
        counts = {
            "user": await session.scalar(
                select(func.count(User.id)).where(
                    User.clerk_user_id == external_user_id
                )
            ),
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

    assert counts == {
        "user": 1,
        "agent_config": 1,
        "business_profile": 1,
        "customer_activation": 1,
    }
