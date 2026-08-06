import importlib
from typing import Any

from livekit.agents import Agent
from livekit.agents import AgentSession

from agent.config import AgentSettings
from agent.debug_streams import InstrumentedAgent
from agent.debug_streams import StreamDebugLogger
from agent.prompt_builder import build_system_prompt
from agent.providers import LLMProvider, PipelineMode, STSProvider, STTProvider, TTSProvider


class VerificationSessionConfigurationError(RuntimeError):
    """A verification TTS-only session could not be configured safely."""


def _resolve_speechmatics_turn_detection_mode(
    settings: AgentSettings,
    plugin_module: Any,
):
    configured_mode = settings.speechmatics_turn_detection_mode.strip().lower()

    if configured_mode == "smart_turn":
        return plugin_module.TurnDetectionMode.SMART_TURN
    if configured_mode == "fixed":
        return plugin_module.TurnDetectionMode.FIXED
    if configured_mode == "external":
        return plugin_module.TurnDetectionMode.EXTERNAL
    return plugin_module.TurnDetectionMode.ADAPTIVE


def build_pipeline_config(agent_config: dict) -> dict:
    pipeline_mode = agent_config.get("pipeline_mode", PipelineMode.STT_LLM_TTS.value)
    if pipeline_mode not in {PipelineMode.STT_LLM_TTS.value, PipelineMode.STS.value}:
        raise ValueError(f"Unsupported pipeline mode: {pipeline_mode}")

    return {
        "pipeline_mode": pipeline_mode,
        "stt_provider": agent_config.get("stt_provider", STTProvider.SPEECHMATICS.value),
        "llm_provider": agent_config.get("llm_provider", LLMProvider.GEMINI.value),
        "tts_provider": agent_config.get("tts_provider", TTSProvider.SPEECHMATICS.value),
        "sts_provider": agent_config.get("sts_provider", STSProvider.GEMINI.value),
    }


def _resolve_gemini_llm(settings: AgentSettings, plugin_module: Any):
    return plugin_module.LLM(model="gemini-2.5-flash", api_key=settings.gemini_api_key)


def _resolve_gemini_api_key(settings: AgentSettings) -> str:
    if settings.gemini_api_key:
        return settings.gemini_api_key
    raise ValueError("Gemini credentials are required for sts pipeline mode")


def _default_plugin_modules(
    settings: AgentSettings,
    config: dict,
) -> dict[str, Any]:
    modules: dict[str, Any] = {}

    if (
        config["stt_provider"] == STTProvider.SPEECHMATICS.value
        or config["tts_provider"] == TTSProvider.SPEECHMATICS.value
    ):
        from livekit.plugins import speechmatics

        modules["speechmatics"] = speechmatics

    if (
        config["stt_provider"] == STTProvider.ELEVENLABS.value
        or config["tts_provider"] == TTSProvider.ELEVENLABS.value
    ):
        from livekit.plugins import elevenlabs

        modules["elevenlabs"] = elevenlabs

    if config["stt_provider"] == STTProvider.DEEPGRAM.value:
        from livekit.plugins import deepgram

        modules["deepgram"] = deepgram

    if config["llm_provider"] == LLMProvider.GEMINI.value:
        from livekit.plugins import google

        modules["google"] = google

    if settings.livekit_silero_vad_enabled:
        from livekit.plugins import silero

        modules["silero"] = silero

    if settings.livekit_turn_detector_enabled:
        from livekit.plugins.turn_detector import multilingual

        modules["turn_detector_multilingual"] = multilingual

    return modules


def _build_stt(
    settings: AgentSettings,
    config: dict,
    plugins: dict[str, Any],
):
    if config["stt_provider"] == STTProvider.SPEECHMATICS.value:
        return plugins["speechmatics"].STT(
            api_key=settings.speechmatics_api_key,
            turn_detection_mode=_resolve_speechmatics_turn_detection_mode(
                settings,
                plugins["speechmatics"],
            ),
        )

    if config["stt_provider"] == STTProvider.ELEVENLABS.value:
        return plugins["elevenlabs"].STT(model_id="scribe_v2_realtime", api_key=settings.elevenlabs_api_key)

    if config["stt_provider"] == STTProvider.DEEPGRAM.value:
        return plugins["deepgram"].STT(model="nova-3", language="multi")

    raise ValueError(f"Unsupported STT provider: {config['stt_provider']}")


def _build_llm(
    settings: AgentSettings,
    config: dict,
    plugins: dict[str, Any],
):
    if config["llm_provider"] == LLMProvider.GEMINI.value:
        return _resolve_gemini_llm(settings, plugins["google"])
    raise ValueError(f"Unsupported LLM provider: {config['llm_provider']}")


def _build_tts(
    settings: AgentSettings,
    config: dict,
    plugins: dict[str, Any],
):
    if config["tts_provider"] == TTSProvider.ELEVENLABS.value:
        return plugins["elevenlabs"].TTS(
            voice_id=settings.elevenlabs_voice_id,
            model="eleven_flash_v2_5",
            api_key=settings.elevenlabs_api_key,
        )

    if config["tts_provider"] == TTSProvider.SPEECHMATICS.value:
        return plugins["speechmatics"].TTS(
            api_key=settings.speechmatics_api_key,
        )
    raise ValueError(f"Unsupported TTS provider: {config['tts_provider']}")


def build_verification_session(
    tts_provider: str,
    *,
    settings: AgentSettings,
    plugin_modules: dict[str, Any] | None = None,
    session_cls=AgentSession,
):
    try:
        if plugin_modules is None:
            plugin = importlib.import_module(f"livekit.plugins.{tts_provider}")
        else:
            plugin = plugin_modules[tts_provider]

        if tts_provider == TTSProvider.SPEECHMATICS.value:
            api_key = (settings.speechmatics_api_key or "").strip()
            if not api_key:
                raise ValueError
            tts = plugin.TTS(api_key=api_key)
        elif tts_provider == TTSProvider.ELEVENLABS.value:
            api_key = (settings.elevenlabs_api_key or "").strip()
            voice_id = (settings.elevenlabs_voice_id or "").strip()
            if not api_key or not voice_id:
                raise ValueError
            tts = plugin.TTS(
                voice_id=voice_id,
                model="eleven_flash_v2_5",
                api_key=api_key,
            )
        else:
            raise ValueError
    except Exception:
        raise VerificationSessionConfigurationError(
            "verification TTS configuration is unavailable"
        ) from None

    return session_cls(tts=tts)


def _build_vad(settings: AgentSettings, plugins: dict[str, Any]):
    if not settings.livekit_silero_vad_enabled:
        return None
    return plugins["silero"].VAD.load()


def _build_turn_detection(settings: AgentSettings, plugins: dict[str, Any]):
    if not settings.livekit_turn_detector_enabled:
        return None
    return plugins["turn_detector_multilingual"].MultilingualModel()


def _bind_turn_detector_executor(turn_detection, inference_executor):
    if turn_detection is None or inference_executor is None:
        return turn_detection
    if hasattr(turn_detection, "_executor"):
        turn_detection._executor = inference_executor
    return turn_detection


def _build_sts_model(
    settings: AgentSettings,
    config: dict,
    plugins: dict[str, Any],
    instructions: str,
):
    if config["sts_provider"] != STSProvider.GEMINI.value:
        raise ValueError(f"Unsupported STS provider: {config['sts_provider']}")

    realtime_module = getattr(plugins["google"], "realtime", None)
    if realtime_module is None or not hasattr(realtime_module, "RealtimeModel"):
        raise ValueError("Google realtime plugin is required for sts pipeline mode")

    return realtime_module.RealtimeModel(
        model="gemini-2.5-flash-native-audio-preview-12-2025",
        instructions=instructions,
        api_key=_resolve_gemini_api_key(settings),
    )


def _build_sts_session(
    settings: AgentSettings,
    config: dict,
    plugins: dict[str, Any],
    instructions: str,
    session_cls,
):
    return session_cls(
        llm=_build_sts_model(settings, config, plugins, instructions)
    )


def build_agent_runtime(
    dispatch_metadata: dict,
    *,
    settings: AgentSettings,
    plugin_modules: dict[str, Any] | None = None,
    vad=None,
    turn_detection=None,
    inference_executor=None,
    agent_cls=Agent,
    session_cls=AgentSession,
):
    config = build_pipeline_config(dispatch_metadata)
    plugins = plugin_modules or _default_plugin_modules(settings, config)
    instructions = build_system_prompt(
        agent_name=dispatch_metadata["agent_name"],
        owner_name=dispatch_metadata["owner_name"],
        system_prompt=dispatch_metadata.get("system_prompt", ""),
        knowledge_base=dispatch_metadata.get("knowledge_base", ""),
        owner_context=dispatch_metadata.get("owner_context", ""),
    )

    if config["pipeline_mode"] == PipelineMode.STS.value:
        session = _build_sts_session(
            settings,
            config,
            plugins,
            instructions,
            session_cls,
        )
    else:
        stt = _build_stt(settings, config, plugins)
        llm = _build_llm(settings, config, plugins)
        tts = _build_tts(settings, config, plugins)
        resolved_vad = vad if vad is not None else _build_vad(settings, plugins)
        resolved_turn_detection = (
            turn_detection
            if turn_detection is not None
            else _build_turn_detection(settings, plugins)
        )
        resolved_turn_detection = _bind_turn_detector_executor(
            resolved_turn_detection, inference_executor
        )

        session_kwargs = {
            "stt": stt,
            "llm": llm,
            "tts": tts,
        }
        if resolved_vad is not None:
            session_kwargs["vad"] = resolved_vad
        if resolved_turn_detection is not None:
            session_kwargs["turn_detection"] = resolved_turn_detection

        session = session_cls(**session_kwargs)

    if agent_cls is Agent:
        agent = InstrumentedAgent(
            debug_logger=StreamDebugLogger.from_dispatch_metadata(dispatch_metadata),
            instructions=instructions,
            min_endpointing_delay=settings.agent_min_endpointing_delay,
            max_endpointing_delay=settings.agent_max_endpointing_delay,
        )
    else:
        agent = agent_cls(
            instructions=instructions,
            min_endpointing_delay=settings.agent_min_endpointing_delay,
            max_endpointing_delay=settings.agent_max_endpointing_delay,
        )
    return agent, session
