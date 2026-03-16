import os

from livekit.agents import Agent
from livekit.agents import AgentSession

from agent.debug_streams import InstrumentedAgent
from agent.debug_streams import StreamDebugLogger
from agent.prompt_builder import build_system_prompt
from agent.providers import LLMProvider, PipelineMode, STSProvider, STTProvider, TTSProvider


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    return float(raw_value)


def _resolve_speechmatics_turn_detection_mode(plugin_module):
    configured_mode = os.getenv("SPEECHMATICS_TURN_DETECTION_MODE", "adaptive").strip().lower()

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


def _resolve_gemini_llm(plugin_module):
    gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    llm_cls = getattr(plugin_module, "LLM", None)
    if llm_cls is not None:
        return llm_cls(model="gemini-2.5-flash", api_key=gemini_api_key)
    return plugin_module.LLM(model="gemini-2.5-flash", api_key=gemini_api_key)


def _resolve_gemini_api_key() -> str:
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if api_key:
        return api_key
    raise ValueError("Gemini credentials are required for sts pipeline mode")


def _default_plugin_modules(config: dict) -> dict[str, object]:
    modules: dict[str, object] = {}

    if config["stt_provider"] == STTProvider.SPEECHMATICS.value or config["tts_provider"] == TTSProvider.SPEECHMATICS.value:
        from livekit.plugins import speechmatics

        modules["speechmatics"] = speechmatics

    if config["stt_provider"] == STTProvider.DEEPGRAM.value:
        from livekit.plugins import deepgram

        modules["deepgram"] = deepgram

    if config["llm_provider"] == LLMProvider.GEMINI.value:
        from livekit.plugins import google

        modules["google"] = google

    if _env_bool("LIVEKIT_SILERO_VAD_ENABLED", True):
        from livekit.plugins import silero

        modules["silero"] = silero

    if _env_bool("LIVEKIT_TURN_DETECTOR_ENABLED", True):
        from livekit.plugins.turn_detector import multilingual

        modules["turn_detector_multilingual"] = multilingual

    return modules


def _build_stt(config: dict, plugins: dict[str, object]):
    if config["stt_provider"] == STTProvider.SPEECHMATICS.value:
        speechmatics_api_key = os.getenv("SPEECHMATICS_API_KEY")
        return plugins["speechmatics"].STT(
            api_key=speechmatics_api_key,
            turn_detection_mode=_resolve_speechmatics_turn_detection_mode(plugins["speechmatics"]),
        )

    if config["stt_provider"] == STTProvider.ELEVENLABS.value:
        elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")
        return plugins["elevenlabs"].STT(model_id="scribe_v2_realtime", api_key=elevenlabs_api_key)

    if config["stt_provider"] == STTProvider.DEEPGRAM.value:
        return plugins["deepgram"].STT(model="nova-3", language="multi")

    raise ValueError(f"Unsupported STT provider: {config['stt_provider']}")


def _build_llm(config: dict, plugins: dict[str, object]):
    if config["llm_provider"] == LLMProvider.GEMINI.value:
        return _resolve_gemini_llm(plugins["google"])
    raise ValueError(f"Unsupported LLM provider: {config['llm_provider']}")


def _build_tts(config: dict, plugins: dict[str, object]):
    if config["tts_provider"] == TTSProvider.ELEVENLABS.value:
        tts_voice_id = os.getenv("ELEVENLABS_VOICE_ID", "ODq5zmih8GrVes37Dizd")
        elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")
        return plugins["elevenlabs"].TTS(
            voice_id=tts_voice_id,
            model="eleven_flash_v2_5",
            api_key=elevenlabs_api_key,
        )

    if config["tts_provider"] == TTSProvider.SPEECHMATICS.value:
        speechmatics_api_key = os.getenv("SPEECHMATICS_API_KEY")
        return plugins["speechmatics"].TTS(
            api_key=speechmatics_api_key,
        )
    raise ValueError(f"Unsupported TTS provider: {config['tts_provider']}")


def _build_vad(plugins: dict[str, object]):
    if not _env_bool("LIVEKIT_SILERO_VAD_ENABLED", True):
        return None
    return plugins["silero"].VAD.load()


def _build_turn_detection(plugins: dict[str, object]):
    if not _env_bool("LIVEKIT_TURN_DETECTOR_ENABLED", True):
        return None
    return plugins["turn_detector_multilingual"].MultilingualModel()


def _bind_turn_detector_executor(turn_detection, inference_executor):
    if turn_detection is None or inference_executor is None:
        return turn_detection
    if hasattr(turn_detection, "_executor"):
        turn_detection._executor = inference_executor
    return turn_detection


def _build_sts_model(config: dict, plugins: dict[str, object], instructions: str):
    if config["sts_provider"] != STSProvider.GEMINI.value:
        raise ValueError(f"Unsupported STS provider: {config['sts_provider']}")

    realtime_module = getattr(plugins["google"], "realtime", None)
    if realtime_module is None or not hasattr(realtime_module, "RealtimeModel"):
        raise ValueError("Google realtime plugin is required for sts pipeline mode")

    return realtime_module.RealtimeModel(
        model="gemini-2.5-flash-native-audio-preview-12-2025",
        instructions=instructions,
        api_key=_resolve_gemini_api_key(),
    )


def _build_sts_session(config: dict, plugins: dict[str, object], instructions: str, session_cls):
    return session_cls(llm=_build_sts_model(config, plugins, instructions))


def build_agent_runtime(
    dispatch_metadata: dict,
    *,
    plugin_modules: dict[str, object] | None = None,
    vad=None,
    turn_detection=None,
    inference_executor=None,
    agent_cls=Agent,
    session_cls=AgentSession,
):
    config = build_pipeline_config(dispatch_metadata)
    plugins = plugin_modules or _default_plugin_modules(config)
    instructions = build_system_prompt(
        agent_name=dispatch_metadata["agent_name"],
        owner_name=dispatch_metadata["owner_name"],
        system_prompt=dispatch_metadata.get("system_prompt", ""),
        knowledge_base=dispatch_metadata.get("knowledge_base", ""),
    )

    if config["pipeline_mode"] == PipelineMode.STS.value:
        session = _build_sts_session(config, plugins, instructions, session_cls)
    else:
        stt = _build_stt(config, plugins)
        llm = _build_llm(config, plugins)
        tts = _build_tts(config, plugins)
        resolved_vad = vad if vad is not None else _build_vad(plugins)
        resolved_turn_detection = (
            turn_detection if turn_detection is not None else _build_turn_detection(plugins)
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
            min_endpointing_delay=_env_float("AGENT_MIN_ENDPOINTING_DELAY", 0.25),
            max_endpointing_delay=_env_float("AGENT_MAX_ENDPOINTING_DELAY", 1.5),
        )
    else:
        agent = agent_cls(
            instructions=instructions,
            min_endpointing_delay=_env_float("AGENT_MIN_ENDPOINTING_DELAY", 0.25),
            max_endpointing_delay=_env_float("AGENT_MAX_ENDPOINTING_DELAY", 1.5),
        )
    return agent, session
