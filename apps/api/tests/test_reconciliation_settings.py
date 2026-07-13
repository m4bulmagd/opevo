import pytest
from pydantic import ValidationError

from app.core.config import Settings


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
