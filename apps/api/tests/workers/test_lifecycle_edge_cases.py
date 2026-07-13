import logging
import pytest
from uuid import uuid4

from app.models.call import Call
from app.models.usage_ledger import UsageLedger
from app.services.call_lifecycle_service import CallFinalizationResult
from app.services.recording_service import RecordingResult, RecordingService

import sys
import importlib.util
from pathlib import Path

_post_call_jobs_path = Path(__file__).parent / "test_post_call_jobs.py"
_spec = importlib.util.spec_from_file_location("test_post_call_jobs", _post_call_jobs_path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
build_lifecycle_service = _module.build_lifecycle_service


class FakeFailingStorageProvider:
    async def upload_bytes(self, *, object_key: str, data: bytes, content_type: str):
        raise OSError("STORAGE_AUTHORIZATION_SENTINEL")


class FakeExplodingRecordingService:
    async def store_recording(self, payload: dict):
        raise RuntimeError("OUTER_RECORDING_TRANSCRIPT_SENTINEL")


# T5-1: Recording upload failure (provider raises) — call should still complete
@pytest.mark.anyio
async def test_recording_upload_failure_call_still_completes(
    db_session,
    active_user,
    caplog,
) -> None:
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33111111111",
        status="pending",
    )
    db_session.add(call)
    await db_session.commit()

    service = build_lifecycle_service(
        db_session,
        recording_service=RecordingService(provider=FakeFailingStorageProvider()),
    )

    with caplog.at_level(logging.ERROR):
        result = await service.finalize_call(
            {
                "call_id": str(call.id),
                "duration_seconds": 61,
                "caller_number": "+33111111111",
                "transcript": [{"speaker": "CALLER", "text": "Hello"}],
                "recording_bytes": b"fake-audio",
            }
        )

    refreshed_call = await db_session.get(Call, call.id)
    assert refreshed_call.status == "completed"
    assert result.recording_job_enqueued is False
    assert result.recording_key is None
    assert "STORAGE_AUTHORIZATION_SENTINEL" not in caplog.text
    assert "event=recording_storage_failed" in caplog.text
    assert "operation=upload_recording" in caplog.text
    assert f"call_id={call.id}" in caplog.text
    assert f"user_id={active_user.id}" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.anyio
async def test_outer_recording_failure_does_not_render_provider_exception(
    db_session,
    active_user,
    caplog,
) -> None:
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33111111111",
        status="pending",
    )
    db_session.add(call)
    await db_session.commit()
    service = build_lifecycle_service(
        db_session,
        recording_service=FakeExplodingRecordingService(),
    )

    with caplog.at_level(logging.ERROR):
        result = await service.finalize_call(
            {
                "call_id": str(call.id),
                "duration_seconds": 61,
                "caller_number": "+33111111111",
                "transcript": [{"speaker": "CALLER", "text": "Hello"}],
            }
        )

    assert result.recording_job_enqueued is False
    assert "OUTER_RECORDING_TRANSCRIPT_SENTINEL" not in caplog.text
    assert "event=call_recording_upload_failed" in caplog.text
    assert "operation=store_recording" in caplog.text
    assert f"call_id={call.id}" in caplog.text
    assert f"user_id={active_user.id}" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


# T5-2: Empty transcript — should complete with no messages
@pytest.mark.anyio
async def test_empty_transcript_completes_with_no_messages(db_session, active_user) -> None:
    from sqlalchemy import select
    from app.models.call_message import CallMessage

    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33111111111",
        status="pending",
    )
    db_session.add(call)
    await db_session.commit()

    service = build_lifecycle_service(db_session)

    result = await service.finalize_call(
        {
            "call_id": str(call.id),
            "duration_seconds": 61,
            "caller_number": "+33111111111",
            "transcript": [],
        }
    )

    refreshed_call = await db_session.get(Call, call.id)
    assert refreshed_call.status == "completed"

    messages = (
        await db_session.execute(
            select(CallMessage).where(CallMessage.call_id == call.id)
        )
    ).scalars().all()
    assert messages == []


# T5-3: Zero duration call (duration_seconds=0) — minutes_charged should be 1
@pytest.mark.anyio
async def test_zero_duration_call_charges_one_minute(db_session, active_user) -> None:
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33111111111",
        status="pending",
    )
    db_session.add_all(
        [
            call,
            UsageLedger(
                user_id=active_user.id,
                event_type="subscription_activated",
                source_id="in_zero_duration",
                minutes_delta=5,
                balance_after=5,
            ),
        ]
    )
    await db_session.commit()

    service = build_lifecycle_service(db_session)

    result = await service.finalize_call(
        {
            "call_id": str(call.id),
            "duration_seconds": 0,
            "caller_number": "+33111111111",
            "transcript": [],
        }
    )

    assert result.minutes_charged == 1
    refreshed_call = await db_session.get(Call, call.id)
    assert refreshed_call.minutes_charged == 1
    assert refreshed_call.status == "completed"


# T5-4: Call not found (bad call_id) — should raise ValueError
@pytest.mark.anyio
async def test_call_not_found_raises_value_error(db_session, active_user) -> None:
    service = build_lifecycle_service(db_session)

    with pytest.raises(ValueError, match="Call not found"):
        await service.finalize_call(
            {
                "call_id": str(uuid4()),
                "duration_seconds": 60,
                "caller_number": "+33111111111",
                "transcript": [],
            }
        )


# T5-5: Already-completed call (status="completed") — should return early with already_completed=True
@pytest.mark.anyio
async def test_already_completed_call_returns_early(db_session, active_user) -> None:
    from sqlalchemy import select
    from app.models.call_message import CallMessage

    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33111111111",
        status="completed",
        minutes_charged=2,
        summary_text="Previous summary",
    )
    db_session.add(call)
    await db_session.commit()

    service = build_lifecycle_service(db_session)

    result = await service.finalize_call(
        {
            "call_id": str(call.id),
            "duration_seconds": 120,
            "caller_number": "+33111111111",
            "transcript": [{"speaker": "CALLER", "text": "Should not be saved"}],
        }
    )

    assert result.already_completed is True
    assert result.minutes_charged == 2
    assert result.summary_job_enqueued is False
    assert result.recording_job_enqueued is False
    assert result.notification_job_enqueued is False
    assert result.summary_text == "Previous summary"

    # No new messages should have been written
    messages = (
        await db_session.execute(
            select(CallMessage).where(CallMessage.call_id == call.id)
        )
    ).scalars().all()
    assert messages == []
