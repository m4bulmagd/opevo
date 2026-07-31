from uuid import UUID

import pytest

from presvo_contracts import ContractError, create_contract, dump_contract, parse_contract
from presvo_contracts.completion import (
    CallCompletionAcknowledgement,
    CallCompletionRequest,
    VerificationCompletionAcknowledgement,
    VerificationCompletionRequest,
)


def valid_segment(sequence_number: int = 1) -> dict[str, object]:
    return {"sequence_number": sequence_number, "speaker": "CALLER", "text": "Bonjour"}


def valid_call_completion() -> dict[str, object]:
    return {"schema_version": 1, "duration_seconds": 0, "transcript": [valid_segment()]}


@pytest.mark.parametrize("value", [0, 1])
def test_call_completion_accepts_nonnegative_integer_durations(value: int) -> None:
    completion = parse_contract(
        CallCompletionRequest, {**valid_call_completion(), "duration_seconds": value}
    )
    assert completion.duration_seconds == value


@pytest.mark.parametrize("value", [-1, True, 0.0, "0"])
def test_call_completion_rejects_negative_or_coerced_durations(value: object) -> None:
    with pytest.raises(ContractError) as caught:
        parse_contract(CallCompletionRequest, {**valid_call_completion(), "duration_seconds": value})
    assert caught.value.code == "invalid_payload"


@pytest.mark.parametrize("size", [0, 2_000])
def test_call_completion_accepts_transcript_size_boundaries(size: int) -> None:
    transcript = [valid_segment(index + 1) for index in range(size)]
    completion = parse_contract(
        CallCompletionRequest, {**valid_call_completion(), "transcript": transcript}
    )
    assert len(completion.transcript) == size


def test_call_completion_rejects_too_many_transcript_segments() -> None:
    transcript = [valid_segment(index + 1) for index in range(2_001)]
    with pytest.raises(ContractError) as caught:
        parse_contract(CallCompletionRequest, {**valid_call_completion(), "transcript": transcript})
    assert caught.value.code == "invalid_payload"


def test_call_completion_rejects_invalid_nested_transcript_segment() -> None:
    invalid_segment = {**valid_segment(), "speaker": "SYSTEM"}
    with pytest.raises(ContractError) as caught:
        parse_contract(
            CallCompletionRequest,
            {**valid_call_completion(), "transcript": [invalid_segment]},
        )
    assert caught.value.code == "invalid_payload"


def test_call_completion_transcript_is_deeply_immutable() -> None:
    completion = parse_contract(CallCompletionRequest, valid_call_completion())
    assert isinstance(completion.transcript, tuple)
    with pytest.raises(Exception):
        completion.transcript[0].text = "Changed"  # type: ignore[misc]


def test_call_completion_round_trips_as_json_array() -> None:
    completion = parse_contract(CallCompletionRequest, valid_call_completion())
    assert dump_contract(completion) == valid_call_completion()


def test_call_completion_producer_rejects_extras() -> None:
    payload = valid_call_completion()
    payload["future"] = "rejected"
    with pytest.raises(ContractError) as caught:
        create_contract(CallCompletionRequest, **payload)
    assert caught.value.code == "invalid_payload"


def test_call_completion_consumer_ignores_extras() -> None:
    payload = valid_call_completion()
    payload["future"] = "ignored"
    completion = parse_contract(CallCompletionRequest, payload)
    assert dump_contract(completion) == valid_call_completion()


def test_call_completion_producer_rejects_nested_transcript_extras() -> None:
    payload = valid_call_completion()
    payload["transcript"] = [{**valid_segment(), "future": "rejected"}]
    with pytest.raises(ContractError) as caught:
        create_contract(CallCompletionRequest, **payload)
    assert caught.value.code == "invalid_payload"


def test_call_completion_consumer_ignores_nested_transcript_extras() -> None:
    payload = valid_call_completion()
    payload["transcript"] = [{**valid_segment(), "future": "ignored"}]
    completion = parse_contract(CallCompletionRequest, payload)
    assert dump_contract(completion) == valid_call_completion()


@pytest.mark.parametrize("job_id", ["job-123", "  job-123  "])
def test_call_completion_acknowledgement_accepts_and_strips_nonblank_job_ids(job_id: str) -> None:
    acknowledgement = parse_contract(
        CallCompletionAcknowledgement,
        {"schema_version": 1, "status": "accepted", "queued": True, "job_id": job_id},
    )
    assert acknowledgement.status == "accepted"
    assert acknowledgement.queued is True
    assert acknowledgement.job_id == "job-123"


@pytest.mark.parametrize("job_id", ["", "   "])
def test_call_completion_acknowledgement_rejects_blank_job_ids(job_id: str) -> None:
    with pytest.raises(ContractError) as caught:
        parse_contract(
            CallCompletionAcknowledgement,
            {"schema_version": 1, "status": "accepted", "queued": True, "job_id": job_id},
        )
    assert caught.value.code == "invalid_payload"


@pytest.mark.parametrize("status", ["queued", "verified", ""])
def test_call_completion_acknowledgement_rejects_non_accepted_status(status: str) -> None:
    with pytest.raises(ContractError) as caught:
        parse_contract(
            CallCompletionAcknowledgement,
            {"schema_version": 1, "status": status, "queued": True, "job_id": "job-123"},
        )
    assert caught.value.code == "invalid_payload"


@pytest.mark.parametrize("queued", [False, 1, "true"])
def test_call_completion_acknowledgement_rejects_non_literal_queued_values(
    queued: object,
) -> None:
    with pytest.raises(ContractError) as caught:
        parse_contract(
            CallCompletionAcknowledgement,
            {"schema_version": 1, "status": "accepted", "queued": queued, "job_id": "job-123"},
        )
    assert caught.value.code == "invalid_payload"


def test_call_completion_acknowledgement_round_trips() -> None:
    payload = {"schema_version": 1, "status": "accepted", "queued": True, "job_id": "job-123"}
    acknowledgement = parse_contract(CallCompletionAcknowledgement, payload)
    assert dump_contract(acknowledgement) == payload


def test_verification_completion_request_round_trips() -> None:
    payload = {"schema_version": 1}
    request = parse_contract(VerificationCompletionRequest, payload)
    assert dump_contract(request) == payload


def test_verification_completion_request_producer_rejects_extra_body_fields() -> None:
    with pytest.raises(ContractError) as caught:
        create_contract(VerificationCompletionRequest, future="rejected")
    assert caught.value.code == "invalid_payload"


def test_verification_completion_request_consumer_ignores_additive_body_fields() -> None:
    request = parse_contract(
        VerificationCompletionRequest,
        {"schema_version": 1, "future": "ignored"},
    )
    assert dump_contract(request) == {"schema_version": 1}


def test_verification_completion_acknowledgement_parses_uuid_and_round_trips() -> None:
    payload = {
        "schema_version": 1,
        "status": "verified",
        "session_id": "12345678-1234-5678-1234-567812345678",
    }
    acknowledgement = parse_contract(VerificationCompletionAcknowledgement, payload)
    assert acknowledgement.session_id == UUID("12345678-1234-5678-1234-567812345678")
    assert dump_contract(acknowledgement) == payload


def test_verification_completion_acknowledgement_rejects_invalid_uuid() -> None:
    with pytest.raises(ContractError) as caught:
        parse_contract(
            VerificationCompletionAcknowledgement,
            {"schema_version": 1, "status": "verified", "session_id": "not-a-uuid"},
        )
    assert caught.value.code == "invalid_payload"
