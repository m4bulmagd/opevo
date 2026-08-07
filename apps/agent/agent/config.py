from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


type RuntimeEnvironment = Literal[
    "development",
    "test",
    "staging",
    "production",
]


def _normalize_runtime_environment(value: object) -> object:
    if isinstance(value, str):
        return value.strip().casefold()
    return value


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: RuntimeEnvironment = "development"

    # LiveKit
    livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None
    livekit_agent_name: str = "ai-call-agent"

    # API communication
    api_base_url: str = "http://api:8000"
    redis_url: str = "redis://localhost:6379/0"

    # AI providers
    gemini_api_key: str | None = None
    speechmatics_api_key: str | None = None
    speechmatics_turn_detection_mode: str = "adaptive"
    mistral_api_key: str | None = None
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str = "ODq5zmih8GrVes37Dizd"
    deepgram_api_key: str | None = None

    # API client
    api_timeout_seconds: float = 10.0
    api_max_retries: int = 3

    # Agent behavior
    agent_debug_streams: bool = False
    agent_min_endpointing_delay: float = 0.25
    agent_max_endpointing_delay: float = 1.5
    livekit_silero_vad_enabled: bool = True
    livekit_turn_detector_enabled: bool = True

    @field_validator("app_env", mode="before")
    @classmethod
    def normalize_app_env(cls, value: object) -> object:
        return _normalize_runtime_environment(value)


@lru_cache
def get_settings() -> AgentSettings:
    return AgentSettings()
