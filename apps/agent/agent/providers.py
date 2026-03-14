from enum import StrEnum


class PipelineMode(StrEnum):
    STT_LLM_TTS = "stt_llm_tts"
    STS = "sts"


class STTProvider(StrEnum):
    DEEPGRAM = "deepgram"
    OPENAI = "openai"
    ELEVENLABS = "elevenlabs"
    VOXTRAL = "voxtral"
    SPEECHMATICS = "speechmatics"


class LLMProvider(StrEnum):
    OPENAI = "openai"
    GEMINI = "gemini"


class TTSProvider(StrEnum):
    OPENAI = "openai"
    ELEVENLABS = "elevenlabs"
    SPEECHMATICS = "speechmatics"


class STSProvider(StrEnum):
    GEMINI = "gemini"
    OPENAI = "openai"
