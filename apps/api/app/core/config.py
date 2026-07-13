from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str
    redis_url: str
    cors_allowed_origins: str | None = None
    agent_internal_api_token: str | None = None
    clerk_issuer: str = ""
    clerk_audience: str | None = None
    clerk_jwt_key: str | None = None
    clerk_jwks_url: str | None = None
    clerk_webhook_secret: str | None = None
    livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None
    livekit_agent_name: str = "ai-call-agent"
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_price_starter: str | None = None
    stripe_checkout_success_url: str | None = None
    stripe_checkout_cancel_url: str | None = None
    stripe_billing_portal_return_url: str | None = None
    telnyx_api_key: str | None = None
    telnyx_active_connection_id: str | None = None
    telnyx_disabled_connection_id: str | None = None
    telnyx_ordering_enabled: bool = False
    gemini_api_key: str | None = None
    summary_provider: str = "gemini"
    summary_model: str = "gemini-2.5-flash"
    storage_bucket_name: str = "recordings"
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"
    firebase_credentials_json: str | None = None
    agent_dispatch_jwt_secret: str | None = None
    agent_dispatch_jwt_ttl_seconds: int = 7200

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
