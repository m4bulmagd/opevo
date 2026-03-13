from agent.pipeline_factory import build_agent_runtime
from agent.pipeline_factory import build_pipeline_config


class FakeDeepgramPlugin:
    class STT:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs


class FakeOpenAIPlugin:
    class LLM:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs


class FakeElevenLabsPlugin:
    class TTS:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs


class FakeAgent:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class FakeSession:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def test_pipeline_factory_defaults_to_stt_llm_tts() -> None:
    config = build_pipeline_config({})

    assert config["pipeline_mode"] == "stt_llm_tts"
    assert config["stt_provider"] == "deepgram"
    assert config["llm_provider"] == "openai"
    assert config["tts_provider"] == "openai"


def test_pipeline_factory_rejects_sts_when_not_enabled() -> None:
    try:
        build_pipeline_config({"pipeline_mode": "sts"})
    except ValueError as exc:
        assert "not enabled" in str(exc)
    else:
        raise AssertionError("Expected sts mode to raise")


def test_pipeline_factory_builds_agent_runtime_with_live_providers() -> None:
    agent, session = build_agent_runtime(
        {
            "agent_name": "Ava",
            "owner_name": "Sam",
            "system_prompt": "Be helpful.",
            "knowledge_base": "Hours 9-5",
            "pipeline_mode": "stt_llm_tts",
            "stt_provider": "deepgram",
            "llm_provider": "openai",
            "tts_provider": "elevenlabs",
        },
        plugin_modules={
            "deepgram": FakeDeepgramPlugin,
            "openai": FakeOpenAIPlugin,
            "elevenlabs": FakeElevenLabsPlugin,
        },
        agent_cls=FakeAgent,
        session_cls=FakeSession,
    )

    assert "Be helpful." in agent.kwargs["instructions"]
    assert "Hours 9-5" in agent.kwargs["instructions"]
    assert session.kwargs["stt"].kwargs["model"] == "nova-3"
    assert session.kwargs["llm"].kwargs["model"] == "gpt-4.1-mini"
    assert session.kwargs["tts"].kwargs["voice_id"] == "ODq5zmih8GrVes37Dizd"
