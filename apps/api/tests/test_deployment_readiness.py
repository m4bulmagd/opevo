import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.core.config import Settings
from app.core.runtime_validation import validate_api_runtime


REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def base_settings() -> Settings:
    return Settings(
        app_env="production",
        database_url="postgresql+asyncpg://db/ai_call",
        redis_url="rediss://redis/0",
        clerk_issuer="https://clerk.example.com",
        clerk_jwks_url="https://clerk.example.com/.well-known/jwks.json",
        clerk_webhook_secret="clerk-webhook-secret",
        stripe_secret_key="stripe-secret-key",
        stripe_webhook_secret="stripe-webhook-secret",
        stripe_price_starter="stripe-starter-price",
        stripe_checkout_success_url="https://app.example.com/billing/success",
        stripe_checkout_cancel_url="https://app.example.com/billing/cancel",
        stripe_billing_portal_return_url="https://app.example.com/dashboard/billing",
        livekit_url="wss://livekit.example.com",
        livekit_api_key="livekit-api-key",
        livekit_api_secret="livekit-api-secret",
        telnyx_api_key="telnyx-api-key",
        telnyx_active_connection_id="telnyx-active-connection",
        telnyx_disabled_connection_id="telnyx-disabled-connection",
        storage_bucket_name="recordings",
        s3_endpoint_url="https://storage.example.com",
        s3_access_key="storage-access-key",
        s3_secret_key="storage-secret-key",
        s3_region="eu-west-3",
        agent_dispatch_jwt_secret="dispatch-jwt-secret",
        summary_provider="gemini",
        summary_model="gemini-2.5-flash",
        gemini_api_key="gemini-api-key",
    )


@pytest.mark.parametrize(
    ("field_name", "environment_name"),
    [
        ("database_url", "DATABASE_URL"),
        ("redis_url", "REDIS_URL"),
        ("clerk_issuer", "CLERK_ISSUER"),
        ("clerk_webhook_secret", "CLERK_WEBHOOK_SECRET"),
        ("stripe_secret_key", "STRIPE_SECRET_KEY"),
        ("stripe_webhook_secret", "STRIPE_WEBHOOK_SECRET"),
        ("stripe_price_starter", "STRIPE_PRICE_STARTER"),
        ("stripe_checkout_success_url", "STRIPE_CHECKOUT_SUCCESS_URL"),
        ("stripe_checkout_cancel_url", "STRIPE_CHECKOUT_CANCEL_URL"),
        ("stripe_billing_portal_return_url", "STRIPE_BILLING_PORTAL_RETURN_URL"),
        ("livekit_url", "LIVEKIT_URL"),
        ("livekit_api_key", "LIVEKIT_API_KEY"),
        ("livekit_api_secret", "LIVEKIT_API_SECRET"),
        ("telnyx_api_key", "TELNYX_API_KEY"),
        ("telnyx_active_connection_id", "TELNYX_ACTIVE_CONNECTION_ID"),
        ("telnyx_disabled_connection_id", "TELNYX_DISABLED_CONNECTION_ID"),
        ("storage_bucket_name", "STORAGE_BUCKET_NAME"),
        ("s3_endpoint_url", "S3_ENDPOINT_URL"),
        ("s3_access_key", "S3_ACCESS_KEY"),
        ("s3_secret_key", "S3_SECRET_KEY"),
        ("s3_region", "S3_REGION"),
        ("agent_dispatch_jwt_secret", "AGENT_DISPATCH_JWT_SECRET"),
        ("summary_provider", "SUMMARY_PROVIDER"),
        ("summary_model", "SUMMARY_MODEL"),
    ],
)
def test_production_rejects_missing_required_api_setting(
    base_settings: Settings,
    field_name: str,
    environment_name: str,
) -> None:
    settings = base_settings.model_copy(update={field_name: ""})

    with pytest.raises(RuntimeError, match=environment_name):
        validate_api_runtime(settings)


def test_production_rejects_missing_clerk_verification_source(base_settings: Settings) -> None:
    settings = base_settings.model_copy(
        update={"clerk_jwt_key": "", "clerk_jwks_url": ""}
    )

    with pytest.raises(RuntimeError) as exc_info:
        validate_api_runtime(settings)

    message = str(exc_info.value)
    assert "CLERK_JWT_KEY" in message
    assert "CLERK_JWKS_URL" in message


@pytest.mark.parametrize("verification_field", ["clerk_jwt_key", "clerk_jwks_url"])
def test_production_accepts_either_clerk_verification_source(
    base_settings: Settings,
    verification_field: str,
) -> None:
    settings = base_settings.model_copy(
        update={
            "clerk_jwt_key": "",
            "clerk_jwks_url": "",
            verification_field: "usable-verification-source",
        }
    )

    validate_api_runtime(settings)


def test_production_requires_gemini_credentials_for_gemini_summaries(
    base_settings: Settings,
) -> None:
    settings = base_settings.model_copy(update={"gemini_api_key": ""})

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        validate_api_runtime(settings)


def test_settings_do_not_expose_a_standard_stripe_price() -> None:
    assert "stripe_price_standard" not in Settings.model_fields


@pytest.mark.parametrize(
    "portal_return_url",
    [
        "ftp://app.example.com/settings",
        "https://user@app.example.com/settings",
        "https://app.example.com:bad/settings",
        "https://app.example.com/has space",
        "not a url",
    ],
)
def test_production_rejects_invalid_billing_portal_return_url(
    base_settings: Settings,
    portal_return_url: str,
) -> None:
    settings = base_settings.model_copy(
        update={"stripe_billing_portal_return_url": portal_return_url}
    )

    with pytest.raises(RuntimeError, match="STRIPE_BILLING_PORTAL_RETURN_URL"):
        validate_api_runtime(settings)


@pytest.mark.parametrize(
    "provider_value",
    ["Gemini", " gemini", "gemini ", "private-provider-value"],
)
def test_production_requires_exact_summary_provider_without_echoing_value(
    base_settings: Settings,
    provider_value: str,
) -> None:
    settings = base_settings.model_copy(update={"summary_provider": provider_value})

    with pytest.raises(RuntimeError, match="SUMMARY_PROVIDER") as exc_info:
        validate_api_runtime(settings)

    assert provider_value not in str(exc_info.value)


def test_production_reports_every_missing_api_setting(base_settings: Settings) -> None:
    settings = base_settings.model_copy(
        update={"redis_url": "", "livekit_api_key": "", "s3_secret_key": ""}
    )

    with pytest.raises(RuntimeError) as exc_info:
        validate_api_runtime(settings)

    message = str(exc_info.value)
    assert "REDIS_URL" in message
    assert "LIVEKIT_API_KEY" in message
    assert "S3_SECRET_KEY" in message


def test_development_accepts_fake_api_providers() -> None:
    settings = Settings(
        app_env="development",
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
    )

    validate_api_runtime(settings)


def test_api_import_rejects_invalid_production_settings_before_startup() -> None:
    environment = {
        **os.environ,
        "APP_ENV": "production",
        "AGENT_DISPATCH_JWT_SECRET": "",
    }

    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    assert result.returncode != 0
    assert "AGENT_DISPATCH_JWT_SECRET" in result.stderr


def test_deployment_docs_cover_staging_checklist_and_local_infra() -> None:
    readme = (REPO_ROOT / "README.md").read_text()
    compose = (REPO_ROOT / "compose.yaml").read_text()
    compose_dev = (REPO_ROOT / "compose.dev.yaml").read_text()
    api_env = (REPO_ROOT / "apps" / "api" / ".env.example").read_text()
    agent_env = (REPO_ROOT / "apps" / "agent" / ".env.example").read_text()
    api_dockerfile = (REPO_ROOT / "apps" / "api" / "Dockerfile").read_text()
    api_entrypoint = (REPO_ROOT / "apps" / "api" / "docker-entrypoint.sh").read_text()
    agent_dockerfile = (REPO_ROOT / "apps" / "agent" / "Dockerfile").read_text()
    backend_context = (REPO_ROOT / "docs" / "architecture" / "backend-context.md").read_text()

    assert "## Local Infra" in readme
    assert "docker compose up -d postgres redis minio minio-init" in readme
    assert "- [ ] API starts with real Postgres and Redis" in readme
    assert "- [ ] Agent worker starts with real LiveKit credentials" in readme
    assert "- [ ] Clerk webhook reaches `/webhooks/clerk`" in readme
    assert "- [ ] Stripe webhook resets minutes" in readme
    assert "- [ ] Telnyx number can be provisioned" in readme
    assert "- [ ] LiveKit dispatch reaches the agent" in readme
    assert "docker compose -f compose.yaml -f compose.dev.yaml --profile app up api worker agent" in readme
    assert "uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload" in readme
    assert "uv run arq app.workers.arq_worker.WorkerSettings" in readme
    assert "python dev_runner.py" in readme

    assert 'image: postgres:17.8-bookworm' in compose
    assert 'image: redis:7.4.7-alpine' in compose
    assert 'image: quay.io/minio/minio:RELEASE.2025-07-23T15-54-02Z' in compose
    assert 'image: quay.io/minio/mc:RELEASE.2025-07-21T05-28-08Z' in compose
    assert 'MINIO_ROOT_USER: minioadmin' in compose
    assert 'MINIO_ROOT_PASSWORD: minioadmin' in compose
    assert 'STORAGE_BUCKET_NAME: recordings' in compose
    assert 'env_file:' in compose
    assert '- ./apps/api/.env' in compose
    assert '- ./apps/agent/.env' in compose
    assert 'DATABASE_URL: postgresql+asyncpg://postgres:postgres@postgres:5432/ai_call' in compose
    assert 'REDIS_URL: redis://redis:6379/0' in compose
    assert 'container_name: ai-call-worker' in compose
    assert 'command: ["uv", "run", "arq", "app.workers.arq_worker.WorkerSettings"]' in compose
    assert 'API_BASE_URL: http://api:8000' in compose
    assert '${LIVEKIT_API_KEY:-replace-me}' not in compose
    assert '${LIVEKIT_API_SECRET:-replace-me}' not in compose
    assert '${TELNYX_API_KEY:-replace-me}' not in compose
    assert '${AGENT_INTERNAL_API_TOKEN:-replace-me}' not in compose

    assert "STORAGE_BUCKET_NAME=recordings" in api_env
    assert "S3_ENDPOINT_URL=http://minio:9000" in api_env
    assert "S3_ACCESS_KEY=minioadmin" in api_env
    assert "S3_SECRET_KEY=minioadmin" in api_env
    assert "S3_REGION=us-east-1" in api_env
    assert "CLERK_JWKS_URL=replace-me" in api_env
    assert "STRIPE_SECRET_KEY=replace-me" in api_env
    assert "TELNYX_ORDERING_ENABLED=false" in api_env
    assert "STRIPE_PRICE_STARTER=price_replace_me" in api_env
    assert "STRIPE_PRICE_STANDARD" not in api_env
    assert "STRIPE_CHECKOUT_SUCCESS_URL=https://your-app.example.com/billing/success" in api_env
    assert "STRIPE_CHECKOUT_CANCEL_URL=https://your-app.example.com/billing/cancel" in api_env
    assert "STRIPE_BILLING_PORTAL_RETURN_URL=https://your-app.example.com/dashboard/billing" in api_env
    assert "CLERK_JWT_SECRET=replace-me" not in api_env
    assert "OPENAI_API_KEY=replace-me" not in api_env

    assert "REDIS_URL=redis://redis:6379/0" in agent_env
    assert "API_BASE_URL=http://api:8000" in agent_env
    assert "AGENT_INTERNAL_API_TOKEN=replace-me" in agent_env
    assert "GEMINI_API_KEY=replace-me" in agent_env
    assert "OPENAI_API_KEY=replace-me" not in agent_env
    assert "DEEPGRAM_API_KEY=replace-me" in agent_env
    assert "ELEVENLABS_API_KEY=replace-me" in agent_env

    assert "COPY alembic.ini ./" in api_dockerfile
    assert "COPY alembic ./alembic" in api_dockerfile
    assert 'CMD ["./docker-entrypoint.sh"]' in api_dockerfile
    assert "uv run alembic -c alembic.ini upgrade head" in api_entrypoint
    assert 'exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000' in api_entrypoint

    assert 'CMD ["uv", "run", "python", "-m", "agent.main", "start"]' in agent_dockerfile

    assert "services:" in compose_dev
    assert "volumes:" in compose_dev
    assert "./apps/api/app:/app/app" in compose_dev
    assert "./apps/agent/agent:/app/agent" in compose_dev
    assert "uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload" in compose_dev
    assert 'command: ["uv", "run", "arq", "app.workers.arq_worker.WorkerSettings"]' in compose_dev
    assert "dev_runner.py" in compose_dev

    assert "## Staging Smoke Status" in backend_context
    assert "Partially executed on 2026-03-16." in backend_context
    assert "Queue-backed call finalization" in backend_context
    assert "phone_number_provisioning_review_required" in backend_context
