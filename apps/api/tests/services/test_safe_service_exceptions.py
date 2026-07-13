import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.call_lifecycle_service import CallLifecycleService
from app.services.recording_service import RecordingResult, RecordingService
from app.services.usage_accounting_service import UsageDebitResult


class SecretBearingStorageProvider:
    async def upload_bytes(self, *, object_key: str, data: bytes, content_type: str):
        raise RuntimeError("STORAGE_AUTHORIZATION_SENTINEL")


def build_lifecycle_service(*, recording_service, telephony_service, phone_number=None):
    call_id = uuid4()
    user_id = uuid4()
    call = SimpleNamespace(
        id=call_id,
        user_id=user_id,
        status="pending",
        summary_text=None,
    )
    session = SimpleNamespace(commit=AsyncMock())
    call_repository = SimpleNamespace(
        get_by_id=AsyncMock(return_value=call),
        mark_completed=AsyncMock(),
    )
    service = CallLifecycleService(
        session,
        call_repository=call_repository,
        message_repository=SimpleNamespace(create_many=AsyncMock()),
        usage_accounting_service=SimpleNamespace(
            debit_call=AsyncMock(
                return_value=UsageDebitResult(
                    user_id=user_id,
                    minutes_charged=2,
                    balance_before=10,
                    balance_after=0 if phone_number is not None else 8,
                    already_debited=False,
                )
            )
        ),
        phone_number_repository=SimpleNamespace(
            get_by_user_id=AsyncMock(return_value=phone_number)
        ),
        telephony_service=telephony_service,
        summary_service=SimpleNamespace(
            create_summary=AsyncMock(
                return_value=SimpleNamespace(
                    text=None,
                    data=None,
                    job_enqueued=False,
                )
            )
        ),
        recording_service=recording_service,
        notification_service=SimpleNamespace(
            create_call_completed_notification=AsyncMock(
                return_value=SimpleNamespace(job_enqueued=False)
            )
        ),
    )
    payload = {
        "call_id": str(call_id),
        "duration_seconds": 61,
        "transcript": [],
    }
    return service, payload, user_id


@pytest.mark.anyio
async def test_recording_storage_failure_does_not_render_provider_exception(caplog) -> None:
    service = RecordingService(provider=SecretBearingStorageProvider())
    call_id = "call_recording_123"
    user_id = "user_recording_123"

    with caplog.at_level(logging.ERROR):
        result = await service.store_recording(
            {
                "call_id": call_id,
                "user_id": user_id,
                "recording_bytes": b"audio",
            }
        )

    assert result.job_enqueued is False
    assert "STORAGE_AUTHORIZATION_SENTINEL" not in caplog.text
    assert "event=recording_storage_failed" in caplog.text
    assert "operation=upload_recording" in caplog.text
    assert f"call_id={call_id}" in caplog.text
    assert f"user_id={user_id}" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.anyio
async def test_outer_recording_failure_does_not_render_provider_exception(caplog) -> None:
    recording_service = SimpleNamespace(
        store_recording=AsyncMock(
            side_effect=RuntimeError("OUTER_RECORDING_TRANSCRIPT_SENTINEL")
        )
    )
    service, payload, user_id = build_lifecycle_service(
        recording_service=recording_service,
        telephony_service=SimpleNamespace(disable_number=AsyncMock()),
    )

    with caplog.at_level(logging.ERROR):
        result = await service.finalize_call(payload)

    assert result.recording_job_enqueued is False
    assert "OUTER_RECORDING_TRANSCRIPT_SENTINEL" not in caplog.text
    assert "event=call_recording_upload_failed" in caplog.text
    assert "operation=store_recording" in caplog.text
    assert f"call_id={payload['call_id']}" in caplog.text
    assert f"user_id={user_id}" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.anyio
async def test_disable_number_failure_does_not_render_provider_exception(caplog) -> None:
    telephony_service = SimpleNamespace(
        disable_number=AsyncMock(
            side_effect=RuntimeError("TELNYX_AUTHORIZATION_SENTINEL_FROM_DISABLE")
        )
    )
    service, payload, user_id = build_lifecycle_service(
        recording_service=SimpleNamespace(
            store_recording=AsyncMock(
                return_value=RecordingResult(
                    object_key=None,
                    url=None,
                    job_enqueued=False,
                )
            )
        ),
        telephony_service=telephony_service,
        phone_number=SimpleNamespace(id=uuid4()),
    )
    with caplog.at_level(logging.ERROR):
        result = await service.finalize_call(payload)

    assert result.number_disabled is False
    assert "TELNYX_AUTHORIZATION_SENTINEL_FROM_DISABLE" not in caplog.text
    assert "event=phone_number_disable_failed" in caplog.text
    assert "operation=disable_phone_number" in caplog.text
    assert f"call_id={payload['call_id']}" in caplog.text
    assert f"user_id={user_id}" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
