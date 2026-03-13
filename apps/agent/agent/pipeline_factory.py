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
