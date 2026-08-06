from app.core.config import Settings


TEST_RECONCILIATION_SETTINGS = Settings(
    app_env="test",
    database_url="sqlite+aiosqlite://",
    redis_url="redis://explicit-reconciliation.invalid/0",
)
