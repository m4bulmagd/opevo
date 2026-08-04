import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest

from app.core.config import Settings
from app.core.runtime_validation import validate_api_runtime, validate_worker_runtime


REPO_ROOT = Path(__file__).resolve().parents[3]

PRODUCTION_COMPOSE_ENVIRONMENT = {
    "ACTIVATION_FLOW_ENABLED": "true",
    "AGENT_DISPATCH_JWT_SECRET": "test-only-test-only-test-only-test-only",
    "AGENT_IMAGE": "presvo-agent:verification",
    "API_BASE_URL": "https://api.example.invalid",
    "API_IMAGE": "presvo-api:verification",
    "CLERK_AUTHORIZED_PARTIES": "https://app.example.com",
    "CLERK_ISSUER": "https://clerk.example.com",
    "CLERK_JWT_KEY": "",
    "CLERK_JWKS_URL": "https://clerk.example.com/.well-known/jwks.json",
    "CLERK_SECRET_KEY": "disposable-clerk-secret",
    "CLERK_WEBHOOK_SECRET": "disposable-webhook-secret",
    "CORS_ALLOWED_ORIGINS": "https://app.example.com",
    "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@postgres:5432/ai_call",
    "GEMINI_API_KEY": "disposable-gemini-key",
    "LIVEKIT_API_KEY": "disposable-livekit-key",
    "LIVEKIT_API_SECRET": "disposable-livekit-secret",
    "LIVEKIT_URL": "wss://livekit.example.invalid",
    "NEXT_PUBLIC_API_BASE_URL": "https://api.example.invalid",
    "NEXT_PUBLIC_APP_URL": "https://app.example.com",
    "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY": "pk_test_disposable",
    "NEXT_PUBLIC_REALTIME_ENABLED": "false",
    "REDIS_URL": "redis://redis:6379/0",
    "S3_ACCESS_KEY": "test-only-s3-key",
    "S3_ENDPOINT_URL": "https://s3.example.invalid",
    "S3_REGION": "us-east-1",
    "S3_SECRET_KEY": "test-only-s3-secret",
    "SPEECHMATICS_API_KEY": "disposable-speechmatics-key",
    "STORAGE_BUCKET_NAME": "recordings",
    "STRIPE_BILLING_PORTAL_CONFIGURATION_ID": "bpc_disposable",
    "STRIPE_BILLING_PORTAL_RETURN_URL": "https://app.example.com/dashboard/billing",
    "STRIPE_CHECKOUT_CANCEL_URL": "https://app.example.com/billing/cancel",
    "STRIPE_CHECKOUT_SUCCESS_URL": "https://app.example.com/billing/success",
    "STRIPE_PRICE_STARTER": "price_disposable",
    "STRIPE_SECRET_KEY": "stripe-test-fixture",
    "STRIPE_WEBHOOK_SECRET": "whsec_disposable",
    "SUMMARY_MODEL": "gemini-2.5-flash",
    "SUMMARY_PROVIDER": "gemini",
    "TELNYX_ACTIVE_CONNECTION_ID": "disposable-active-connection",
    "TELNYX_API_KEY": "disposable-telnyx-key",
    "TELNYX_DISABLED_CONNECTION_ID": "disposable-disabled-connection",
    "TELNYX_ORDERING_ENABLED": "true",
    "WEB_IMAGE": "presvo-web:verification",
}
CLERK_SESSION_VERIFIER_SETTINGS = (
    "CLERK_AUTHORIZED_PARTIES",
    "CLERK_JWT_KEY",
    "CLERK_JWKS_URL",
    "CLERK_JWKS_CACHE_TTL_SECONDS",
    "CLERK_JWKS_STALE_GRACE_SECONDS",
    "CLERK_JWKS_CONNECT_TIMEOUT_SECONDS",
    "CLERK_JWKS_READ_TIMEOUT_SECONDS",
    "CLERK_JWKS_POOL_TIMEOUT_SECONDS",
    "CLERK_JWKS_TOTAL_TIMEOUT_SECONDS",
)
LOCAL_COMPOSE_AUTH_DEFAULTS = {
    "AUTH_MODE": "",
    "LOCAL_AUTH_TOKEN": "",
    "CLERK_AUTHORIZED_PARTIES": "",
}


def render_compose(
    compose_file: str | Path,
    environment: dict[str, str],
    *,
    resolve_env_files: bool = True,
    working_directory: Path = REPO_ROOT,
) -> dict:
    command = [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "config",
        "--format",
        "json",
    ]
    if not resolve_env_files:
        command.append("--no-env-resolution")
    result = subprocess.run(
        command,
        cwd=working_directory,
        capture_output=True,
        check=False,
        env={**os.environ, **environment},
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def load_compose_yaml() -> dict:
    return render_compose("compose.yaml", PRODUCTION_COMPOSE_ENVIRONMENT)


def resolved_service_environment(document: dict, service: str) -> dict[str, str]:
    return document["services"][service].get("environment", {})


def load_local_compose_yaml(
    environment: dict[str, str] | None = None,
) -> dict:
    return render_compose(
        "compose.dev.yaml",
        LOCAL_COMPOSE_AUTH_DEFAULTS | (environment or {}),
        resolve_env_files=False,
    )


def test_compose_render_can_skip_service_env_file_resolution(tmp_path: Path) -> None:
    compose_file = tmp_path / "compose.yaml"
    service_env_file = tmp_path / "service.env"
    compose_file.write_text(
        """\
services:
  api:
    image: example.invalid/presvo/api:test
    env_file:
      - ./service.env
    environment:
      EXPLICIT_SENTINEL: from-compose-model
""",
        encoding="utf-8",
    )
    service_env_file.write_text(
        "ENV_FILE_SENTINEL=from-service-env-file\n",
        encoding="utf-8",
    )

    resolved_document = render_compose(
        compose_file,
        {},
        working_directory=tmp_path,
    )
    isolated_document = render_compose(
        compose_file,
        {},
        resolve_env_files=False,
        working_directory=tmp_path,
    )
    resolved_environment = resolved_service_environment(resolved_document, "api")
    isolated_environment = resolved_service_environment(isolated_document, "api")

    assert resolved_environment["ENV_FILE_SENTINEL"] == "from-service-env-file"
    assert "ENV_FILE_SENTINEL" not in isolated_environment
    assert isolated_environment["EXPLICIT_SENTINEL"] == "from-compose-model"


def test_postgres_driver_is_available_without_development_dependencies() -> None:
    pyproject = tomllib.loads(
        (REPO_ROOT / "apps" / "api" / "pyproject.toml").read_text()
    )

    runtime_dependencies = pyproject["project"]["dependencies"]
    development_dependencies = pyproject["dependency-groups"]["dev"]

    assert any(dependency.startswith("asyncpg") for dependency in runtime_dependencies)
    assert not any(
        dependency.startswith("asyncpg") for dependency in development_dependencies
    )


@pytest.fixture
def base_settings() -> Settings:
    return Settings(
        app_env="production",
        database_url="postgresql+asyncpg://db/ai_call",
        redis_url="rediss://redis/0",
        clerk_issuer="https://clerk.example.com",
        clerk_authorized_parties="https://app.example.com",
        clerk_jwt_key=None,
        clerk_jwks_url="https://clerk.example.com/.well-known/jwks.json",
        clerk_webhook_secret="clerk-webhook-secret",
        stripe_secret_key="stripe-secret-key",
        stripe_webhook_secret="stripe-webhook-secret",
        stripe_price_starter="stripe-starter-price",
        stripe_checkout_success_url="https://app.example.com/billing/success",
        stripe_checkout_cancel_url="https://app.example.com/billing/cancel",
        stripe_billing_portal_return_url="https://app.example.com/dashboard/billing",
        stripe_billing_portal_configuration_id="bpc_period_end_cancel",
        livekit_url="wss://livekit.example.com",
        livekit_api_key="livekit-api-key",
        livekit_api_secret="livekit-api-secret",
        telnyx_api_key="telnyx-api-key",
        telnyx_active_connection_id="telnyx-active-connection",
        telnyx_disabled_connection_id="telnyx-disabled-connection",
        telnyx_ordering_enabled=True,
        auth_mode="clerk",
        billing_mode="stripe",
        carrier_lookup_mode="telnyx",
        telephony_mode="telnyx",
        storage_bucket_name="recordings",
        s3_endpoint_url="https://storage.example.com",
        s3_access_key="storage-access-key",
        s3_secret_key="storage-secret-key",
        s3_region="eu-west-3",
        agent_dispatch_jwt_secret="production-dispatch-jwt-secret-at-least-32-bytes",
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
        (
            "stripe_billing_portal_configuration_id",
            "STRIPE_BILLING_PORTAL_CONFIGURATION_ID",
        ),
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


@pytest.mark.parametrize(
    "verification_configuration",
    [
        {"clerk_jwt_key": "", "clerk_jwks_url": ""},
        {
            "clerk_jwt_key": "test-static-verification-key",
            "clerk_jwks_url": "https://clerk.example.com/.well-known/jwks.json",
        },
    ],
)
def test_production_rejects_nonexclusive_clerk_verification_sources(
    base_settings: Settings,
    verification_configuration: dict[str, str],
) -> None:
    settings = base_settings.model_copy(
        update=verification_configuration
    )

    with pytest.raises(RuntimeError) as exc_info:
        validate_api_runtime(settings)

    message = str(exc_info.value)
    assert "CLERK_JWT_KEY" in message
    assert "test-static-verification-key" not in message


def test_production_rejects_disabled_telnyx_ordering(
    base_settings: Settings,
) -> None:
    settings = base_settings.model_copy(update={"telnyx_ordering_enabled": False})

    with pytest.raises(RuntimeError, match="TELNYX_ORDERING_ENABLED"):
        validate_api_runtime(settings)


@pytest.mark.parametrize(
    "unsafe_secret",
    [
        "too-short",
        "replace-with-a-long-random-secret",
        "CHANGE-ME-CHANGE-ME-CHANGE-ME-CHANGE-ME",
    ],
)
def test_production_rejects_unsafe_dispatch_hmac_secret(
    base_settings: Settings,
    unsafe_secret: str,
) -> None:
    settings = base_settings.model_copy(
        update={"agent_dispatch_jwt_secret": unsafe_secret}
    )

    with pytest.raises(RuntimeError, match="AGENT_DISPATCH_JWT_SECRET"):
        validate_api_runtime(settings)


@pytest.mark.parametrize(
    "verification_configuration",
    [
        {
            "clerk_jwt_key": "test-static-verification-key",
            "clerk_jwks_url": None,
        },
        {
            "clerk_jwt_key": None,
            "clerk_jwks_url": "https://clerk.example.com/.well-known/jwks.json",
        },
    ],
)
def test_production_accepts_either_clerk_verification_source(
    base_settings: Settings,
    verification_configuration: dict[str, str | None],
) -> None:
    settings = base_settings.model_copy(
        update=verification_configuration
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


def test_realtime_is_disabled_by_default() -> None:
    settings = Settings(
        app_env="development",
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
    )

    assert settings.realtime_enabled is False


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


def test_local_runtime_rejects_an_omitted_local_auth_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOCAL_AUTH_TOKEN", raising=False)
    settings = Settings(
        _env_file=None,
        app_env="development",
        auth_mode="local",
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
    )

    with pytest.raises(RuntimeError) as error:
        validate_api_runtime(settings)

    assert str(error.value) == (
        "Missing or invalid required runtime settings: LOCAL_AUTH_TOKEN"
    )


@pytest.mark.parametrize(
    ("field_name", "unsafe_value", "setting_name"),
    [
        ("auth_mode", "local", "AUTH_MODE"),
        ("billing_mode", "fake", "BILLING_MODE"),
        ("carrier_lookup_mode", "fake", "CARRIER_LOOKUP_MODE"),
        ("telephony_mode", "fake", "TELEPHONY_MODE"),
    ],
)
def test_production_requires_exact_provider_modes_without_echoing_values(
    base_settings: Settings,
    field_name: str,
    unsafe_value: str,
    setting_name: str,
) -> None:
    settings = base_settings.model_copy(
        update={
            field_name: unsafe_value,
            "local_auth_token": "explicit-local-test-token",
        }
    )

    with pytest.raises(RuntimeError, match=setting_name) as error:
        validate_api_runtime(settings)

    assert unsafe_value not in str(error.value)


@pytest.mark.parametrize(
    "field_name",
    ("auth_mode", "billing_mode", "carrier_lookup_mode", "telephony_mode"),
)
def test_settings_validation_hides_arbitrary_invalid_mode_values(
    field_name: str,
) -> None:
    sentinel = f"{field_name.upper()}_SENTINEL_SECRET"

    with pytest.raises(ValueError) as error:
        Settings(
            database_url="sqlite+aiosqlite://",
            redis_url="redis://localhost:6379/0",
            **{field_name: sentinel},
        )

    assert sentinel not in str(error.value)


@pytest.mark.parametrize("app_env", ["test", "staging"])
@pytest.mark.parametrize(
    "validator",
    (validate_api_runtime, validate_worker_runtime),
    ids=("api", "worker"),
)
def test_every_non_development_environment_rejects_local_auth(
    app_env: str,
    validator,
) -> None:
    settings = Settings(
        app_env=app_env,
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
        agent_dispatch_jwt_secret="runtime-dispatch-secret-with-at-least-32-bytes",
        auth_mode="local",
        local_auth_token="local-token-sentinel-that-must-not-be-reported",
    )

    with pytest.raises(RuntimeError, match="AUTH_MODE") as error:
        validator(settings)

    assert "local-token-sentinel-that-must-not-be-reported" not in str(error.value)


@pytest.mark.parametrize("app_env", ["test", "staging"])
@pytest.mark.parametrize(
    "unsafe_secret",
    [
        None,
        "",
        "too-short",
        "replace-with-a-long-random-secret",
        "CHANGE-ME-CHANGE-ME-CHANGE-ME-CHANGE-ME",
    ],
)
def test_non_development_runtime_rejects_missing_or_unsafe_dispatch_secret(
    app_env: str,
    unsafe_secret: str | None,
) -> None:
    settings = Settings(
        app_env=app_env,
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
        agent_dispatch_jwt_secret=unsafe_secret,
    )

    with pytest.raises(RuntimeError, match="AGENT_DISPATCH_JWT_SECRET"):
        validate_api_runtime(settings)


@pytest.mark.parametrize("app_env", ["test", "staging"])
def test_non_development_runtime_accepts_strong_dispatch_secret_only(
    app_env: str,
) -> None:
    settings = Settings(
        app_env=app_env,
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
        agent_dispatch_jwt_secret="runtime-dispatch-secret-with-at-least-32-bytes",
    )

    validate_api_runtime(settings)


def test_api_import_rejects_invalid_production_settings_before_startup() -> None:
    environment = {
        **os.environ,
        "APP_ENV": "production",
        "AGENT_DISPATCH_JWT_SECRET": "",
        "AUTH_MODE": "clerk",
        "BILLING_MODE": "stripe",
        "CARRIER_LOOKUP_MODE": "telnyx",
        "TELEPHONY_MODE": "telnyx",
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


def test_api_startup_is_migration_free_but_release_image_keeps_alembic() -> None:
    api_dockerfile = (REPO_ROOT / "apps" / "api" / "Dockerfile").read_text()
    api_main = (REPO_ROOT / "apps" / "api" / "app" / "main.py").read_text()
    migration_compose = (REPO_ROOT / "compose.migrate.yaml").read_text()

    assert "COPY apps/api/alembic.ini ./" in api_dockerfile
    assert "COPY apps/api/alembic ./alembic" in api_dockerfile
    assert "alembic" not in api_main.lower()
    assert not (REPO_ROOT / "apps" / "api" / "docker-entrypoint.sh").exists()
    assert 'CMD ["/app/.venv/bin/uvicorn"' in api_dockerfile
    assert "migrate:" in migration_compose
    assert 'command: ["/app/.venv/bin/alembic", "-c", "/app/alembic.ini", "upgrade", "head"]' in migration_compose


def test_assistant_overrides_migration_is_the_only_alembic_head() -> None:
    config = Config(str(REPO_ROOT / "apps" / "api" / "alembic.ini"))
    config.set_main_option("path_separator", "os")

    assert ScriptDirectory.from_config(config).get_heads() == [
        "0017_assistant_overrides"
    ]


def test_migration_compose_requires_only_image_and_database_url() -> None:
    migration_compose = (REPO_ROOT / "compose.migrate.yaml").read_text()
    interpolated_variables = set(re.findall(r"\$\{([A-Z0-9_]+)", migration_compose))

    assert interpolated_variables == {"API_IMAGE", "DATABASE_URL"}
    assert "read_only: true" in migration_compose
    assert "cap_drop:" in migration_compose
    assert "no-new-privileges:true" in migration_compose


def test_alembic_offline_requires_only_database_url(tmp_path: Path) -> None:
    isolated_root = tmp_path / "migration-runtime"
    isolated_root.mkdir()
    (isolated_root / "app").symlink_to(REPO_ROOT / "apps" / "api" / "app", target_is_directory=True)
    (isolated_root / "alembic").symlink_to(
        REPO_ROOT / "apps" / "api" / "alembic",
        target_is_directory=True,
    )
    (isolated_root / "alembic.ini").write_text(
        (REPO_ROOT / "apps" / "api" / "alembic.ini").read_text()
    )

    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"REDIS_URL", "TEST_REDIS_URL"}
    }
    environment["DATABASE_URL"] = (
        "postgresql+asyncpg://migration:pa%25ss@database.example/presvo"
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(isolated_root / "alembic.ini"),
            "upgrade",
            "0001_initial_schema",
            "--sql",
        ],
        cwd=isolated_root,
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_runtime_images_are_pinned_minimal_non_root_and_health_checked() -> None:
    dockerfiles = {
        application: (REPO_ROOT / "apps" / application / "Dockerfile").read_text()
        for application in ("api", "agent", "web")
    }

    for dockerfile in dockerfiles.values():
        assert " AS builder" in dockerfile
        assert "USER 10001:10001" in dockerfile
        assert "HEALTHCHECK" in dockerfile
        assert "HOME=/tmp" in dockerfile

    for application in ("api", "agent"):
        dockerfile = dockerfiles[application]
        assert "python:3.13-slim@sha256:" in dockerfile
        assert 'uv==0.11.19' in dockerfile
        assert (
            f"uv sync --directory apps/{application} --frozen --no-dev --no-editable"
            in dockerfile
        )
        assert 'CMD ["uv"' not in dockerfile

    assert "node:22-alpine@sha256:" in dockerfiles["web"]
    assert "COPY --from=builder /app/.next/standalone ./" in dockerfiles["web"]
    assert "COPY --from=builder /app/.next/static ./.next/static" in dockerfiles["web"]
    assert 'CMD ["node", "server.js"]' in dockerfiles["web"]
    assert "node_modules ./node_modules" not in dockerfiles["web"]

    next_config = (REPO_ROOT / "apps" / "web" / "next.config.mjs").read_text()
    assert 'output: "standalone"' in next_config


def test_compose_separates_required_production_inputs_from_local_services() -> None:
    compose = (REPO_ROOT / "compose.yaml").read_text()
    compose_dev = (REPO_ROOT / "compose.dev.yaml").read_text()

    for service in ("postgres", "redis", "minio", "minio-init"):
        assert f"  {service}:" not in compose
        assert f"  {service}:" in compose_dev

    for unsafe_value in ("minioadmin", "postgres:postgres", "replace-me"):
        assert unsafe_value not in compose

    for required_input in (
        "DATABASE_URL",
        "REDIS_URL",
        "CLERK_WEBHOOK_SECRET",
        "LIVEKIT_API_SECRET",
        "STORAGE_BUCKET_NAME",
        "S3_ENDPOINT_URL",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "S3_REGION",
        "AGENT_DISPATCH_JWT_SECRET",
        "CLERK_SECRET_KEY",
    ):
        assert f"${{{required_input}:?" in compose

    assert "  migrate:" not in compose
    assert "read_only: true" in compose
    assert "tmpfs:" in compose
    assert 'image: postgres:17.8-bookworm' in compose_dev
    assert 'image: redis:7.4.7-alpine' in compose_dev
    assert 'MINIO_ROOT_USER: minioadmin' in compose_dev
    assert 'MINIO_ROOT_PASSWORD: minioadmin' in compose_dev
    assert "migrate:" in compose_dev
    assert "service_completed_successfully" in compose_dev


def test_production_compose_scopes_clerk_session_verifier_settings_to_api() -> None:
    document = load_compose_yaml()
    api_environment = resolved_service_environment(document, "api")

    for setting in CLERK_SESSION_VERIFIER_SETTINGS:
        assert setting in api_environment
    assert api_environment.get("REALTIME_ENABLED", "false") == "false"
    for service in ("worker", "agent", "web"):
        environment = resolved_service_environment(document, service)
        for setting in CLERK_SESSION_VERIFIER_SETTINGS:
            assert setting not in environment
    assert (
        resolved_service_environment(document, "web")["NEXT_PUBLIC_REALTIME_ENABLED"]
        == "false"
    )

    migration_compose = (REPO_ROOT / "compose.migrate.yaml").read_text()
    for setting in CLERK_SESSION_VERIFIER_SETTINGS:
        assert setting not in migration_compose


def test_production_compose_render_ignores_external_realtime_enablement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEXT_PUBLIC_REALTIME_ENABLED", "true")

    document = load_compose_yaml()

    assert (
        resolved_service_environment(document, "web")[
            "NEXT_PUBLIC_REALTIME_ENABLED"
        ]
        == "false"
    )


def test_production_compose_renders_exactly_one_nonempty_clerk_key_source() -> None:
    api_environment = resolved_service_environment(load_compose_yaml(), "api")

    assert api_environment["CLERK_JWT_KEY"] == ""
    assert api_environment["CLERK_JWKS_URL"] == (
        "https://clerk.example.com/.well-known/jwks.json"
    )
    assert sum(
        bool(api_environment[setting])
        for setting in ("CLERK_JWT_KEY", "CLERK_JWKS_URL")
    ) == 1


def test_local_compose_defaults_interactive_services_to_clerk() -> None:
    document = load_local_compose_yaml()
    api_environment = resolved_service_environment(document, "api")
    web_environment = resolved_service_environment(document, "web")
    worker_environment = resolved_service_environment(document, "worker")

    assert api_environment["AUTH_MODE"] == "clerk"
    assert web_environment["AUTH_MODE"] == "clerk"
    assert api_environment["LOCAL_AUTH_TOKEN"] == ""
    assert web_environment["LOCAL_AUTH_TOKEN"] == ""
    assert api_environment["CLERK_AUTHORIZED_PARTIES"] == (
        "http://127.0.0.1:3000,http://localhost:3000"
    )
    assert api_environment.get("REALTIME_ENABLED", "false") == "false"
    for setting in ("AUTH_MODE", "LOCAL_AUTH_TOKEN", "CLERK_AUTHORIZED_PARTIES"):
        assert setting not in worker_environment


def test_local_compose_custom_web_port_updates_every_default_local_origin() -> None:
    document = load_local_compose_yaml({"WEB_PORT": "3300"})
    api_environment = resolved_service_environment(document, "api")
    web_environment = resolved_service_environment(document, "web")

    expected_origins = "http://127.0.0.1:3300,http://localhost:3300"
    assert api_environment["CORS_ALLOWED_ORIGINS"] == expected_origins
    assert api_environment["CLERK_AUTHORIZED_PARTIES"] == expected_origins
    assert web_environment["NEXT_PUBLIC_APP_URL"] == "http://127.0.0.1:3300"


def test_local_compose_explicit_clerk_authorized_parties_override_wins() -> None:
    document = load_local_compose_yaml(
        {
            "WEB_PORT": "3300",
            "CLERK_AUTHORIZED_PARTIES": "https://explicit.example",
        }
    )
    api_environment = resolved_service_environment(document, "api")

    assert api_environment["CLERK_AUTHORIZED_PARTIES"] == "https://explicit.example"
    assert api_environment["CORS_ALLOWED_ORIGINS"] == (
        "http://127.0.0.1:3300,http://localhost:3300"
    )


def test_local_compose_accepts_explicit_synthetic_auth_for_disposable_tests() -> None:
    document = load_local_compose_yaml(
        {
            "AUTH_MODE": "local",
            "LOCAL_AUTH_TOKEN": "disposable-local-token",
        }
    )

    for service in ("api", "web"):
        environment = resolved_service_environment(document, service)
        assert environment["AUTH_MODE"] == "local"
        assert environment["LOCAL_AUTH_TOKEN"] == "disposable-local-token"


@pytest.mark.parametrize(
    "local_auth_token",
    ["", "   ", " padded-local-token "],
    ids=("blank", "whitespace", "padded"),
)
def test_local_compose_runtime_rejects_incomplete_synthetic_auth(
    local_auth_token: str,
) -> None:
    document = load_local_compose_yaml(
        {
            "AUTH_MODE": "local",
            "LOCAL_AUTH_TOKEN": local_auth_token,
        }
    )
    api_environment = resolved_service_environment(document, "api")
    settings = Settings(
        app_env=api_environment["APP_ENV"],
        database_url=api_environment["DATABASE_URL"],
        redis_url=api_environment["REDIS_URL"],
        auth_mode=api_environment["AUTH_MODE"],
        local_auth_token=api_environment["LOCAL_AUTH_TOKEN"],
    )

    with pytest.raises(RuntimeError) as error:
        validate_api_runtime(settings)

    assert str(error.value) == (
        "Missing or invalid required runtime settings: LOCAL_AUTH_TOKEN"
    )


def test_clerk_example_documents_session_verifier_without_real_origin() -> None:
    example = (REPO_ROOT / "apps" / "api" / ".env.example").read_text()
    expected_settings = {
        "CLERK_AUTHORIZED_PARTIES": "http://127.0.0.1:3000,http://localhost:3000",
        "CLERK_JWT_KEY": "",
        "CLERK_JWKS_URL": "",
        "CLERK_JWKS_CACHE_TTL_SECONDS": "300",
        "CLERK_JWKS_STALE_GRACE_SECONDS": "600",
        "CLERK_JWKS_CONNECT_TIMEOUT_SECONDS": "0.5",
        "CLERK_JWKS_READ_TIMEOUT_SECONDS": "1.0",
        "CLERK_JWKS_POOL_TIMEOUT_SECONDS": "0.25",
        "CLERK_JWKS_TOTAL_TIMEOUT_SECONDS": "2.0",
    }

    for setting, value in expected_settings.items():
        assert f"{setting}={value}" in example
    assert [
        line
        for line in example.splitlines()
        if line.startswith("CLERK_AUTHORIZED_PARTIES=")
    ] == [
        "CLERK_AUTHORIZED_PARTIES=http://127.0.0.1:3000,http://localhost:3000"
    ]


def test_api_ci_supplies_network_free_clerk_construction_values() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    api_job = workflow.split("\n  api:", 1)[1].split("\n  agent:", 1)[0]

    assert "CLERK_ISSUER: https://clerk.example.com" in api_job
    assert "CLERK_AUTHORIZED_PARTIES: https://app.example.com" in api_job
    assert (
        "CLERK_JWKS_URL: https://clerk.example.com/.well-known/jwks.json"
        in api_job
    )
    assert "CLERK_JWT_KEY:" not in api_job


def test_compose_requires_explicit_telnyx_ordering_for_worker_and_api() -> None:
    compose = (REPO_ROOT / "compose.yaml").read_text()
    required_setting = (
        "TELNYX_ORDERING_ENABLED: "
        "${TELNYX_ORDERING_ENABLED:?TELNYX_ORDERING_ENABLED is required}"
    )

    assert compose.count(required_setting) == 1
    worker_environment = compose.split("x-api-environment:", maxsplit=1)[0]
    assert required_setting in worker_environment


def test_production_compose_scopes_modes_and_passes_runtime_validation(
    base_settings: Settings,
) -> None:
    compose = (REPO_ROOT / "compose.yaml").read_text()
    worker_environment = compose.split(
        "x-worker-environment: &worker-environment",
        1,
    )[1].split("x-api-environment: &api-environment", 1)[0]
    api_environment = compose.split(
        "x-api-environment: &api-environment",
        1,
    )[1].split("x-service-hardening: &service-hardening", 1)[0]
    api_only_modes = {
        "AUTH_MODE": ("auth_mode", "clerk"),
        "BILLING_MODE": ("billing_mode", "stripe"),
        "CARRIER_LOOKUP_MODE": ("carrier_lookup_mode", "telnyx"),
    }
    resolved_modes: dict[str, str] = {}
    for environment_name, (field_name, expected_value) in api_only_modes.items():
        match = re.search(
            rf"^  {environment_name}: ([a-z]+)$",
            api_environment,
            flags=re.MULTILINE,
        )
        assert match is not None
        assert match.group(1) == expected_value
        resolved_modes[field_name] = match.group(1)
        if environment_name == "BILLING_MODE":
            assert environment_name in worker_environment
        else:
            assert environment_name not in worker_environment

    telephony_match = re.search(
        r"^  TELEPHONY_MODE: ([a-z]+)$",
        worker_environment,
        flags=re.MULTILINE,
    )
    assert telephony_match is not None
    assert telephony_match.group(1) == "telnyx"
    resolved_modes["telephony_mode"] = telephony_match.group(1)

    assert (
        "ACTIVATION_FLOW_ENABLED: "
        "${ACTIVATION_FLOW_ENABLED:?ACTIVATION_FLOW_ENABLED is required}"
    ) in worker_environment
    assert "LOCAL_AUTH_TOKEN" not in compose

    validate_api_runtime(base_settings.model_copy(update=resolved_modes))


def test_production_worker_runtime_accepts_least_privilege_settings() -> None:
    settings = Settings(
        app_env="production",
        database_url="postgresql+asyncpg://db/ai_call",
        redis_url="rediss://redis/0",
        livekit_url="wss://livekit.example.com",
        livekit_api_key="livekit-api-key",
        livekit_api_secret="livekit-api-secret",
        telnyx_api_key="telnyx-api-key",
        telnyx_active_connection_id="telnyx-active-connection",
        telnyx_disabled_connection_id="telnyx-disabled-connection",
        telnyx_ordering_enabled=True,
        telephony_mode="telnyx",
        storage_bucket_name="recordings",
        s3_endpoint_url="https://storage.example.com",
        s3_access_key="storage-access-key",
        s3_secret_key="storage-secret-key",
        s3_region="eu-west-3",
        agent_dispatch_jwt_secret=(
            "production-dispatch-jwt-secret-at-least-32-bytes"
        ),
        summary_provider="gemini",
        summary_model="gemini-2.5-flash",
        gemini_api_key="gemini-api-key",
        billing_mode="stripe",
        stripe_secret_key="stripe-secret-key",
    )

    validate_worker_runtime(settings)


@pytest.mark.parametrize(
    ("field_name", "environment_name"),
    [
        ("livekit_url", "LIVEKIT_URL"),
        ("livekit_api_key", "LIVEKIT_API_KEY"),
        ("livekit_api_secret", "LIVEKIT_API_SECRET"),
        ("storage_bucket_name", "STORAGE_BUCKET_NAME"),
        ("s3_endpoint_url", "S3_ENDPOINT_URL"),
        ("s3_access_key", "S3_ACCESS_KEY"),
        ("s3_secret_key", "S3_SECRET_KEY"),
        ("s3_region", "S3_REGION"),
    ],
)
def test_production_worker_requires_recording_provider_and_private_storage(
    base_settings: Settings,
    field_name: str,
    environment_name: str,
) -> None:
    settings = base_settings.model_copy(update={field_name: ""})

    with pytest.raises(RuntimeError, match=environment_name):
        validate_worker_runtime(settings)


def test_worker_rejects_stripe_mode_without_stripe_secret_key(
    base_settings: Settings,
) -> None:
    settings = base_settings.model_copy(update={"stripe_secret_key": ""})

    with pytest.raises(RuntimeError, match="STRIPE_SECRET_KEY"):
        validate_worker_runtime(settings)


@pytest.mark.parametrize("app_env", ["development", "test", "staging"])
def test_non_production_worker_rejects_stripe_mode_without_stripe_secret_key(
    app_env: str,
) -> None:
    settings = Settings(
        app_env=app_env,
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
        billing_mode="stripe",
        stripe_secret_key="",
        agent_dispatch_jwt_secret=(
            "worker-dispatch-jwt-secret-with-at-least-32-bytes"
        ),
    )

    with pytest.raises(RuntimeError, match="STRIPE_SECRET_KEY"):
        validate_worker_runtime(settings)


def test_development_worker_accepts_fake_billing_without_stripe_secret_key() -> None:
    settings = Settings(
        app_env="development",
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
        billing_mode="fake",
    )

    validate_worker_runtime(settings)


def test_recording_runtime_does_not_require_an_agent_evaluation_model() -> None:
    from app.core.runtime_validation import (
        PRODUCTION_REQUIRED_SETTINGS,
        WORKER_PRODUCTION_REQUIRED_SETTINGS,
    )

    assert "livekit_eval_model" not in Settings.model_fields
    assert "livekit_eval_model" not in PRODUCTION_REQUIRED_SETTINGS
    assert "livekit_eval_model" not in WORKER_PRODUCTION_REQUIRED_SETTINGS
    assert "LIVEKIT_EVAL_MODEL" not in (REPO_ROOT / "compose.yaml").read_text()


def test_recording_runtime_has_one_reference_only_outbox_contract() -> None:
    from app.core.observability import _OUTBOX_TOPICS
    from app.core.provider_failures import is_safe_provider_operation
    from app.providers.livekit_recording.livekit import LiveKitRecordingProvider
    from app.services.outbox_service import (
        REFERENCE_PAYLOAD_FIELDS,
        SUPPORTED_OUTBOX_TOPICS,
    )
    from app.workers.jobs.outbox_topics import (
        DEFAULT_OUTBOX_HANDLERS,
        deliver_recording_reconcile,
    )

    assert {
        topic for topic in SUPPORTED_OUTBOX_TOPICS if topic.startswith("recording.")
    } == {"recording.reconcile"}
    assert {
        topic for topic in DEFAULT_OUTBOX_HANDLERS if topic.startswith("recording.")
    } == {"recording.reconcile"}
    assert {
        topic for topic in _OUTBOX_TOPICS if topic.startswith("recording.")
    } == {"recording.reconcile"}
    assert REFERENCE_PAYLOAD_FIELDS["recording.reconcile"] == frozenset(
        {"operation_id"}
    )
    assert (
        DEFAULT_OUTBOX_HANDLERS["recording.reconcile"]
        is deliver_recording_reconcile
    )
    assert is_safe_provider_operation("livekit", "list_recording_egresses")
    assert callable(LiveKitRecordingProvider.list_room_egresses)


def test_development_services_load_local_env_files_without_leaking_api_identity_to_worker() -> None:
    compose_dev = (REPO_ROOT / "compose.dev.yaml").read_text()
    api_service = compose_dev.split("\n  api:", 1)[1].split("\n  worker:", 1)[0]
    worker_service = compose_dev.split("\n  worker:", 1)[1].split("\n  agent:", 1)[0]
    agent_service = compose_dev.split("\n  agent:", 1)[1].split("\n  web:", 1)[0]
    web_service = compose_dev.split("\n  web:", 1)[1].split("\nvolumes:", 1)[0]

    for service, env_path in (
        (api_service, "./apps/api/.env"),
        (agent_service, "./apps/agent/.env"),
        (web_service, "./apps/web/.env"),
    ):
        assert "env_file:" in service
        assert f"path: {env_path}" in service
        assert "required: false" in service

    assert "env_file:" not in worker_service
    assert "./apps/api/.env" not in worker_service

    for provider_key in (
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "SPEECHMATICS_API_KEY",
        "GEMINI_API_KEY",
        "MISTRAL_API_KEY",
        "ELEVENLABS_API_KEY",
        "DEEPGRAM_API_KEY",
    ):
        assert f"{provider_key}:" not in agent_service

    assert "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY:" not in web_service
    assert "CLERK_SECRET_KEY:" not in web_service
    assert "DATABASE_URL: postgresql+asyncpg://postgres:postgres@postgres:5432/ai_call" in api_service
    assert "API_BASE_URL: http://api:8000" in agent_service
    assert "API_BASE_URL: http://api:8000" in web_service


def test_development_compose_scopes_clerk_identity_and_provider_modes() -> None:
    document = load_local_compose_yaml()
    api_env_example = (REPO_ROOT / "apps" / "api" / ".env.example").read_text()
    api_environment = resolved_service_environment(document, "api")
    worker_environment = resolved_service_environment(document, "worker")
    web_environment = resolved_service_environment(document, "web")

    assert api_environment["AUTH_MODE"] == "clerk"
    assert api_environment["BILLING_MODE"] == "fake"
    assert api_environment["CARRIER_LOOKUP_MODE"] == "fake"
    assert api_environment["TELEPHONY_MODE"] == "fake"
    assert api_environment["ACTIVATION_FLOW_ENABLED"] == "true"

    assert worker_environment["TELEPHONY_MODE"] == "fake"
    assert worker_environment["ACTIVATION_FLOW_ENABLED"] == "true"
    for forbidden_setting in (
        "AUTH_MODE",
        "CARRIER_LOOKUP_MODE",
        "LOCAL_AUTH_TOKEN",
    ):
        assert forbidden_setting not in worker_environment
    for setting in CLERK_SESSION_VERIFIER_SETTINGS:
        assert setting not in worker_environment
    assert worker_environment["BILLING_MODE"] == "fake"
    assert web_environment["AUTH_MODE"] == "clerk"
    assert web_environment["BILLING_MODE"] == "fake"
    assert web_environment["TELEPHONY_MODE"] == "fake"
    assert "CARRIER_LOOKUP_MODE" not in web_environment
    assert "NEXT_PUBLIC_LOCAL_AUTH_TOKEN" not in web_environment

    local_examples = (
        "AUTH_MODE=local",
        "LOCAL_AUTH_TOKEN=replace-with-a-development-only-token",
        "BILLING_MODE=fake",
        "CARRIER_LOOKUP_MODE=fake",
        "TELEPHONY_MODE=fake",
    )
    active_example_lines = {
        line.strip()
        for line in api_env_example.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    for example in local_examples:
        assert f"# {example}" in api_env_example
        assert example not in active_example_lines


def test_development_compose_parameterizes_disposable_host_ports() -> None:
    compose_dev = (REPO_ROOT / "compose.dev.yaml").read_text()

    for binding in (
        '"127.0.0.1:${POSTGRES_PORT:-5432}:5432"',
        '"127.0.0.1:${REDIS_PORT:-6379}:6379"',
        '"127.0.0.1:${MINIO_PORT:-9000}:9000"',
        '"127.0.0.1:${MINIO_CONSOLE_PORT:-9001}:9001"',
        '"127.0.0.1:${API_PORT:-8000}:8000"',
        '"127.0.0.1:${WEB_PORT:-3000}:3000"',
    ):
        assert binding in compose_dev


def test_local_e2e_runner_is_disposable_and_never_starts_voice_agent() -> None:
    runner = (REPO_ROOT / "scripts" / "run-local-e2e.sh").read_text()

    assert runner.startswith("#!/bin/sh\nset -eu\n")
    assert "presvo-e2e" in runner
    for port in ("3300", "5800", "55432", "56379", "59000", "59001"):
        assert port in runner
    assert "trap cleanup" in runner
    assert "down --volumes" in runner
    assert "wait_for_health" in runner
    up_commands = re.findall(r"^compose up --detach (.+)$", runner, re.MULTILINE)
    started_services = set(" ".join(up_commands).split())
    assert started_services == {
        "postgres",
        "redis",
        "minio",
        "minio-init",
        "migrate",
        "api",
        "worker",
        "web",
    }
    assert "agent" not in started_services


def test_local_e2e_runner_scopes_dashboard_metrics_reference_time_to_api() -> None:
    runner = (REPO_ROOT / "scripts" / "run-local-e2e.sh").read_text()
    compose_dev = (REPO_ROOT / "compose.dev.yaml").read_text()
    production_compose = (REPO_ROOT / "compose.yaml").read_text()
    migration_service = compose_dev.split("\n  migrate:", 1)[1].split("\n  api:", 1)[0]
    api_service = compose_dev.split("\n  api:", 1)[1].split("\n  worker:", 1)[0]
    worker_service = compose_dev.split("\n  worker:", 1)[1].split("\n  agent:", 1)[0]
    agent_service = compose_dev.split("\n  agent:", 1)[1].split("\n  web:", 1)[0]
    web_service = compose_dev.split("\n  web:", 1)[1].split("\nvolumes:", 1)[0]
    reference_setting = "DASHBOARD_METRICS_REFERENCE_TIME"
    reference_export = (
        "export DASHBOARD_METRICS_REFERENCE_TIME=2026-07-29T12:00:00Z"
    )

    assert reference_export in runner
    assert f"{reference_setting}:" in api_service
    for service in (migration_service, worker_service, agent_service, web_service):
        assert reference_setting not in service
    assert reference_setting not in production_compose


def test_local_e2e_runner_proves_deactivation_survives_api_worker_restart() -> None:
    runner = (REPO_ROOT / "scripts" / "run-local-e2e.sh").read_text()

    activation_phase = "tests/e2e/activation.spec.ts"
    deactivation_phase = "tests/e2e/deactivation-start.spec.ts"
    restart_marker = "E2E_AFTER_SERVICE_RESTART=true"
    resume_phase = "tests/e2e/restart-resume.spec.ts"
    resume_command = re.compile(
        r'E2E_AFTER_SERVICE_RESTART=true E2E_BASE_URL="http://127\.0\.0\.1:\$\{WEB_PORT\}" \\\n'
        r"\s+npm --prefix apps/web run test:e2e -- tests/e2e/restart-resume\.spec\.ts"
    )
    restart_commands = [
        line.strip().split()
        for line in runner.splitlines()
        if line.strip().startswith("compose restart")
    ]
    assert restart_commands == [["compose", "restart", "api", "worker"]]
    restart_command = " ".join(restart_commands[0])

    assert activation_phase in runner
    assert deactivation_phase in runner
    assert restart_command in runner
    assert resume_phase in runner
    assert runner.count(restart_marker) == 1
    assert len(resume_command.findall(runner)) == 1
    activation_index = runner.index(activation_phase)
    deactivation_index = runner.index(deactivation_phase)
    restart_index = runner.index(restart_command)
    restart_wait_index = runner.index("wait_for_running worker", restart_index)
    marker_index = runner.index(restart_marker)
    resume_index = runner.index(resume_phase)
    assert (
        activation_index
        < deactivation_index
        < restart_index
        < restart_wait_index
        < marker_index
        < resume_index
    )
    before_restart = runner[:restart_index]
    restart_to_resume = runner[restart_index:resume_index]
    assert restart_to_resume.count(restart_marker) == 1
    for wait_command in ("wait_for_health api", "wait_for_running worker"):
        assert before_restart.count(wait_command) == 1
        assert restart_to_resume.count(wait_command) == 1
        assert restart_to_resume.index(wait_command) < restart_to_resume.index(
            restart_marker
        )
    for retained_wait in (
        "wait_for_health postgres",
        "wait_for_health redis",
        "wait_for_health minio",
        "wait_for_health web",
    ):
        assert before_restart.count(retained_wait) == 1
        assert retained_wait not in restart_to_resume
    assert "down_stack" not in restart_to_resume


@pytest.mark.parametrize(
    ("signal_name", "expected_exit_code"),
    [("HUP", 129), ("INT", 130), ("TERM", 143)],
)
def test_local_e2e_runner_preserves_signal_exit_and_failure_logs(
    tmp_path: Path,
    signal_name: str,
    expected_exit_code: int,
) -> None:
    runner = REPO_ROOT / "scripts" / "run-local-e2e.sh"
    probe_log = tmp_path / "docker.log"
    signal_marker = tmp_path / "signal-sent"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        """#!/bin/sh
if [ "${AUTH_MODE:-}" = "local" ]; then
  auth_mode_state=local
else
  auth_mode_state=unexpected
fi
if [ -n "${LOCAL_AUTH_TOKEN:-}" ]; then
  local_token_state=configured
else
  local_token_state=missing
fi
printf '%s|%s|%s\\n' "$auth_mode_state" "$local_token_state" "$*" >> "$PROBE_LOG"
if [ ! -e "$PROBE_SIGNAL_MARKER" ]; then
  : > "$PROBE_SIGNAL_MARKER"
  kill -"$PROBE_SIGNAL" "$PPID"
fi
exit 0
"""
    )
    fake_docker.chmod(0o755)
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "PROBE_LOG": str(probe_log),
        "PROBE_SIGNAL": signal_name,
        "PROBE_SIGNAL_MARKER": str(signal_marker),
    }

    result = subprocess.run(
        ["/bin/sh", str(runner)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == expected_exit_code
    docker_calls = probe_log.read_text().splitlines()
    assert docker_calls
    assert all(call.startswith("local|configured|") for call in docker_calls)
    docker_calls = "\n".join(docker_calls)
    assert " ps" in docker_calls
    assert " logs api worker web" in docker_calls
    assert " down --volumes --remove-orphans" in docker_calls


def test_ci_runs_the_disposable_browser_journey_without_deployment() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    e2e_job = workflow.split("\n  e2e:", 1)[1].split("\n  migrations:", 1)[0]

    assert "needs: [api, agent, web]" in e2e_job
    assert "npm ci" in e2e_job
    assert "playwright install --with-deps chromium" in e2e_job
    assert "bash scripts/run-local-e2e.sh" in e2e_job
    for forbidden in ("deploy", "push", "publish"):
        assert forbidden not in e2e_job.lower()


def test_worker_secrets_are_least_privilege_and_agent_shutdown_can_drain() -> None:
    compose = (REPO_ROOT / "compose.yaml").read_text()
    worker_environment = compose.split(
        "x-worker-environment: &worker-environment", 1
    )[1].split("x-api-environment: &api-environment", 1)[0]
    worker_service = compose.split("\n  worker:", 1)[1].split("\n  agent:", 1)[0]
    agent_service = compose.split("\n  agent:", 1)[1].split("\n  api:", 1)[0]

    for forbidden_secret in (
        "CLERK_WEBHOOK_SECRET",
        "STRIPE_WEBHOOK_SECRET",
    ):
        assert forbidden_secret not in worker_environment
    for required_worker_setting in (
        "DATABASE_URL",
        "REDIS_URL",
        "STRIPE_SECRET_KEY",
        "LIVEKIT_API_SECRET",
        "TELNYX_API_KEY",
        "S3_SECRET_KEY",
        "AGENT_DISPATCH_JWT_SECRET",
        "GEMINI_API_KEY",
    ):
        assert required_worker_setting in worker_environment

    assert "environment: *worker-environment" in worker_service
    assert "environment: *api-environment" not in worker_service
    assert "stop_grace_period: 66m" in agent_service


def test_deployment_and_rollback_runbooks_define_safe_release_boundaries() -> None:
    architecture = (
        REPO_ROOT / "docs" / "architecture" / "production-deployment.md"
    ).read_text()
    deploy = (REPO_ROOT / "docs" / "runbooks" / "deploy.md").read_text()
    normalized_deploy = " ".join(deploy.replace("**", "").split())
    rollback = (REPO_ROOT / "docs" / "runbooks" / "rollback.md").read_text()

    for provider in ("AWS Paris", "Scaleway Paris", "EU managed application platform"):
        assert provider in architecture
    for criterion in (
        "data residency",
        "PostgreSQL PITR",
        "Redis TLS",
        "private networking",
        "secret management",
        "static egress IP",
        "monthly beta cost",
    ):
        assert criterion in architecture
    assert "pending explicit user approval" in architecture
    assert "Terraform" not in architecture

    release_order = (
        "backup verification",
        "migration job",
        "worker and agent",
        "API",
        "readiness",
        "web",
        "smoke test",
    )
    positions = [deploy.index(step) for step in release_order]
    assert positions == sorted(positions)
    assert "do not start the API" in deploy
    assert "previous API must be proven compatible" in deploy
    assert "with the migrated schema" in deploy
    assert "maintenance procedure" in deploy
    assert "new agent revision is compatible with the previous API" in normalized_deploy
    assert "previous web revision is compatible with the new API" in normalized_deploy
    assert "cross-service contract evidence" in normalized_deploy
    assert "stop accepting new dispatches" in deploy
    assert "active job count reaches zero" in deploy
    assert "termination grace" in deploy
    assert "pre-drain" in architecture
    assert "backward-compatible" in rollback
    assert "forward-fix" in rollback
    assert "irreversible" in rollback
