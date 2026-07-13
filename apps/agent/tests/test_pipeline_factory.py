import pytest

from agent.config import get_settings
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


class FakeGoogleRealtimePlugin:
    class LLM:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class realtime:
        class RealtimeModel:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs


class FakeElevenLabsPlugin:
    class STT:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class TTS:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs


class FakeSileroPlugin:
    class VAD:
        @staticmethod
        def load(**kwargs):
            return {"plugin": "silero", "kwargs": kwargs}


class FakeTurnDetectorModule:
    class multilingual:
        class MultilingualModel:
            def __init__(self) -> None:
                self.plugin = "turn_detector"
                self._executor = None


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


def test_pipeline_factory_accepts_sts_mode() -> None:
    config = build_pipeline_config({"pipeline_mode": "sts"})

    assert config["pipeline_mode"] == "sts"
    assert config["sts_provider"] == "gemini"


def test_pipeline_factory_rejects_removed_openai_provider() -> None:
    try:
        build_agent_runtime(
            {
                "agent_name": "Ava",
                "owner_name": "Sam",
                "system_prompt": "Be helpful.",
                "knowledge_base": "Hours 9-5",
                "pipeline_mode": "stt_llm_tts",
                "stt_provider": "speechmatics",
                "llm_provider": "openai",
                "tts_provider": "speechmatics",
            },
            plugin_modules={
                "speechmatics": FakeSpeechmaticsPlugin,
                "silero": FakeSileroPlugin,
                "turn_detector_multilingual": FakeTurnDetectorModule.multilingual,
            },
            agent_cls=FakeAgent,
            session_cls=FakeSession,
        )
    except ValueError as exc:
        assert "Unsupported LLM provider: openai" in str(exc)
    else:
        raise AssertionError("Expected openai provider to raise")


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
            "silero": FakeSileroPlugin,
            "turn_detector_multilingual": FakeTurnDetectorModule.multilingual,
        },
        agent_cls=FakeAgent,
        session_cls=FakeSession,
    )

    assert agent.kwargs["min_endpointing_delay"] == 0.25
    assert agent.kwargs["max_endpointing_delay"] == 1.5
    assert session.kwargs["stt"].kwargs["turn_detection_mode"] == "adaptive"
    assert session.kwargs["llm"].kwargs["model"] == "gemini-2.5-flash"
    assert "api_key" in session.kwargs["tts"].kwargs
    assert session.kwargs["vad"]["plugin"] == "silero"
    assert session.kwargs["turn_detection"].plugin == "turn_detector"


def test_pipeline_factory_does_not_print_prompt_content(capsys) -> None:
    prompt_sentinel = "SYSTEM_PROMPT_SENTINEL_SECRET"
    knowledge_sentinel = "KNOWLEDGE_BASE_SENTINEL_SECRET"

    build_agent_runtime(
        {
            "agent_name": "Ava",
            "owner_name": "Sam",
            "system_prompt": prompt_sentinel,
            "knowledge_base": knowledge_sentinel,
            "pipeline_mode": "stt_llm_tts",
            "stt_provider": "speechmatics",
            "llm_provider": "gemini",
            "tts_provider": "speechmatics",
        },
        plugin_modules={
            "google": FakeGooglePlugin,
            "speechmatics": FakeSpeechmaticsPlugin,
            "silero": FakeSileroPlugin,
            "turn_detector_multilingual": FakeTurnDetectorModule.multilingual,
        },
        agent_cls=FakeAgent,
        session_cls=FakeSession,
    )

    captured = capsys.readouterr()
    assert prompt_sentinel not in captured.out
    assert knowledge_sentinel not in captured.out


def test_pipeline_factory_binds_turn_detector_executor_when_provided() -> None:
    _agent, session = build_agent_runtime(
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
            "silero": FakeSileroPlugin,
            "turn_detector_multilingual": FakeTurnDetectorModule.multilingual,
        },
        inference_executor="executor",
        agent_cls=FakeAgent,
        session_cls=FakeSession,
    )

    assert session.kwargs["turn_detection"]._executor == "executor"


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
            "silero": FakeSileroPlugin,
            "turn_detector_multilingual": FakeTurnDetectorModule.multilingual,
        },
        session_cls=FakeSession,
    )

    assert isinstance(agent, InstrumentedAgent)


def test_pipeline_factory_builds_sts_runtime_with_gemini_realtime(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    get_settings.cache_clear()

    agent, session = build_agent_runtime(
        {
            "agent_name": "Ava",
            "owner_name": "Sam",
            "system_prompt": "Be helpful.",
            "knowledge_base": "Hours 9-5",
            "pipeline_mode": "sts",
            "sts_provider": "gemini",
        },
        plugin_modules={"google": FakeGoogleRealtimePlugin},
        agent_cls=FakeAgent,
        session_cls=FakeSession,
    )

    assert agent.kwargs["min_endpointing_delay"] == 0.25
    assert session.kwargs["llm"].kwargs["api_key"] == "test-key"
    assert "stt" not in session.kwargs
    assert "tts" not in session.kwargs
    assert "vad" not in session.kwargs
    assert "turn_detection" not in session.kwargs


def test_pipeline_factory_rejects_sts_without_google_credentials(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="Gemini credentials"):
        build_agent_runtime(
            {
                "agent_name": "Ava",
                "owner_name": "Sam",
                "pipeline_mode": "sts",
                "sts_provider": "gemini",
            },
            plugin_modules={"google": FakeGoogleRealtimePlugin},
            agent_cls=FakeAgent,
            session_cls=FakeSession,
        )


def test_pipeline_factory_rejects_unsupported_sts_provider(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="Unsupported STS provider"):
        build_agent_runtime(
            {
                "agent_name": "Ava",
                "owner_name": "Sam",
                "pipeline_mode": "sts",
                "sts_provider": "other",
            },
            plugin_modules={"google": FakeGoogleRealtimePlugin},
            agent_cls=FakeAgent,
            session_cls=FakeSession,
        )
