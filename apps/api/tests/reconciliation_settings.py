from app.core.config import Settings


TEST_RECONCILIATION_SETTINGS = Settings(
    app_env="test",
    database_url="sqlite+aiosqlite://",
    redis_url="redis://explicit-reconciliation.invalid/0",
    call_reconciliation_pending_stale_seconds=120,
    call_reconciliation_connected_stale_seconds=3720,
    call_reconciliation_ending_grace_seconds=60,
    call_reconciliation_finalizing_lease_seconds=300,
    call_reconciliation_max_attempts=5,
)
