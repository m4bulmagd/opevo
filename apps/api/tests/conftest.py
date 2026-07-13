import base64
import hashlib
import hmac
import json
import time
from pathlib import Path

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base


@pytest.fixture
def clerk_key_material() -> dict[str, str | bytes]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    webhook_secret_bytes = b"test-webhook-secret"
    webhook_secret = "whsec_" + base64.b64encode(webhook_secret_bytes).decode("utf-8")
    return {
        "private_key_pem": private_key_pem,
        "public_key_pem": public_key_pem,
        "webhook_secret": webhook_secret,
        "webhook_secret_bytes": webhook_secret_bytes,
    }


@pytest.fixture(autouse=True)
def settings_env(monkeypatch: pytest.MonkeyPatch, clerk_key_material: dict[str, str | bytes]):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_call_test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.example.com")
    monkeypatch.setenv("CLERK_AUDIENCE", "")
    monkeypatch.setenv("CLERK_JWT_KEY", str(clerk_key_material["public_key_pem"]))
    monkeypatch.setenv("CLERK_WEBHOOK_SECRET", str(clerk_key_material["webhook_secret"]))
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "test-stripe-secret")
    monkeypatch.setenv("AGENT_INTERNAL_API_TOKEN", "test-agent-token")

    from app.core.config import get_settings
    from app.core.database import get_engine, get_session_factory
    from app.core.redis import get_redis_client

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    get_redis_client.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    get_redis_client.cache_clear()


@pytest.fixture
def settings():
    from app.core.config import get_settings

    get_settings.cache_clear()
    return get_settings()


@pytest_asyncio.fixture
async def db_session(tmp_path: Path) -> AsyncSession:
    database_path = tmp_path / "unit_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}", future=True)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session
        await session.rollback()

    await engine.dispose()


@pytest_asyncio.fixture
async def active_user(db_session: AsyncSession):
    from app.repositories.user_repository import UserRepository

    user = await UserRepository(db_session).create(
        clerk_user_id="user_active",
        email="active@example.com",
    )
    await db_session.commit()
    return user


@pytest_asyncio.fixture
async def test_app(tmp_path: Path):
    from app.core.database import get_session
    from app.main import app

    database_path = tmp_path / "test_client.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"

    async def setup_database() -> None:
        engine = create_async_engine(database_url, future=True)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    await setup_database()
    app.state.test_database_url = database_url

    async def override_get_session():
        engine = create_async_engine(database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            yield session
        await engine.dispose()

    app.dependency_overrides[get_session] = override_get_session

    async with app.router.lifespan_context(app):
        yield app

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def async_client(test_app):
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def client_database_url(test_app) -> str:
    return test_app.state.test_database_url


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
def signed_clerk_headers(
    clerk_user_created_payload_bytes: bytes,
    clerk_key_material: dict[str, str | bytes],
) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signed_content = b"evt_test_123." + timestamp.encode("utf-8") + b"." + clerk_user_created_payload_bytes
    digest = hmac.new(
        clerk_key_material["webhook_secret_bytes"],
        signed_content,
        hashlib.sha256,
    ).digest()

    return {
        "svix-id": "evt_test_123",
        "svix-timestamp": timestamp,
        "svix-signature": f"v1,{base64.b64encode(digest).decode('utf-8')}",
        "content-type": "application/json",
    }


@pytest.fixture
def rs256_clerk_token_for(clerk_key_material: dict[str, str | bytes]):
    private_key_pem = str(clerk_key_material["private_key_pem"])

    def _build(clerk_user_id: str) -> str:
        return jwt.encode(
            {
                "sub": clerk_user_id,
                "iss": "https://clerk.example.com",
                "exp": 4102444800,
            },
            private_key_pem,
            algorithm="RS256",
        )

    return _build


@pytest.fixture
def valid_clerk_but_missing_local_user_token(rs256_clerk_token_for) -> str:
    return rs256_clerk_token_for("user_missing")




@pytest.fixture
def stripe_subscription_created_payload() -> dict:
    return {
        "id": "evt_sub_created_123",
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "id": "sub_123",
                "customer": "cus_123",
                "status": "active",
                "metadata": {"clerk_user_id": "user_123"},
                "items": {
                    "data": [
                        {
                            "price": {
                                "id": "price_starter",
                                "lookup_key": "starter",
                            }
                        }
                    ]
                },
                "current_period_start": 1710000000,
                "current_period_end": 1712592000,
            }
        },
    }


@pytest.fixture
def stripe_current_subscription_created_payload() -> dict:
    return {
        "id": "evt_sub_created_current_123",
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "id": "sub_123",
                "customer": "cus_123",
                "status": "active",
                "metadata": {"clerk_user_id": "user_123"},
                "items": {
                    "data": [
                        {
                            "current_period_start": 1710000000,
                            "current_period_end": 1712592000,
                            "price": {
                                "id": "price_starter",
                                "lookup_key": "starter",
                            },
                        }
                    ]
                },
                "billing_cycle_anchor": 1710000000,
            }
        },
    }


@pytest.fixture
def stripe_invoice_paid_payload() -> dict:
    return {
        "id": "evt_invoice_paid_123",
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": "in_123",
                "customer": "cus_123",
                "status": "paid",
                "paid": True,
                "parent": {
                    "subscription_details": {
                        "subscription": "sub_123",
                    }
                },
                "lines": {
                    "data": [
                        {
                            "parent": {
                                "subscription_item_details": {
                                    "subscription": "sub_123",
                                    "subscription_item": "si_123",
                                }
                            },
                            "pricing": {
                                "price_details": {
                                    "price": "price_standard",
                                    "product": "prod_123",
                                }
                            }
                        }
                    ]
                },
            }
        },
    }


@pytest.fixture
def signed_stripe_headers_factory():
    def _build(payload: dict, *, timestamp: int | None = None) -> dict[str, str]:
        if timestamp is None:
            timestamp = int(time.time())
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        signed_payload = f"{timestamp}.".encode("utf-8") + payload_bytes
        digest = hmac.new(b"test-stripe-secret", signed_payload, hashlib.sha256).hexdigest()
        return {
            "stripe-signature": f"t={timestamp},v1={digest}",
            "content-type": "application/json",
        }

    return _build
