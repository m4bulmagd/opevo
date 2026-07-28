from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.user import User
from app.services.call_history_service import CallHistoryService


NOW = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)


def make_call(
    *,
    call_id: str,
    user_id: UUID,
    caller_number: str | None = None,
    summary_text: str | None = None,
    summary_data: dict | None = None,
    deleted: bool = False,
    started_at: datetime = NOW,
    status: str = "completed",
) -> Call:
    return Call(
        id=UUID(call_id),
        user_id=user_id,
        caller_number=caller_number,
        status=status,
        failure_code="legacy_failure" if status == "failed" else None,
        started_at=started_at,
        ended_at=started_at,
        duration_seconds=0,
        minutes_charged=0,
        summary_text=summary_text,
        summary_data=summary_data,
        deleted_at=NOW if deleted else None,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.anyio
async def test_list_calls_returns_deterministic_page_and_matching_total(
    db_session,
    active_user,
) -> None:
    other_user = User(
        clerk_user_id="call_search_other",
        email="call-search-other@example.invalid",
    )
    db_session.add(other_user)
    await db_session.flush()

    owner_calls = [
        make_call(
            call_id=f"00000000-0000-0000-0000-00000000000{index}",
            user_id=active_user.id,
            summary_text=f"Visible call {index}",
        )
        for index in (1, 2, 3)
    ]
    db_session.add_all(
        [
            *owner_calls,
            make_call(
                call_id="00000000-0000-0000-0000-000000000004",
                user_id=active_user.id,
                summary_text="Removed owner call",
                deleted=True,
            ),
            make_call(
                call_id="00000000-0000-0000-0000-000000000005",
                user_id=other_user.id,
                summary_text="Foreign call",
            ),
        ]
    )
    await db_session.commit()

    service = CallHistoryService(db_session, recording_service=None)
    first_page = await service.list_calls(active_user.id, limit=2, offset=0)
    second_page = await service.list_calls(active_user.id, limit=2, offset=2)

    assert [item.id for item in first_page.calls] == [
        UUID("00000000-0000-0000-0000-000000000003"),
        UUID("00000000-0000-0000-0000-000000000002"),
    ]
    assert first_page.total == 3
    assert first_page.limit == 2
    assert first_page.offset == 0
    assert first_page.has_more is True
    assert [item.id for item in second_page.calls] == [
        UUID("00000000-0000-0000-0000-000000000001")
    ]
    assert second_page.total == 3
    assert second_page.has_more is False


@pytest.mark.anyio
async def test_search_matches_summary_and_caller_intent_case_insensitively(
    db_session,
    active_user,
) -> None:
    summary_call = make_call(
        call_id="00000000-0000-0000-0000-000000000011",
        user_id=active_user.id,
        caller_number="+33187000011",
        summary_text="Asked about OPENING hours",
    )
    intent_call = make_call(
        call_id="00000000-0000-0000-0000-000000000012",
        user_id=active_user.id,
        caller_number="+33187000012",
        summary_data={
            "summary_text": "Appointment request",
            "caller_intent": "Book a Consultation",
            "action_items": [],
            "sentiment": "neutral",
            "follow_up_required": False,
            "private_debug_value": "debug value",
        },
    )
    transcript_only_call = make_call(
        call_id="00000000-0000-0000-0000-000000000013",
        user_id=active_user.id,
        caller_number="+33187000013",
    )
    db_session.add_all([summary_call, intent_call, transcript_only_call])
    await db_session.flush()
    db_session.add(
        CallMessage(
            call_id=transcript_only_call.id,
            speaker="CALLER",
            text="transcript-only secret phrase",
            sequence_number=1,
        )
    )
    await db_session.commit()

    service = CallHistoryService(db_session, recording_service=None)

    opening = await service.list_calls(active_user.id, query="opening")
    consultation = await service.list_calls(active_user.id, query="CONSULTATION")
    transcript = await service.list_calls(active_user.id, query="secret phrase")
    arbitrary_json = await service.list_calls(active_user.id, query="debug value")
    prose_with_digit = await service.list_calls(active_user.id, query="invoice 2")
    blank = await service.list_calls(active_user.id, query="   ")

    assert [item.id for item in opening.calls] == [summary_call.id]
    assert [item.id for item in consultation.calls] == [intent_call.id]
    assert transcript.total == 0
    assert arbitrary_json.total == 0
    assert prose_with_digit.total == 0
    assert blank.total == 3


@pytest.mark.anyio
async def test_phone_search_normalizes_punctuation_and_requires_three_digits(
    db_session,
    active_user,
) -> None:
    phone_call = make_call(
        call_id="00000000-0000-0000-0000-000000000021",
        user_id=active_user.id,
        caller_number="+33187001234",
        summary_text="Unrelated summary",
    )
    db_session.add(phone_call)
    await db_session.commit()
    service = CallHistoryService(db_session, recording_service=None)

    international = await service.list_calls(active_user.id, query="+33 1 87")
    punctuated = await service.list_calls(active_user.id, query="(187)")
    domestic = await service.list_calls(active_user.id, query="01 87")
    too_short = await service.list_calls(active_user.id, query="87")

    assert [item.id for item in international.calls] == [phone_call.id]
    assert [item.id for item in punctuated.calls] == [phone_call.id]
    assert [item.id for item in domestic.calls] == [phone_call.id]
    assert too_short.total == 0


@pytest.mark.anyio
async def test_status_and_date_filters_compose_with_search_and_scope(
    db_session,
    active_user,
) -> None:
    other_user = User(
        clerk_user_id="call_filter_other",
        email="call-filter-other@example.invalid",
    )
    db_session.add(other_user)
    await db_session.flush()
    matching = make_call(
        call_id="00000000-0000-0000-0000-000000000031",
        user_id=active_user.id,
        summary_text="Appointment request",
        started_at=NOW,
        status="completed",
    )
    db_session.add_all(
        [
            matching,
            make_call(
                call_id="00000000-0000-0000-0000-000000000032",
                user_id=active_user.id,
                summary_text="Appointment request",
                started_at=NOW,
                status="failed",
            ),
            make_call(
                call_id="00000000-0000-0000-0000-000000000033",
                user_id=active_user.id,
                summary_text="Appointment request",
                started_at=NOW - timedelta(days=8),
                status="completed",
            ),
            make_call(
                call_id="00000000-0000-0000-0000-000000000034",
                user_id=active_user.id,
                summary_text="Appointment request",
                started_at=NOW,
                status="failed",
            ),
            make_call(
                call_id="00000000-0000-0000-0000-000000000035",
                user_id=other_user.id,
                summary_text="Appointment request",
                started_at=NOW,
                status="completed",
            ),
            make_call(
                call_id="00000000-0000-0000-0000-000000000036",
                user_id=active_user.id,
                summary_text="Appointment request",
                started_at=NOW,
                status="completed",
                deleted=True,
            ),
            make_call(
                call_id="00000000-0000-0000-0000-000000000037",
                user_id=active_user.id,
                summary_text="Different request",
                started_at=NOW,
                status="completed",
            ),
            make_call(
                call_id="00000000-0000-0000-0000-000000000038",
                user_id=active_user.id,
                summary_text="Appointment request",
                started_at=NOW + timedelta(seconds=1),
                status="completed",
            ),
        ]
    )
    await db_session.commit()

    service = CallHistoryService(db_session, recording_service=None)
    result = await service.list_calls(
        active_user.id,
        query="appointment",
        status_filter="completed",
        date_range="7d",
        now=NOW,
    )

    assert [item.id for item in result.calls] == [matching.id]
    assert result.total == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status_filter", "call_status", "included"),
    [
        ("completed", "completed", True),
        ("completed", "failed", False),
        ("failed", "failed", True),
        ("failed", "completed", False),
        ("in_progress", "pending", True),
        ("in_progress", "connected", True),
        ("in_progress", "ending", True),
        ("in_progress", "finalizing", True),
        ("in_progress", "completed", False),
    ],
)
async def test_status_filters_map_to_allowed_call_states(
    db_session,
    active_user,
    status_filter: str,
    call_status: str,
    included: bool,
) -> None:
    call = (
        make_call(
            call_id="00000000-0000-0000-0000-000000000041",
            user_id=active_user.id,
            status=call_status,
        )
    )
    db_session.add(call)
    await db_session.commit()

    result = await CallHistoryService(
        db_session,
        recording_service=None,
    ).list_calls(
        active_user.id,
        status_filter=status_filter,
    )

    assert [item.id for item in result.calls] == ([call.id] if included else [])
