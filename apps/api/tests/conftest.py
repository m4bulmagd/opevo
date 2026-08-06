import base64
import hashlib
import hmac
import json
import os
import time
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base


DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_call_test"
)
DEFAULT_TEST_REDIS_URL = "redis://localhost:6379/0"
TEST_CLERK_AUTHORIZED_PARTY = "https://app.example.com"
TEST_CLERK_ISSUER = "https://clerk.example.com"
TEST_CLERK_JWKS_URL = "https://clerk.example.com/.well-known/jwks.json"
TEST_CLERK_WEBHOOK_SECRET_BYTES = b"test-webhook-secret"
TEST_CLERK_WEBHOOK_SECRET = "whsec_" + base64.b64encode(
    TEST_CLERK_WEBHOOK_SECRET_BYTES
).decode("utf-8")
TEST_DISPATCH_JWT_SECRET = "shared-test-dispatch-secret-with-at-least-32-bytes"


def install_test_api_runtime(
    application,
    *,
    settings=None,
    auth_provider=None,
    readiness_checks=None,
    observability=None,
    arq_pool=None,
    call_finalization_queue=None,
    realtime_service=None,
    livekit_webhook_receiver=None,
):
    from app.composition.lifecycle import RuntimeCleanup
    from app.composition.runtime import ApiRuntime
    from app.core.config import get_settings
    from app.core.observability import get_observability

    runtime = ApiRuntime(
        settings=settings or get_settings(),
        engine=object(),
        session_factory=object(),
        redis_client=object(),
        observability=observability or get_observability(),
        auth_provider=auth_provider or object(),
        readiness_checks=readiness_checks or object(),
        storage_provider=object(),
        arq_pool=arq_pool,
        call_finalization_queue=call_finalization_queue,
        realtime_service=realtime_service,
        livekit_webhook_receiver=livekit_webhook_receiver,
        livekit_recording_service=None,
        _cleanup=RuntimeCleanup(AsyncExitStack()),
    )
    application.state.runtime = runtime
    return runtime


def _construction_settings_environment() -> dict[str, str]:
    return {
        "APP_ENV": "test",
        "DATABASE_URL": os.environ.get(
            "TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL
        ),
        "REDIS_URL": os.environ.get("TEST_REDIS_URL", DEFAULT_TEST_REDIS_URL),
        "WORKER_LIFECYCLE_MAX_JOBS": "10",
        "WORKER_BACKGROUND_MAX_JOBS": "4",
        "REALTIME_ENABLED": "false",
        "ACTIVATION_FLOW_ENABLED": "false",
        "AUTH_MODE": "clerk",
        "CLERK_ISSUER": TEST_CLERK_ISSUER,
        "CLERK_AUDIENCE": "",
        "CLERK_AUTHORIZED_PARTIES": TEST_CLERK_AUTHORIZED_PARTY,
        "CLERK_JWKS_URL": TEST_CLERK_JWKS_URL,
        "CLERK_WEBHOOK_SECRET": TEST_CLERK_WEBHOOK_SECRET,
        "STRIPE_WEBHOOK_SECRET": "test-stripe-secret",
        "AGENT_DISPATCH_JWT_SECRET": TEST_DISPATCH_JWT_SECRET,
    }


def _replace_controlled_settings_environment(
    values: dict[str, str],
    *,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> None:
    controlled_names = {*_construction_settings_environment(), "CLERK_JWT_KEY"}
    for inherited_name in list(os.environ):
        if inherited_name.upper() not in controlled_names:
            continue
        if monkeypatch is None:
            os.environ.pop(inherited_name)
        else:
            monkeypatch.delenv(inherited_name)

    for name, value in values.items():
        if monkeypatch is None:
            os.environ[name] = value
        else:
            monkeypatch.setenv(name, value)


def pytest_configure(config: pytest.Config) -> None:
    _replace_controlled_settings_environment(_construction_settings_environment())


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
    webhook_secret_bytes = TEST_CLERK_WEBHOOK_SECRET_BYTES
    webhook_secret = TEST_CLERK_WEBHOOK_SECRET
    return {
        "private_key_pem": private_key_pem,
        "public_key_pem": public_key_pem,
        "webhook_secret": webhook_secret,
        "webhook_secret_bytes": webhook_secret_bytes,
    }


@pytest.fixture(autouse=True)
def settings_env(monkeypatch: pytest.MonkeyPatch, clerk_key_material: dict[str, str | bytes]):
    function_environment = _construction_settings_environment()
    function_environment.update(
        {
            "CLERK_JWT_KEY": str(clerk_key_material["public_key_pem"]),
            "CLERK_JWKS_URL": "",
        }
    )
    _replace_controlled_settings_environment(
        function_environment,
        monkeypatch=monkeypatch,
    )

    from app.core.config import Settings, get_settings
    from app.core.rate_limit import configure_rate_limiter, limiter

    previous_limiter_enabled = limiter.enabled
    get_settings.cache_clear()
    configure_rate_limiter(Settings())
    yield
    limiter.enabled = previous_limiter_enabled
    get_settings.cache_clear()


@pytest.fixture
def settings():
    from app.core.config import Settings

    return Settings()


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


async def _initialize_test_database(
    database_url: str,
    *,
    engine_factory=None,
) -> None:
    if engine_factory is None:
        from app.core.database import create_database_engine

        engine_factory = create_database_engine

    engine = engine_factory(database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    except BaseException as setup_error:
        try:
            await engine.dispose()
        except BaseException as cleanup_error:
            raise setup_error from cleanup_error
        raise
    await engine.dispose()


@pytest_asyncio.fixture
async def test_app(tmp_path: Path, settings):
    from app import main as main_module

    original_app = main_module.app
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'test_client.db'}"
    await _initialize_test_database(database_url)

    app = main_module.create_app(
        settings.model_copy(update={"database_url": database_url})
    )
    try:
        main_module.app = app
        async with app.router.lifespan_context(app):
            yield app
    finally:
        app.dependency_overrides.clear()
        main_module.app = original_app


@pytest_asyncio.fixture
async def configured_livekit_recording_runtime(test_app):
    class RecordingServiceFake:
        pass

    runtime = test_app.state.runtime
    previous = runtime.livekit_recording_service
    runtime.livekit_recording_service = RecordingServiceFake()
    try:
        yield runtime.livekit_recording_service
    finally:
        runtime.livekit_recording_service = previous


@pytest_asyncio.fixture
async def async_client(test_app):
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def client_database_url(test_app) -> str:
    return test_app.state.runtime.settings.database_url


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

    def _build(
        clerk_user_id: str,
        *,
        claims: dict[str, object] | None = None,
        headers: dict[str, object] | None = None,
    ) -> str:
        payload: dict[str, object] = {
            "sub": clerk_user_id,
            "iss": "https://clerk.example.com",
            "exp": 4102444800,
            "nbf": 0,
            "azp": TEST_CLERK_AUTHORIZED_PARTY,
        }
        if claims:
            payload.update(claims)
        return jwt.encode(
            payload,
            private_key_pem,
            algorithm="RS256",
            headers=headers,
        )

    return _build


@pytest.fixture
def valid_clerk_but_missing_local_user_token(rs256_clerk_token_for) -> str:
    return rs256_clerk_token_for("user_missing")




@pytest.fixture
def stripe_subscription_created_payload() -> dict:
    return {
        "id": "evt_sub_created_123",
        "created": 1710000100,
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "id": "sub_123",
                "created": 1709990000,
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
        "created": 1710000100,
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "id": "sub_123",
                "created": 1709990000,
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
        "created": 1710000200,
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
