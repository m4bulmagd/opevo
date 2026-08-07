from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.config import Settings


REFERENCE_TIME = datetime(2026, 7, 29, 12, tzinfo=UTC)
BASE_SETTINGS = {
    "database_url": "sqlite+aiosqlite://",
    "redis_url": "redis://localhost:6379/0",
}


@pytest.mark.parametrize("app_env", ["development", "test"])
def test_dashboard_reference_time_is_accepted_only_in_safe_environments(
    app_env: str,
) -> None:
    settings = Settings(
        app_env=app_env,
        dashboard_metrics_reference_time=REFERENCE_TIME,
        **BASE_SETTINGS,
    )

    assert settings.dashboard_metrics_reference_time == REFERENCE_TIME


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_dashboard_reference_time_is_rejected_outside_safe_environments(
    app_env: str,
) -> None:
    with pytest.raises(
        ValidationError,
        match="DASHBOARD_METRICS_REFERENCE_TIME",
    ):
        Settings(
            app_env=app_env,
            dashboard_metrics_reference_time=REFERENCE_TIME,
            **BASE_SETTINGS,
        )


def test_dashboard_reference_time_rejects_a_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="test",
            dashboard_metrics_reference_time=datetime(2026, 7, 29, 12),
            **BASE_SETTINGS,
        )


def test_invalid_dashboard_reference_time_is_redacted() -> None:
    sentinel = "do-not-echo-this-reference-time"

    with pytest.raises(ValidationError) as caught:
        Settings(
            app_env="test",
            dashboard_metrics_reference_time=sentinel,
            **BASE_SETTINGS,
        )

    assert sentinel not in str(caught.value)


def test_dashboard_reference_time_defaults_to_none() -> None:
    settings = Settings(**BASE_SETTINGS)

    assert settings.dashboard_metrics_reference_time is None
