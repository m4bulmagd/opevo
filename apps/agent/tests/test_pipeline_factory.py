from agent.pipeline_factory import build_agent_runtime
from agent.pipeline_factory import build_pipeline_config
from agent.debug_streams import InstrumentedAgent


class FakeSpeechmaticsPlugin:
    class TurnDetectionMode:
        ADAPTIVE = "adaptive"
        SMART_TURN = "smart_turn"

    class STT:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class TTS:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs


class FakeDeepgramPlugin:
    class STT:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs


class FakeGooglePlugin:
    class LLM:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs


class FakeElevenLabsPlugin:
    class STT:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

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
    assert config["stt_provider"] == "speechmatics"
    assert config["llm_provider"] == "gemini"
    assert config["tts_provider"] == "speechmatics"


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
            "stt_provider": "speechmatics",
            "llm_provider": "gemini",
            "tts_provider": "speechmatics",
        },
        plugin_modules={
            "deepgram": FakeDeepgramPlugin,
            "google": FakeGooglePlugin,
            "speechmatics": FakeSpeechmaticsPlugin,
        },
        agent_cls=FakeAgent,
        session_cls=FakeSession,
    )

    assert "Be helpful." in agent.kwargs["instructions"]
    assert "Hours 9-5" in agent.kwargs["instructions"]
    assert session.kwargs["stt"].kwargs["turn_detection_mode"] == "adaptive"
    assert session.kwargs["llm"].kwargs["model"] == "gemini-3-flash-preview"
    assert "api_key" in session.kwargs["tts"].kwargs


def test_pipeline_factory_wraps_default_agent_with_debug_instrumentation() -> None:
    agent, _session = build_agent_runtime(
        {
            "agent_name": "Ava",
            "owner_name": "Sam",
            "system_prompt": "Be helpful.",
            "knowledge_base": "Hours 9-5",
            "pipeline_mode": "stt_llm_tts",
            "stt_provider": "speechmatics",
            "llm_provider": "gemini",
            "tts_provider": "speechmatics",
        },
        plugin_modules={
            "google": FakeGooglePlugin,
            "speechmatics": FakeSpeechmaticsPlugin,
        },
        session_cls=FakeSession,
    )

    assert isinstance(agent, InstrumentedAgent)
