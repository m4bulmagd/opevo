from enum import StrEnum


class PipelineMode(StrEnum):
    STT_LLM_TTS = "stt_llm_tts"
    STS = "sts"


class STTProvider(StrEnum):
    DEEPGRAM = "deepgram"
    ELEVENLABS = "elevenlabs"
    VOXTRAL = "voxtral"
    SPEECHMATICS = "speechmatics"


class LLMProvider(StrEnum):
    GEMINI = "gemini"


class TTSProvider(StrEnum):
    ELEVENLABS = "elevenlabs"
    SPEECHMATICS = "speechmatics"


class STSProvider(StrEnum):
    GEMINI = "gemini"
