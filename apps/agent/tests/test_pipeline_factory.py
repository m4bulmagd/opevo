import inspect
from importlib.metadata import version
import pytest

from agent.config import AgentSettings
from agent import pipeline_factory
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


class FakeAgent:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class FakeSession:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


DEFAULT_DISPATCH_METADATA = {
    "agent_name": "Ava",
    "owner_name": "Sam",
    "system_prompt": "Be helpful.",
    "knowledge_base": "Hours 9-5",
    "pipeline_mode": "stt_llm_tts",
    "stt_provider": "speechmatics",
    "llm_provider": "gemini",
    "tts_provider": "speechmatics",
}

COMPLETE_FAKE_PLUGINS = {
    "deepgram": FakeDeepgramPlugin,
    "elevenlabs": FakeElevenLabsPlugin,
    "google": FakeGooglePlugin,
    "speechmatics": FakeSpeechmaticsPlugin,
    "silero": FakeSileroPlugin,
    "turn_detector_multilingual": FakeTurnDetectorModule.multilingual,
}


def make_settings(**overrides: object) -> AgentSettings:
    settings = AgentSettings(
        gemini_api_key="gemini-test-key",
        speechmatics_api_key="speechmatics-test-key",
        elevenlabs_api_key="elevenlabs-test-key",
        elevenlabs_voice_id="voice-id",
        livekit_silero_vad_enabled=True,
        livekit_turn_detector_enabled=True,
    )
    return settings.model_copy(update=overrides)


@pytest.mark.parametrize(
    ("tts_provider", "plugin", "credential_name"),
    [
        ("speechmatics", FakeSpeechmaticsPlugin, "speechmatics_api_key"),
        ("elevenlabs", FakeElevenLabsPlugin, "elevenlabs_api_key"),
    ],
)
def test_verification_session_builds_only_the_selected_tts(
    monkeypatch: pytest.MonkeyPatch,
    tts_provider: str,
    plugin: object,
    credential_name: str,
) -> None:
    settings = make_settings()
    forbidden = [
        "_default_plugin_modules",
        "_build_stt",
        "_build_llm",
        "_build_sts_model",
        "_build_vad",
        "_build_turn_detection",
        "build_system_prompt",
        "InstrumentedAgent",
        "StreamDebugLogger",
    ]
    for name in forbidden:
        monkeypatch.setattr(
            pipeline_factory,
            name,
            lambda *_args, _name=name, **_kwargs: pytest.fail(
                f"forbidden verification builder called: {_name}"
            ),
        )

    session = pipeline_factory.build_verification_session(
        tts_provider,
        settings=settings,
        plugin_modules={tts_provider: plugin},
        session_cls=FakeSession,
    )

    assert set(session.kwargs) == {"tts"}
    assert session.kwargs["tts"].kwargs["api_key"] == getattr(
        settings,
        credential_name,
    )


@pytest.mark.parametrize("tts_provider", ["speechmatics", "elevenlabs"])
def test_verification_session_imports_only_the_selected_plugin(
    monkeypatch: pytest.MonkeyPatch,
    tts_provider: str,
) -> None:
    imported: list[str] = []
    plugin = (
        FakeSpeechmaticsPlugin
        if tts_provider == "speechmatics"
        else FakeElevenLabsPlugin
    )
    monkeypatch.setattr(
        pipeline_factory.importlib,
        "import_module",
        lambda module_name: imported.append(module_name) or plugin,
    )
    settings = make_settings()

    pipeline_factory.build_verification_session(
        tts_provider,
        settings=settings,
        session_cls=FakeSession,
    )

    assert imported == [f"livekit.plugins.{tts_provider}"]


@pytest.mark.parametrize(
    ("tts_provider", "settings"),
    [
        (
            "speechmatics",
            make_settings(speechmatics_api_key=""),
        ),
        (
            "elevenlabs",
            make_settings(elevenlabs_api_key=""),
        ),
    ],
)
def test_verification_session_rejects_missing_credentials_safely(
    monkeypatch: pytest.MonkeyPatch,
    tts_provider: str,
    settings: AgentSettings,
) -> None:
    with pytest.raises(
        pipeline_factory.VerificationSessionConfigurationError,
        match="verification TTS configuration is unavailable",
    ) as caught:
        pipeline_factory.build_verification_session(
            tts_provider,
            settings=settings,
            plugin_modules={
                "speechmatics": FakeSpeechmaticsPlugin,
                "elevenlabs": FakeElevenLabsPlugin,
            },
            session_cls=FakeSession,
        )

    assert "test-key" not in str(caught.value)


def test_verification_session_rejects_missing_selected_plugin_safely(
) -> None:
    with pytest.raises(
        pipeline_factory.VerificationSessionConfigurationError,
        match="verification TTS configuration is unavailable",
    ):
        pipeline_factory.build_verification_session(
            "speechmatics",
            settings=make_settings(),
            plugin_modules={},
            session_cls=FakeSession,
        )


@pytest.mark.parametrize(
    ("stt_provider", "tts_provider"),
    [
        ("elevenlabs", "speechmatics"),
        ("speechmatics", "elevenlabs"),
    ],
)
def test_default_plugin_modules_loads_elevenlabs_for_each_speech_role(
    stt_provider: str,
    tts_provider: str,
) -> None:
    modules = pipeline_factory._default_plugin_modules(
        make_settings(
            livekit_silero_vad_enabled=False,
            livekit_turn_detector_enabled=False,
        ),
        {
            "stt_provider": stt_provider,
            "llm_provider": "unsupported-for-this-focused-test",
            "tts_provider": tts_provider,
        }
    )

    assert modules["elevenlabs"].__name__ == "livekit.plugins.elevenlabs"


def test_deepgram_advertised_stt_path_has_pinned_plugin_and_loader(
) -> None:
    modules = pipeline_factory._default_plugin_modules(
        make_settings(
            livekit_silero_vad_enabled=False,
            livekit_turn_detector_enabled=False,
        ),
        {
            "stt_provider": "deepgram",
            "llm_provider": "unsupported-for-this-focused-test",
            "tts_provider": "unsupported-for-this-focused-test",
        }
    )

    assert version("livekit-plugins-deepgram") == "1.6.9"
    assert modules["deepgram"].__name__ == "livekit.plugins.deepgram"


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


def test_pipeline_factory_rejects_unknown_pipeline_mode() -> None:
    with pytest.raises(ValueError, match="Unsupported pipeline mode"):
        build_pipeline_config({"pipeline_mode": "custom"})


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
            settings=make_settings(),
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
        DEFAULT_DISPATCH_METADATA,
        settings=make_settings(),
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

    assert agent.kwargs["turn_handling"] == {
        "endpointing": {"min_delay": 0.25, "max_delay": 1.5}
    }
    assert "min_endpointing_delay" not in agent.kwargs
    assert "max_endpointing_delay" not in agent.kwargs
    assert session.kwargs["stt"].kwargs["turn_detection_mode"] == "adaptive"
    assert session.kwargs["llm"].kwargs["model"] == "gemini-2.5-flash"
    assert "api_key" in session.kwargs["tts"].kwargs
    assert session.kwargs["vad"]["plugin"] == "silero"
    assert (
        session.kwargs["turn_handling"]["turn_detection"].plugin
        == "turn_detector"
    )
    assert "turn_detection" not in session.kwargs


def test_pipeline_factory_uses_public_elevenlabs_stt_model_option() -> None:
    _agent, session = build_agent_runtime(
        {
            **DEFAULT_DISPATCH_METADATA,
            "stt_provider": "elevenlabs",
        },
        settings=make_settings(),
        plugin_modules={
            "elevenlabs": FakeElevenLabsPlugin,
            "google": FakeGooglePlugin,
            "speechmatics": FakeSpeechmaticsPlugin,
            "silero": FakeSileroPlugin,
            "turn_detector_multilingual": FakeTurnDetectorModule.multilingual,
        },
        agent_cls=FakeAgent,
        session_cls=FakeSession,
    )

    assert session.kwargs["stt"].kwargs["model"] == "scribe_v2_realtime"
    assert "model_id" not in session.kwargs["stt"].kwargs


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
        settings=make_settings(),
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


def test_pipeline_factory_leaves_turn_detector_execution_to_livekit() -> None:
    assert (
        "inference_executor"
        not in inspect.signature(build_agent_runtime).parameters
    )


def test_pipeline_factory_wraps_default_agent_with_debug_instrumentation() -> None:
    agent, _session = build_agent_runtime(
        DEFAULT_DISPATCH_METADATA,
        settings=make_settings(),
        plugin_modules={
            "google": FakeGooglePlugin,
            "speechmatics": FakeSpeechmaticsPlugin,
            "silero": FakeSileroPlugin,
            "turn_detector_multilingual": FakeTurnDetectorModule.multilingual,
        },
        session_cls=FakeSession,
    )

    assert isinstance(agent, InstrumentedAgent)


@pytest.mark.parametrize(
    ("environment_value", "settings_value"),
    [("true", False), ("false", True)],
)
def test_pipeline_debug_logger_uses_explicit_settings_when_environment_conflicts(
    monkeypatch: pytest.MonkeyPatch,
    environment_value: str,
    settings_value: bool,
) -> None:
    monkeypatch.setenv("AGENT_DEBUG_STREAMS", environment_value)

    agent, _session = build_agent_runtime(
        DEFAULT_DISPATCH_METADATA,
        settings=make_settings(agent_debug_streams=settings_value),
        plugin_modules=COMPLETE_FAKE_PLUGINS,
        session_cls=FakeSession,
    )

    assert isinstance(agent, InstrumentedAgent)
    assert agent._debug_logger.enabled is settings_value


def test_agent_runtime_empty_plugin_registry_does_not_load_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pipeline_factory,
        "_default_plugin_modules",
        lambda *_args, **_kwargs: pytest.fail("explicit registry loaded defaults"),
    )

    with pytest.raises(
        pipeline_factory.AgentPipelineConfigurationError,
        match="Required pipeline plugin is unavailable: speechmatics",
    ):
        build_agent_runtime(
            DEFAULT_DISPATCH_METADATA,
            settings=make_settings(
                livekit_silero_vad_enabled=False,
                livekit_turn_detector_enabled=False,
            ),
            plugin_modules={},
            agent_cls=FakeAgent,
            session_cls=FakeSession,
        )


@pytest.mark.parametrize(
    ("dispatch_metadata", "plugin_modules", "missing_plugin"),
    [
        (
            {
                **DEFAULT_DISPATCH_METADATA,
                "stt_provider": "deepgram",
            },
            {
                "google": FakeGooglePlugin,
                "speechmatics": FakeSpeechmaticsPlugin,
            },
            "deepgram",
        ),
        (
            DEFAULT_DISPATCH_METADATA,
            {"speechmatics": FakeSpeechmaticsPlugin},
            "google",
        ),
        (
            {
                **DEFAULT_DISPATCH_METADATA,
                "stt_provider": "deepgram",
            },
            {
                "deepgram": FakeDeepgramPlugin,
                "google": FakeGooglePlugin,
            },
            "speechmatics",
        ),
        (
            {
                **DEFAULT_DISPATCH_METADATA,
                "pipeline_mode": "sts",
                "sts_provider": "gemini",
            },
            {},
            "google",
        ),
    ],
)
def test_agent_runtime_partial_registry_reports_selected_missing_plugin_without_defaults(
    monkeypatch: pytest.MonkeyPatch,
    dispatch_metadata: dict[str, object],
    plugin_modules: dict[str, object],
    missing_plugin: str,
) -> None:
    monkeypatch.setattr(
        pipeline_factory,
        "_default_plugin_modules",
        lambda *_args, **_kwargs: pytest.fail("explicit registry loaded defaults"),
    )
    settings = make_settings(
        gemini_api_key="credential-sentinel",
        livekit_silero_vad_enabled=False,
        livekit_turn_detector_enabled=False,
    )

    with pytest.raises(pipeline_factory.AgentPipelineConfigurationError) as caught:
        build_agent_runtime(
            dispatch_metadata,
            settings=settings,
            plugin_modules=plugin_modules,
            agent_cls=FakeAgent,
            session_cls=FakeSession,
        )

    assert str(caught.value) == (
        f"Required pipeline plugin is unavailable: {missing_plugin}"
    )
    assert "credential-sentinel" not in str(caught.value)


@pytest.mark.parametrize(
    ("settings_overrides", "plugin_modules", "missing_plugin"),
    [
        (
            {
                "livekit_silero_vad_enabled": True,
                "livekit_turn_detector_enabled": False,
            },
            {
                "google": FakeGooglePlugin,
                "speechmatics": FakeSpeechmaticsPlugin,
            },
            "silero",
        ),
        (
            {
                "livekit_silero_vad_enabled": False,
                "livekit_turn_detector_enabled": True,
            },
            {
                "google": FakeGooglePlugin,
                "speechmatics": FakeSpeechmaticsPlugin,
            },
            "turn_detector_multilingual",
        ),
    ],
)
def test_agent_runtime_explicit_registry_requires_enabled_optional_plugin(
    monkeypatch: pytest.MonkeyPatch,
    settings_overrides: dict[str, object],
    plugin_modules: dict[str, object],
    missing_plugin: str,
) -> None:
    monkeypatch.setattr(
        pipeline_factory,
        "_default_plugin_modules",
        lambda *_args, **_kwargs: pytest.fail("explicit registry loaded defaults"),
    )

    with pytest.raises(
        pipeline_factory.AgentPipelineConfigurationError,
        match=f"Required pipeline plugin is unavailable: {missing_plugin}",
    ):
        build_agent_runtime(
            DEFAULT_DISPATCH_METADATA,
            settings=make_settings(**settings_overrides),
            plugin_modules=plugin_modules,
            agent_cls=FakeAgent,
            session_cls=FakeSession,
        )


@pytest.mark.parametrize(
    ("dispatch_metadata", "default_plugins", "expected_config"),
    [
        (
            DEFAULT_DISPATCH_METADATA,
            COMPLETE_FAKE_PLUGINS,
            {
                "pipeline_mode": "stt_llm_tts",
                "stt_provider": "speechmatics",
                "llm_provider": "gemini",
                "tts_provider": "speechmatics",
                "sts_provider": "gemini",
            },
        ),
        (
            {
                **DEFAULT_DISPATCH_METADATA,
                "pipeline_mode": "sts",
                "sts_provider": "gemini",
            },
            {"google": FakeGoogleRealtimePlugin},
            {
                "pipeline_mode": "sts",
                "stt_provider": "speechmatics",
                "llm_provider": "gemini",
                "tts_provider": "speechmatics",
                "sts_provider": "gemini",
            },
        ),
    ],
)
def test_agent_runtime_none_registry_loads_defaults_for_selected_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    dispatch_metadata: dict[str, object],
    default_plugins: dict[str, object],
    expected_config: dict[str, object],
) -> None:
    loaded_configs: list[dict[str, object]] = []

    def load_defaults(
        _settings: AgentSettings,
        config: dict[str, object],
    ) -> dict[str, object]:
        loaded_configs.append(config.copy())
        return default_plugins

    monkeypatch.setattr(pipeline_factory, "_default_plugin_modules", load_defaults)

    build_agent_runtime(
        dispatch_metadata,
        settings=make_settings(),
        plugin_modules=None,
        agent_cls=FakeAgent,
        session_cls=FakeSession,
    )

    assert loaded_configs == [expected_config]


def test_pipeline_factory_builds_sts_runtime_from_explicit_settings_when_environment_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "conflicting-environment-key")

    agent, session = build_agent_runtime(
        {
            "agent_name": "Ava",
            "owner_name": "Sam",
            "system_prompt": "Be helpful.",
            "knowledge_base": "Hours 9-5",
            "pipeline_mode": "sts",
            "sts_provider": "gemini",
        },
        settings=make_settings(gemini_api_key="test-key"),
        plugin_modules={"google": FakeGoogleRealtimePlugin},
        agent_cls=FakeAgent,
        session_cls=FakeSession,
    )

    assert agent.kwargs["turn_handling"] == {
        "endpointing": {"min_delay": 0.25, "max_delay": 1.5}
    }
    assert "min_endpointing_delay" not in agent.kwargs
    assert "max_endpointing_delay" not in agent.kwargs
    assert session.kwargs["llm"].kwargs["api_key"] == "test-key"
    assert "stt" not in session.kwargs
    assert "tts" not in session.kwargs
    assert "vad" not in session.kwargs
    assert "turn_detection" not in session.kwargs
    assert "turn_handling" not in session.kwargs


def test_pipeline_factory_rejects_sts_without_google_credentials() -> None:
    with pytest.raises(ValueError, match="Gemini credentials"):
        build_agent_runtime(
            {
                "agent_name": "Ava",
                "owner_name": "Sam",
                "pipeline_mode": "sts",
                "sts_provider": "gemini",
            },
            settings=make_settings(gemini_api_key=""),
            plugin_modules={"google": FakeGoogleRealtimePlugin},
            agent_cls=FakeAgent,
            session_cls=FakeSession,
        )


def test_pipeline_factory_rejects_unsupported_sts_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported STS provider"):
        build_agent_runtime(
            {
                "agent_name": "Ava",
                "owner_name": "Sam",
                "pipeline_mode": "sts",
                "sts_provider": "other",
            },
            settings=make_settings(gemini_api_key="test-key"),
            plugin_modules={"google": FakeGoogleRealtimePlugin},
            agent_cls=FakeAgent,
            session_cls=FakeSession,
        )
