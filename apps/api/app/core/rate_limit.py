from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import Settings


limiter = Limiter(
    key_func=get_remote_address,
)


def configure_rate_limiter(settings: Settings) -> Limiter:
    limiter.enabled = settings.app_env.strip().lower() != "test"
    return limiter
