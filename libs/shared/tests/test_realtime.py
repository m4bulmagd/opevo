from uuid import UUID

import pytest

from presvo_contracts import ContractError, create_contract, dump_contract
from presvo_contracts.realtime import (
    AgentSessionEndedEvent,
    CallFinalizedEvent,
    CallStartedEvent,
    TranscriptObservedEvent,
    parse_realtime_event,
    realtime_channel,
)


def valid_transcript_observed() -> dict[str, object]:
    return {
        "schema_version": 1,
        "type": "transcript_observed",
        "user_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "call_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "sequence_number": 1,
        "speaker": "CALLER",
        "text": "Hello, I need help.",
    }


def valid_call_started() -> dict[str, object]:
    return {
        "schema_version": 1,
        "type": "call_started",
        "user_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "call_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "room_name": "call-room-001",
    }


def valid_agent_session_ended() -> dict[str, object]:
    return {
        "schema_version": 1,
        "type": "agent_session_ended",
        "user_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "call_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "duration_seconds": 0,
    }


def valid_call_finalized() -> dict[str, object]:
    return {
        "schema_version": 1,
        "type": "call_finalized",
        "user_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "call_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "minutes_charged": 0,
        "summary_text": "The caller requested account help.",
    }


@pytest.mark.parametrize(
    ("payload", "event_type"),
    [
        (valid_transcript_observed(), TranscriptObservedEvent),
        (valid_call_started(), CallStartedEvent),
        (valid_agent_session_ended(), AgentSessionEndedEvent),
        (valid_call_finalized(), CallFinalizedEvent),
    ],
)
def test_realtime_event_variants_round_trip(
    payload: dict[str, object], event_type: type[object]
) -> None:
    event = parse_realtime_event(payload)
    assert isinstance(event, event_type)
    assert dump_contract(event) == payload


@pytest.mark.parametrize("value", [0, -1, True, 1.0, "1"])
def test_realtime_rejects_unsupported_or_coerced_schema_versions(value: object) -> None:
    payload = valid_call_started()
    payload["schema_version"] = value
    with pytest.raises(ContractError) as caught:
        parse_realtime_event(payload)
    assert caught.value.code == "unsupported_schema_version"


@pytest.mark.parametrize("field", ["user_id", "call_id"])
def test_realtime_rejects_invalid_uuids(field: str) -> None:
    payload = valid_call_started()
    payload[field] = "not-a-uuid"
    with pytest.raises(ContractError) as caught:
        parse_realtime_event(payload)
    assert caught.value.code == "invalid_payload"


@pytest.mark.parametrize("room_name", ["", "   "])
def test_call_started_rejects_empty_room_names(room_name: str) -> None:
    payload = valid_call_started()
    payload["room_name"] = room_name
    with pytest.raises(ContractError) as caught:
        parse_realtime_event(payload)
    assert caught.value.code == "invalid_payload"


@pytest.mark.parametrize("summary_text", ["x" * 8_000, None])
def test_call_finalized_accepts_summary_at_maximum_length_or_none(
    summary_text: str | None,
) -> None:
    payload = valid_call_finalized()
    payload["summary_text"] = summary_text
    assert dump_contract(parse_realtime_event(payload)) == payload


def test_call_finalized_rejects_summary_over_maximum_length() -> None:
    payload = valid_call_finalized()
    payload["summary_text"] = "x" * 8_001
    with pytest.raises(ContractError) as caught:
        parse_realtime_event(payload)
    assert caught.value.code == "invalid_payload"


@pytest.mark.parametrize("field", ["duration_seconds", "minutes_charged"])
@pytest.mark.parametrize("value", [-1, True, 1.0, "1"])
def test_realtime_usage_values_reject_negative_or_coerced_integers(
    field: str, value: object
) -> None:
    payload = valid_agent_session_ended() if field == "duration_seconds" else valid_call_finalized()
    payload[field] = value
    with pytest.raises(ContractError) as caught:
        parse_realtime_event(payload)
    assert caught.value.code == "invalid_payload"


@pytest.mark.parametrize("event_type", ["unknown", "transcript", "call_ended"])
def test_realtime_rejects_unknown_and_retired_discriminators(event_type: str) -> None:
    payload = valid_call_started()
    payload["type"] = event_type
    with pytest.raises(ContractError) as caught:
        parse_realtime_event(payload)
    assert caught.value.code == "invalid_payload"


def test_realtime_producer_rejects_extra_fields() -> None:
    payload = valid_call_started()
    payload["future"] = "not producer-safe"
    with pytest.raises(ContractError) as caught:
        create_contract(CallStartedEvent, **payload)
    assert caught.value.code == "invalid_payload"


def test_realtime_consumer_ignores_additive_fields() -> None:
    payload = valid_call_started()
    payload["future"] = {"nested": {"field": "ignored"}}
    assert dump_contract(parse_realtime_event(payload)) == valid_call_started()


def test_realtime_rejects_malformed_json() -> None:
    with pytest.raises(ContractError) as caught:
        parse_realtime_event('{"schema_version": 1')
    assert caught.value.code == "malformed_json"


def test_realtime_channel_uses_canonical_uuid() -> None:
    user_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    assert realtime_channel(user_id) == f"realtime:user:{user_id}"
