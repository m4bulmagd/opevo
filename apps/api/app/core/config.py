from functools import lru_cache
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str
    redis_url: str
    otel_service_name: str = "presvo-api"
    otel_exporter_otlp_endpoint: str | None = None
    realtime_enabled: bool = False
    cors_allowed_origins: str | None = None
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
    max_call_duration_seconds: int = Field(default=3600, ge=1)
    call_reconciliation_pending_stale_seconds: int = Field(default=120, ge=1)
    call_reconciliation_connected_stale_seconds: int = Field(default=3720, ge=1)
    call_reconciliation_ending_grace_seconds: int = Field(default=60, ge=1)
    call_reconciliation_finalizing_lease_seconds: int = Field(default=300, ge=1)
    call_reconciliation_max_attempts: int = Field(default=5, ge=1, le=5)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_connected_reconciliation_timeout(self) -> Self:
        minimum_timeout = self.max_call_duration_seconds + 120
        if self.call_reconciliation_connected_stale_seconds < minimum_timeout:
            raise ValueError(
                "CALL_RECONCILIATION_CONNECTED_STALE_SECONDS must be at least "
                "MAX_CALL_DURATION_SECONDS plus 120 seconds"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
