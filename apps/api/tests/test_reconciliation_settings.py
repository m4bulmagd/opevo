import importlib

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from tests import reconciliation_settings


@pytest.mark.parametrize(
    "field_name",
    [
        "call_reconciliation_pending_stale_seconds",
        "call_reconciliation_connected_stale_seconds",
        "call_reconciliation_ending_grace_seconds",
        "call_reconciliation_finalizing_lease_seconds",
        "call_reconciliation_max_attempts",
        "max_call_duration_seconds",
    ],
)
@pytest.mark.parametrize("invalid_value", [0, -1])
def test_reconciliation_settings_require_positive_values(
    field_name: str,
    invalid_value: int,
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://localhost:6379/0",
            **{field_name: invalid_value},
        )


def test_reconciliation_max_attempts_rejects_values_above_absolute_cap() -> None:
    with pytest.raises(ValidationError):
        Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://localhost:6379/0",
            call_reconciliation_max_attempts=6,
        )


def test_call_duration_and_connected_reconciliation_defaults_are_aligned() -> None:
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        redis_url="redis://localhost:6379/0",
    )

    assert settings.max_call_duration_seconds == 3600
    assert settings.call_reconciliation_connected_stale_seconds == 3720


def test_connected_reconciliation_timeout_requires_two_minute_buffer() -> None:
    with pytest.raises(ValidationError):
        Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://localhost:6379/0",
            max_call_duration_seconds=3600,
            call_reconciliation_connected_stale_seconds=3719,
        )


def test_connected_reconciliation_timeout_accepts_exact_two_minute_buffer() -> None:
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        redis_url="redis://localhost:6379/0",
        max_call_duration_seconds=60,
        call_reconciliation_connected_stale_seconds=180,
    )

    assert settings.call_reconciliation_connected_stale_seconds == 180


def test_shared_reconciliation_settings_ignore_poisoned_duration_environment() -> (
    None
):
    try:
        with pytest.MonkeyPatch.context() as poisoned_environment:
            poisoned_environment.setenv("MAX_CALL_DURATION_SECONDS", "4000")
            reloaded = importlib.reload(reconciliation_settings)

            settings = reloaded.TEST_RECONCILIATION_SETTINGS
            assert settings.max_call_duration_seconds == 3600
            assert settings.call_reconciliation_pending_stale_seconds == 120
            assert settings.call_reconciliation_connected_stale_seconds == 3720
            assert settings.call_reconciliation_ending_grace_seconds == 60
            assert settings.call_reconciliation_finalizing_lease_seconds == 300
            assert settings.call_reconciliation_max_attempts == 5
    finally:
        importlib.reload(reconciliation_settings)


def test_worker_capacity_defaults_and_boundaries() -> None:
    settings = Settings(
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost",
    )
    assert settings.worker_lifecycle_max_jobs == 10
    assert settings.worker_background_max_jobs == 4
    for name, value in (
        ("worker_lifecycle_max_jobs", 0),
        ("worker_lifecycle_max_jobs", 101),
        ("worker_background_max_jobs", 0),
        ("worker_background_max_jobs", 51),
    ):
        with pytest.raises(ValidationError):
            Settings(
                database_url="sqlite+aiosqlite://",
                redis_url="redis://localhost",
                **{name: value},
            )
