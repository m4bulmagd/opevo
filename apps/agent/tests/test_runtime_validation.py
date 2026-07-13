import pytest

from agent.config import AgentSettings
from agent.main import build_worker_options
from agent.runtime_validation import validate_agent_runtime


@pytest.fixture
def agent_settings() -> AgentSettings:
    return AgentSettings(
        app_env="production",
        livekit_url="wss://livekit.example.com",
        livekit_api_key="livekit-api-key",
        livekit_api_secret="livekit-api-secret",
        api_base_url="https://api.example.com",
        agent_internal_api_token="agent-api-token",
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
        ("agent_internal_api_token", "AGENT_INTERNAL_API_TOKEN"),
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
        agent_internal_api_token=None,
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
    monkeypatch.setattr("agent.main.get_settings", lambda: agent_settings)
    monkeypatch.setattr(
        "agent.main.validate_agent_runtime",
        lambda settings: events.append("validate"),
    )
    monkeypatch.setattr(
        "agent.main._register_inference_runners",
        lambda: events.append("initialize"),
    )

    build_worker_options()

    assert events == ["validate", "initialize"]


def test_build_worker_options_rejects_invalid_production_before_initialization(
    agent_settings: AgentSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_settings = agent_settings.model_copy(update={"livekit_api_secret": ""})
    events: list[str] = []
    monkeypatch.setattr("agent.main.get_settings", lambda: invalid_settings)
    monkeypatch.setattr(
        "agent.main._register_inference_runners",
        lambda: events.append("initialize"),
    )
    monkeypatch.setattr(
        "agent.main.WorkerOptions",
        lambda **kwargs: events.append("worker_options"),
    )

    with pytest.raises(RuntimeError, match="LIVEKIT_API_SECRET"):
        build_worker_options()

    assert events == []
