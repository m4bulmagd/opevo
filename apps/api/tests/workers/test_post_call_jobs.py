import pytest

from app.services.call_lifecycle_service import CallLifecycleService


@pytest.mark.anyio
async def test_call_completion_persists_usage_and_enqueues_jobs(db_session, active_user) -> None:
    service = CallLifecycleService(db_session)

    result = await service.finalize_call(
        {
            "user_id": active_user.id,
            "call_id": "call_123",
            "duration_seconds": 61,
            "minutes_remaining": 10,
        }
    )

    assert result.minutes_charged == 2
    assert result.summary_job_enqueued is True


@pytest.mark.anyio
async def test_minute_exhaustion_disables_number(db_session, active_user) -> None:
    service = CallLifecycleService(db_session)

    result = await service.finalize_call(
        {
            "user_id": active_user.id,
            "call_id": "call_456",
            "duration_seconds": 61,
            "minutes_remaining": 1,
        }
    )

    assert result.number_disabled is True
