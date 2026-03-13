import os

from livekit.agents import Agent
from livekit.agents import AgentSession

from agent.prompt_builder import build_system_prompt
from agent.providers import LLMProvider, PipelineMode, STSProvider, STTProvider, TTSProvider


def build_pipeline_config(agent_config: dict) -> dict:
    pipeline_mode = agent_config.get("pipeline_mode", PipelineMode.STT_LLM_TTS.value)
    if pipeline_mode == PipelineMode.STS.value:
        raise ValueError("sts pipeline mode is not enabled yet")

    return {
        "pipeline_mode": PipelineMode.STT_LLM_TTS.value,
        "stt_provider": agent_config.get("stt_provider", STTProvider.DEEPGRAM.value),
        "llm_provider": agent_config.get("llm_provider", LLMProvider.OPENAI.value),
        "tts_provider": agent_config.get("tts_provider", TTSProvider.OPENAI.value),
        "sts_provider": agent_config.get("sts_provider", STSProvider.GEMINI.value),
    }


def _resolve_openai_llm(plugin_module):
    llm_cls = getattr(plugin_module, "LLM", None)
    if llm_cls is not None:
        return llm_cls(model="gpt-4.1-mini")
    return plugin_module.responses.LLM(model="gpt-4.1-mini")


def _default_plugin_modules() -> dict[str, object]:
    from livekit.plugins import deepgram
    from livekit.plugins import elevenlabs
    from livekit.plugins import openai

    return {
        "deepgram": deepgram,
        "openai": openai,
        "elevenlabs": elevenlabs,
    }


def build_agent_runtime(
    dispatch_metadata: dict,
    *,
    plugin_modules: dict[str, object] | None = None,
    agent_cls=Agent,
    session_cls=AgentSession,
):
    config = build_pipeline_config(dispatch_metadata)
    plugins = plugin_modules or _default_plugin_modules()

    stt = plugins["deepgram"].STT(model="nova-3", language="multi")
    llm = _resolve_openai_llm(plugins["openai"])
    tts_voice_id = os.getenv("ELEVENLABS_VOICE_ID", "ODq5zmih8GrVes37Dizd")
    tts = plugins["elevenlabs"].TTS(voice_id=tts_voice_id, model="eleven_flash_v2_5")

    session = session_cls(
        stt=stt,
        llm=llm,
        tts=tts,
    )
    agent = agent_cls(
        instructions=build_system_prompt(
            agent_name=dispatch_metadata["agent_name"],
            owner_name=dispatch_metadata["owner_name"],
            system_prompt=dispatch_metadata.get("system_prompt", ""),
            knowledge_base=dispatch_metadata.get("knowledge_base", ""),
        )
    )
    return agent, session
