from dataclasses import dataclass

from app.core.config import Settings


class LiveKitClientConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LiveKitClientConfig:
    url: str
    api_key: str
    api_secret: str


def require_livekit_client_config(settings: Settings) -> LiveKitClientConfig:
    url = settings.livekit_url
    api_key = settings.livekit_api_key
    api_secret = settings.livekit_api_secret
    if (
        not isinstance(url, str)
        or not url.strip()
        or not isinstance(api_key, str)
        or not api_key.strip()
        or not isinstance(api_secret, str)
        or not api_secret.strip()
    ):
        raise LiveKitClientConfigurationError(
            "LiveKit client configuration is incomplete"
        )
    return LiveKitClientConfig(
        url=url,
        api_key=api_key,
        api_secret=api_secret,
    )
