from collections.abc import Sequence

from agent.config import AgentSettings


PRODUCTION_REQUIRED_SETTINGS = (
    "livekit_url",
    "livekit_api_key",
    "livekit_api_secret",
    "api_base_url",
    "agent_internal_api_token",
    "redis_url",
    "speechmatics_api_key",
    "gemini_api_key",
)


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def _require(settings: AgentSettings, names: Sequence[str]) -> list[str]:
    return [name.upper() for name in names if _is_missing(getattr(settings, name))]


def validate_agent_runtime(settings: AgentSettings) -> None:
    if settings.app_env.strip().lower() != "production":
        return

    invalid = _require(settings, PRODUCTION_REQUIRED_SETTINGS)
    if settings.agent_debug_streams:
        invalid.append("AGENT_DEBUG_STREAMS")

    if invalid:
        raise RuntimeError(
            f"Missing or invalid required production settings: {', '.join(invalid)}"
        )
