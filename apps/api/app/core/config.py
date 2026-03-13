from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str
    redis_url: str
    clerk_issuer: str = ""
    clerk_audience: str | None = None
    clerk_jwt_secret: str | None = None
    clerk_webhook_secret: str | None = None
    livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None
    livekit_agent_name: str = "ai-call-agent"
    stripe_webhook_secret: str | None = None
    telnyx_api_key: str | None = None
    telnyx_active_connection_id: str | None = None
    telnyx_disabled_connection_id: str | None = None
    storage_bucket_name: str = "recordings"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
