import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base


@pytest.fixture(autouse=True)
def settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    jwt_secret = "test-jwt-secret-with-at-least-32-bytes"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_call_test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.example.com")
    monkeypatch.setenv("CLERK_AUDIENCE", "ai-call-assistant")
    monkeypatch.setenv("CLERK_JWT_SECRET", jwt_secret)
    monkeypatch.setenv("CLERK_WEBHOOK_SECRET", "test-webhook-secret")


@pytest.fixture
def settings():
    from app.core.config import get_settings

    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def client() -> TestClient:
    from app.core.database import get_session
    from app.main import app

    raise RuntimeError("The client fixture now requires db_session and is provided below.")


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session
        await session.rollback()

    await engine.dispose()


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    from app.core.database import get_session
    from app.main import app

    database_path = tmp_path / "test_client.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"

    async def setup_database() -> None:
        engine = create_async_engine(database_url, future=True)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    import asyncio

    asyncio.run(setup_database())

    async def override_get_session():
        engine = create_async_engine(database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            yield session
        await engine.dispose()

    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def clerk_user_created_payload() -> dict:
    return {
        "type": "user.created",
        "data": {
            "id": "user_123",
            "email_addresses": [
                {"email_address": "test@example.com"},
            ],
        },
    }


@pytest.fixture
def clerk_user_created_payload_bytes(clerk_user_created_payload: dict) -> bytes:
    return json.dumps(clerk_user_created_payload, separators=(",", ":")).encode("utf-8")


@pytest.fixture
def signed_clerk_headers(clerk_user_created_payload_bytes: bytes) -> dict[str, str]:
    secret = b"test-webhook-secret"
    digest = hmac.new(secret, clerk_user_created_payload_bytes, hashlib.sha256).digest()

    return {
        "svix-id": "evt_test_123",
        "svix-timestamp": "1710000000",
        "svix-signature": base64.b64encode(digest).decode("utf-8"),
        "content-type": "application/json",
    }


@pytest.fixture
def valid_clerk_but_missing_local_user_token() -> str:
    import jwt

    return jwt.encode(
        {
            "sub": "user_missing",
            "iss": "https://clerk.example.com",
            "aud": "ai-call-assistant",
            "exp": 4102444800,
        },
        "test-jwt-secret-with-at-least-32-bytes",
        algorithm="HS256",
    )
