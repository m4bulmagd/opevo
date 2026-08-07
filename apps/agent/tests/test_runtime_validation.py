from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.config import AgentSettings, get_settings
from agent.main import build_worker_options
from agent.runtime_validation import validate_agent_runtime


ENVIRONMENT_VARIANTS = [
    (" DeVeLoPmEnT ", "development"),
    (" TEST ", "test"),
    (" StAgInG ", "staging"),
    (" PrOdUcTiOn ", "production"),
]
INVALID_ENVIRONMENTS = ["", "   ", "prodution", "preview"]


def test_direct_agent_settings_ignore_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".env").write_text(
        "APP_ENV=preview\nAPI_BASE_URL=https://poison.invalid\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("API_BASE_URL", raising=False)

    settings = AgentSettings()

    assert settings.app_env == "development"
    assert settings.api_base_url == "http://api:8000"


def test_direct_agent_settings_keep_constructor_and_process_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".env").write_text(
        "APP_ENV=preview\n"
        "API_BASE_URL=https://poison.invalid\n"
        "LIVEKIT_AGENT_NAME=dotenv-agent\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("API_BASE_URL", raising=False)
    monkeypatch.delenv("LIVEKIT_AGENT_NAME", raising=False)
    monkeypatch.setenv("LIVEKIT_AGENT_NAME", "process-agent")

    settings = AgentSettings(app_env=" TEST ")

    assert settings.app_env == "test"
    assert settings.api_base_url == "http://api:8000"
    assert settings.livekit_agent_name == "process-agent"


def test_get_settings_explicitly_loads_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".env").write_text(
        "APP_ENV= TEST \n"
        "API_BASE_URL=https://dotenv.example.com\n"
        "LIVEKIT_AGENT_NAME=dotenv-agent\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("API_BASE_URL", raising=False)
    monkeypatch.delenv("LIVEKIT_AGENT_NAME", raising=False)
    get_settings.cache_clear()

    try:
        settings = get_settings()

        assert settings.app_env == "test"
        assert settings.api_base_url == "https://dotenv.example.com"
        assert settings.livekit_agent_name == "dotenv-agent"
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    ("source_value", "canonical_value"),
    ENVIRONMENT_VARIANTS,
)
def test_agent_constructor_environment_is_canonicalized(
    source_value: str,
    canonical_value: str,
) -> None:
    settings = AgentSettings(app_env=source_value)

    assert settings.app_env == canonical_value


@pytest.mark.parametrize(
    ("source_value", "canonical_value"),
    ENVIRONMENT_VARIANTS,
)
def test_agent_process_environment_is_canonicalized(
    source_value: str,
    canonical_value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", source_value)

    settings = AgentSettings()

    assert settings.app_env == canonical_value


@pytest.mark.parametrize("app_env", INVALID_ENVIRONMENTS)
def test_agent_constructor_rejects_unknown_environment(app_env: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        AgentSettings(app_env=app_env)

    assert {tuple(error["loc"]) for error in exc_info.value.errors()} == {
        ("app_env",)
    }


@pytest.mark.parametrize("app_env", INVALID_ENVIRONMENTS)
def test_agent_process_rejects_unknown_environment(
    app_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)

    with pytest.raises(ValidationError) as exc_info:
        AgentSettings()

    assert {tuple(error["loc"]) for error in exc_info.value.errors()} == {
        ("app_env",)
    }


@pytest.mark.parametrize("app_env", [" PRODUCTION ", "PrOdUcTiOn"])
def test_agent_production_variants_cannot_bypass_runtime_validation(
    app_env: str,
) -> None:
    settings = AgentSettings(app_env=app_env)

    with pytest.raises(RuntimeError, match="LIVEKIT_URL"):
        validate_agent_runtime(settings)


@pytest.fixture
def agent_settings() -> AgentSettings:
    return AgentSettings(
        app_env="production",
        livekit_url="wss://livekit.example.com",
        livekit_api_key="livekit-api-key",
        livekit_api_secret="livekit-api-secret",
        api_base_url="https://api.example.com",
        redis_url="rediss://redis.example.com/0",
        speechmatics_api_key="speechmatics-api-key",
        gemini_api_key="gemini-api-key",
        agent_debug_streams=False,
    )


@pytest.mark.parametrize(
    ("field_name", "environment_name"),
    [
        ("livekit_url", "LIVEKIT_URL"),
        ("livekit_api_key", "LIVEKIT_API_KEY"),
        ("livekit_api_secret", "LIVEKIT_API_SECRET"),
        ("api_base_url", "API_BASE_URL"),
        ("redis_url", "REDIS_URL"),
        ("speechmatics_api_key", "SPEECHMATICS_API_KEY"),
        ("gemini_api_key", "GEMINI_API_KEY"),
    ],
)
def test_agent_production_rejects_missing_launch_setting(
    agent_settings: AgentSettings,
    field_name: str,
    environment_name: str,
) -> None:
    settings = agent_settings.model_copy(update={field_name: ""})

    with pytest.raises(RuntimeError, match=environment_name):
        validate_agent_runtime(settings)


def test_agent_production_rejects_debug_streams(
    agent_settings: AgentSettings,
) -> None:
    settings = agent_settings.model_copy(update={"agent_debug_streams": True})

    with pytest.raises(RuntimeError, match="AGENT_DEBUG_STREAMS"):
        validate_agent_runtime(settings)


def test_agent_production_does_not_require_unused_optional_providers(
    agent_settings: AgentSettings,
) -> None:
    settings = agent_settings.model_copy(
        update={
            "deepgram_api_key": None,
            "elevenlabs_api_key": None,
            "mistral_api_key": None,
        }
    )

    validate_agent_runtime(settings)


def test_agent_production_reports_every_missing_setting(
    agent_settings: AgentSettings,
) -> None:
    settings = agent_settings.model_copy(
        update={
            "livekit_api_secret": "",
            "speechmatics_api_key": "",
            "gemini_api_key": "",
        }
    )

    with pytest.raises(RuntimeError) as exc_info:
        validate_agent_runtime(settings)

    message = str(exc_info.value)
    assert "LIVEKIT_API_SECRET" in message
    assert "SPEECHMATICS_API_KEY" in message
    assert "GEMINI_API_KEY" in message


def test_agent_development_accepts_fake_providers() -> None:
    settings = AgentSettings(
        app_env="development",
        livekit_url=None,
        livekit_api_key=None,
        livekit_api_secret=None,
        speechmatics_api_key=None,
        gemini_api_key=None,
        agent_debug_streams=True,
    )

    validate_agent_runtime(settings)


def test_build_worker_options_validates_before_initializing_runtime(
    agent_settings: AgentSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "agent.main.get_settings",
        lambda: pytest.fail("explicit settings unexpectedly reloaded"),
    )
    monkeypatch.setattr(
        "agent.main.validate_agent_runtime",
        lambda settings: events.append(
            "validate_exact" if settings is agent_settings else "validate_other"
        ),
    )
    monkeypatch.setattr(
        "agent.main._register_inference_runners",
        lambda settings: events.append(
            "initialize_exact" if settings is agent_settings else "initialize_other"
        ),
    )

    build_worker_options(agent_settings)

    assert events == ["validate_exact", "initialize_exact"]


def test_build_worker_options_rejects_invalid_production_before_initialization(
    agent_settings: AgentSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_settings = agent_settings.model_copy(update={"livekit_api_secret": ""})
    events: list[str] = []
    monkeypatch.setattr(
        "agent.main._register_inference_runners",
        lambda _settings: events.append("initialize"),
    )
    monkeypatch.setattr(
        "agent.main.WorkerOptions",
        lambda **kwargs: events.append("worker_options"),
    )

    with pytest.raises(RuntimeError, match="LIVEKIT_API_SECRET"):
        build_worker_options(invalid_settings)

    assert events == []
