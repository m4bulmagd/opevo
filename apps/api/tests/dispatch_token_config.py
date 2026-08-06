from app.core.dispatch_token import DispatchTokenConfig
from app.core.config import Settings


TEST_DISPATCH_TOKEN_CONFIG = DispatchTokenConfig(
    secret="shared-test-dispatch-secret-with-at-least-32-bytes",
    ttl_seconds=7200,
)


def settings_with_test_dispatch_token(base: Settings) -> Settings:
    return base.model_copy(
        update={
            "agent_dispatch_jwt_secret": TEST_DISPATCH_TOKEN_CONFIG.secret,
            "agent_dispatch_jwt_ttl_seconds": TEST_DISPATCH_TOKEN_CONFIG.ttl_seconds,
        }
    )
