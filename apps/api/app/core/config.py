from functools import lru_cache
from typing import Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import PydanticBaseSettingsSource


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


def _requests_test_mode(
    init_settings: PydanticBaseSettingsSource,
    env_settings: PydanticBaseSettingsSource,
) -> bool:
    init_values = init_settings()
    effective_app_env = (
        init_values["app_env"]
        if "app_env" in init_values
        else env_settings().get("app_env")
    )
    return _normalize_runtime_environment(effective_app_env) == "test"


class Settings(BaseSettings):
    app_env: RuntimeEnvironment = "development"
    database_url: str
    redis_url: str
    otel_service_name: str = "presvo-api"
    otel_exporter_otlp_endpoint: str | None = None
    realtime_enabled: bool = False
    activation_flow_enabled: bool = False
    cors_allowed_origins: str | None = None
    auth_mode: Literal["clerk", "local"] = "clerk"
    local_auth_token: str = ""
    clerk_issuer: str = ""
    clerk_audience: str | None = None
    clerk_authorized_parties: str | None = None
    clerk_jwt_key: str | None = None
    clerk_jwks_url: str | None = None
    clerk_jwks_cache_ttl_seconds: float = Field(
        default=300.0, ge=30.0, le=3600.0, allow_inf_nan=False
    )
    clerk_jwks_stale_grace_seconds: float = Field(
        default=600.0, ge=1.0, le=3600.0, allow_inf_nan=False
    )
    clerk_jwks_connect_timeout_seconds: float = Field(
        default=0.5, ge=0.05, le=10.0, allow_inf_nan=False
    )
    clerk_jwks_read_timeout_seconds: float = Field(
        default=1.0, ge=0.05, le=10.0, allow_inf_nan=False
    )
    clerk_jwks_pool_timeout_seconds: float = Field(
        default=0.25, ge=0.05, le=10.0, allow_inf_nan=False
    )
    clerk_jwks_total_timeout_seconds: float = Field(
        default=2.0, ge=0.05, le=10.0, allow_inf_nan=False
    )
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
    stripe_billing_portal_configuration_id: str | None = None
    telnyx_api_key: str | None = None
    telnyx_active_connection_id: str | None = None
    telnyx_disabled_connection_id: str | None = None
    telnyx_ordering_enabled: bool = False
    carrier_lookup_mode: Literal["fake", "telnyx"] = "fake"
    telephony_mode: Literal["fake", "telnyx"] = "fake"
    billing_mode: Literal["fake", "stripe"] = "fake"
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
    worker_lifecycle_max_jobs: int = Field(default=10, ge=1, le=100)
    worker_background_max_jobs: int = Field(default=4, ge=1, le=50)
    dashboard_metrics_reference_time: AwareDatetime | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        if _requests_test_mode(init_settings, env_settings):
            return init_settings, env_settings, file_secret_settings
        return init_settings, env_settings, dotenv_settings, file_secret_settings

    @field_validator("app_env", mode="before")
    @classmethod
    def normalize_app_env(cls, value: object) -> object:
        return _normalize_runtime_environment(value)

    @model_validator(mode="after")
    def validate_connected_reconciliation_timeout(self) -> Self:
        minimum_timeout = self.max_call_duration_seconds + 120
        if self.call_reconciliation_connected_stale_seconds < minimum_timeout:
            raise ValueError(
                "CALL_RECONCILIATION_CONNECTED_STALE_SECONDS must be at least "
                "MAX_CALL_DURATION_SECONDS plus 120 seconds"
            )
        return self

    @model_validator(mode="after")
    def validate_dashboard_metrics_reference_time(self) -> Self:
        if (
            self.dashboard_metrics_reference_time is not None
            and self.app_env not in {"development", "test"}
        ):
            raise ValueError(
                "DASHBOARD_METRICS_REFERENCE_TIME is supported only in "
                "development or test"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
