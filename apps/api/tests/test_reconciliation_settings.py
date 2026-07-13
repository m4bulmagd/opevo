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
