from pathlib import Path

from pydantic import ValidationError
import pytest

from app.core.config import Settings


POISONED_DOTENV = """\
APP_ENV=development
DATABASE_URL=postgresql+asyncpg://poison:poison@127.0.0.1:1/poison
REDIS_URL=redis://127.0.0.1:1/0
OTEL_SERVICE_NAME=dotenv-poison-sentinel
ACTIVATION_FLOW_ENABLED=true
CLERK_JWT_KEY=dotenv-static-key
CLERK_JWKS_URL=https://poison.example.invalid/jwks.json
"""


def _write_dotenv(tmp_path: Path, content: str = POISONED_DOTENV) -> None:
    (tmp_path / ".env").write_text(content, encoding="utf-8")


@pytest.mark.parametrize("app_env", ["test", " TEST ", "TeSt"])
def test_explicit_test_environment_ignores_dotenv(
    app_env: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dotenv(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "development")

    settings = Settings(
        app_env=app_env,
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
        clerk_jwt_key="constructor-static-key",
        clerk_jwks_url=None,
    )

    assert settings.database_url == "sqlite+aiosqlite://"
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.otel_service_name == "presvo-api"
    assert settings.activation_flow_enabled is False
    assert settings.clerk_jwt_key == "constructor-static-key"
    assert settings.clerk_jwks_url is None


def test_process_test_environment_makes_settings_ignore_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dotenv(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", " TeSt ")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite://")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CLERK_JWT_KEY", "process-static-key")
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)
    settings = Settings()

    assert settings.otel_service_name == "presvo-api"
    assert settings.activation_flow_enabled is False
    assert settings.clerk_jwt_key == "process-static-key"
    assert settings.clerk_jwks_url is None


def test_development_without_pre_dotenv_app_env_loads_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dotenv(tmp_path)
    monkeypatch.chdir(tmp_path)
    for name in (
        "APP_ENV",
        "DATABASE_URL",
        "REDIS_URL",
        "OTEL_SERVICE_NAME",
        "ACTIVATION_FLOW_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings()

    assert settings.app_env == "development"
    assert settings.database_url == (
        "postgresql+asyncpg://poison:poison@127.0.0.1:1/poison"
    )
    assert settings.redis_url == "redis://127.0.0.1:1/0"
    assert settings.otel_service_name == "dotenv-poison-sentinel"
    assert settings.activation_flow_enabled is True


def test_app_env_selected_only_by_dotenv_does_not_preempt_that_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dotenv(tmp_path, POISONED_DOTENV.replace("APP_ENV=development", "APP_ENV=test"))
    monkeypatch.chdir(tmp_path)
    for name in ("APP_ENV", "DATABASE_URL", "REDIS_URL", "OTEL_SERVICE_NAME"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.otel_service_name == "dotenv-poison-sentinel"


@pytest.mark.parametrize("app_env", ["", "   ", "development", "contest"])
def test_non_test_constructor_values_keep_dotenv_enabled(
    app_env: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dotenv(tmp_path)
    monkeypatch.chdir(tmp_path)

    settings = Settings(
        app_env=app_env,
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
    )

    assert settings.app_env == app_env
    assert settings.otel_service_name == "dotenv-poison-sentinel"


@pytest.mark.parametrize("app_env", ["", "   ", "development", "contest"])
def test_non_test_process_values_keep_dotenv_enabled(
    app_env: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dotenv(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", app_env)

    settings = Settings(
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
    )

    assert settings.app_env == app_env
    assert settings.otel_service_name == "dotenv-poison-sentinel"


def test_constructor_process_and_dotenv_precedence_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dotenv(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://process/db")
    monkeypatch.setenv("REDIS_URL", "redis://process:6379/0")

    settings = Settings(
        app_env="development",
        database_url="sqlite+aiosqlite://constructor",
    )

    assert settings.app_env == "development"
    assert settings.database_url == "sqlite+aiosqlite://constructor"
    assert settings.redis_url == "redis://process:6379/0"
    assert settings.otel_service_name == "dotenv-poison-sentinel"


def test_test_mode_retains_file_secret_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets_directory = tmp_path / "secrets"
    secrets_directory.mkdir()
    (secrets_directory / "database_url").write_text(
        "sqlite+aiosqlite://file-secret",
        encoding="utf-8",
    )
    (secrets_directory / "redis_url").write_text(
        "redis://file-secret:6379/0",
        encoding="utf-8",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    settings = Settings(app_env="test", _secrets_dir=secrets_directory)

    assert settings.database_url == "sqlite+aiosqlite://file-secret"
    assert settings.redis_url == "redis://file-secret:6379/0"


def test_test_mode_does_not_supply_missing_required_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(app_env="test")

    errors = {tuple(error["loc"]) for error in exc_info.value.errors()}
    assert ("database_url",) in errors
    assert ("redis_url",) in errors
