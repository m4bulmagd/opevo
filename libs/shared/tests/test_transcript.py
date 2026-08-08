import pytest

from opevo_contracts import ContractError, create_contract, dump_contract, parse_contract
from opevo_contracts.transcript import (
    TRANSCRIPT_TEXT_MAX_LENGTH,
    TranscriptAppendAcknowledgement,
    TranscriptAppendRequest,
    TranscriptSegment,
    TranscriptSpeaker,
)


def valid_segment() -> dict[str, object]:
    return {"sequence_number": 1, "speaker": "CALLER", "text": "Bonjour"}


def valid_request() -> dict[str, object]:
    return {"schema_version": 1, "segment": valid_segment()}


@pytest.mark.parametrize("value", [1, 2])
def test_transcript_segment_accepts_positive_integer_sequence_numbers(value: int) -> None:
    segment = TranscriptSegment.model_validate({**valid_segment(), "sequence_number": value})
    assert segment.sequence_number == value


@pytest.mark.parametrize("value", [0, True, 1.0, "1"])
def test_transcript_segment_rejects_non_positive_or_coerced_sequence_numbers(value: object) -> None:
    with pytest.raises(Exception):
        TranscriptSegment.model_validate({**valid_segment(), "sequence_number": value})


@pytest.mark.parametrize("value", ["x", "x" * TRANSCRIPT_TEXT_MAX_LENGTH])
def test_transcript_segment_accepts_text_at_exact_length_boundaries(value: str) -> None:
    segment = TranscriptSegment.model_validate({**valid_segment(), "text": value})
    assert segment.text == value


@pytest.mark.parametrize("value", ["", "   ", "x" * (TRANSCRIPT_TEXT_MAX_LENGTH + 1)])
def test_transcript_segment_rejects_empty_or_too_long_text(value: str) -> None:
    with pytest.raises(Exception):
        TranscriptSegment.model_validate({**valid_segment(), "text": value})


def test_transcript_segment_strips_text_whitespace() -> None:
    segment = TranscriptSegment.model_validate({**valid_segment(), "text": "  Bonjour  "})
    assert segment.text == "Bonjour"


@pytest.mark.parametrize("speaker", ["CALLER", "AGENT"])
def test_transcript_segment_accepts_exact_speaker_vocabulary(speaker: str) -> None:
    segment = TranscriptSegment.model_validate({**valid_segment(), "speaker": speaker})
    assert segment.speaker.value == speaker


@pytest.mark.parametrize("speaker", ["caller", "AGENT ", "SYSTEM", ""])
def test_transcript_segment_rejects_unknown_or_unnormalized_speakers(speaker: str) -> None:
    with pytest.raises(Exception):
        TranscriptSegment.model_validate({**valid_segment(), "speaker": speaker})


def test_transcript_segment_is_immutable() -> None:
    segment = TranscriptSegment.model_validate(valid_segment())
    with pytest.raises(Exception):
        segment.text = "Changed"  # type: ignore[misc]


def test_producer_rejects_nested_transcript_extras() -> None:
    payload = valid_request()
    payload["segment"] = {**valid_segment(), "future": "rejected"}
    with pytest.raises(ContractError) as caught:
        create_contract(TranscriptAppendRequest, **payload)
    assert caught.value.code == "invalid_payload"


def test_consumer_ignores_nested_transcript_extras() -> None:
    payload = valid_request()
    payload["segment"] = {**valid_segment(), "future": "ignored"}
    parsed = parse_contract(TranscriptAppendRequest, payload)
    assert dump_contract(parsed) == valid_request()


def test_transcript_append_request_round_trips() -> None:
    request = parse_contract(TranscriptAppendRequest, valid_request())
    assert dump_contract(request) == valid_request()


@pytest.mark.parametrize("status", ["stored", "duplicate"])
def test_transcript_append_acknowledgement_accepts_exact_status_values(status: str) -> None:
    payload = {"schema_version": 1, "status": status, "sequence_number": 1}
    acknowledgement = parse_contract(TranscriptAppendAcknowledgement, payload)
    assert dump_contract(acknowledgement) == payload


def test_transcript_append_acknowledgement_rejects_unknown_status() -> None:
    with pytest.raises(ContractError) as caught:
        parse_contract(
            TranscriptAppendAcknowledgement,
            {"schema_version": 1, "status": "accepted", "sequence_number": 1},
        )
    assert caught.value.code == "invalid_payload"


@pytest.mark.parametrize("value", [0, True, 1.0, "1"])
def test_transcript_append_acknowledgement_rejects_non_positive_or_coerced_sequence_numbers(
    value: object,
) -> None:
    with pytest.raises(ContractError) as caught:
        parse_contract(
            TranscriptAppendAcknowledgement,
            {"schema_version": 1, "status": "stored", "sequence_number": value},
        )
    assert caught.value.code == "invalid_payload"


def test_transcript_speaker_has_documented_values() -> None:
    assert set(TranscriptSpeaker) == {TranscriptSpeaker.CALLER, TranscriptSpeaker.AGENT}
