import os

from livekit.agents import Agent
from livekit.agents import AgentSession

from agent.debug_streams import InstrumentedAgent
from agent.debug_streams import StreamDebugLogger
from agent.prompt_builder import build_system_prompt
from agent.providers import LLMProvider, PipelineMode, STSProvider, STTProvider, TTSProvider


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
    if pipeline_mode == PipelineMode.STS.value:
        raise ValueError("sts pipeline mode is not enabled yet")

    return {
        "pipeline_mode": PipelineMode.STT_LLM_TTS.value,
        "stt_provider": agent_config.get("stt_provider", STTProvider.SPEECHMATICS.value),
        "llm_provider": agent_config.get("llm_provider", LLMProvider.GEMINI.value),
        "tts_provider": agent_config.get("tts_provider", TTSProvider.SPEECHMATICS.value),
        "sts_provider": agent_config.get("sts_provider", STSProvider.GEMINI.value),
    }


def _resolve_openai_llm(plugin_module):
    llm_cls = getattr(plugin_module, "LLM", None)
    if llm_cls is not None:
        return llm_cls(model="gpt-4.1-mini")
    return plugin_module.responses.LLM(model="gpt-4.1-mini")


def _resolve_gemini_llm(plugin_module):
    gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    llm_cls = getattr(plugin_module, "LLM", None)
    if llm_cls is not None:
        return llm_cls(model="gemini-3-flash-preview", api_key=gemini_api_key)
    return plugin_module.LLM(model="gemini-3-flash-preview", api_key=gemini_api_key)


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

    if config["llm_provider"] == LLMProvider.OPENAI.value:
        from livekit.plugins import openai

        modules["openai"] = openai

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
    if config["llm_provider"] == LLMProvider.OPENAI.value:
        return _resolve_openai_llm(plugins["openai"])
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


def build_agent_runtime(
    dispatch_metadata: dict,
    *,
    plugin_modules: dict[str, object] | None = None,
    agent_cls=Agent,
    session_cls=AgentSession,
):
    config = build_pipeline_config(dispatch_metadata)
    plugins = plugin_modules or _default_plugin_modules(config)

    stt = _build_stt(config, plugins)
    llm = _build_llm(config, plugins)
    tts = _build_tts(config, plugins)

    session = session_cls(
        stt=stt,
        llm=llm,
        tts=tts,
    )
    instructions = build_system_prompt(
        agent_name=dispatch_metadata["agent_name"],
        owner_name=dispatch_metadata["owner_name"],
        system_prompt=dispatch_metadata.get("system_prompt", ""),
        knowledge_base=dispatch_metadata.get("knowledge_base", ""),
    )
    if agent_cls is Agent:
        agent = InstrumentedAgent(
            debug_logger=StreamDebugLogger.from_dispatch_metadata(dispatch_metadata),
            instructions=instructions,
        )
    else:
        agent = agent_cls(instructions=instructions)
    return agent, session
