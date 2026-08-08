from __future__ import annotations

import json
from pathlib import Path

import pytest
from opevo_contracts import (
    CallCompletionAcknowledgement,
    CallCompletionRequest,
    TranscriptAppendAcknowledgement,
    TranscriptAppendRequest,
    VerificationCompletionAcknowledgement,
    VerificationCompletionRequest,
    dump_contract,
    parse_contract,
)


FIXTURES = Path(__file__).parents[4] / "libs/shared/tests/fixtures/v1"


@pytest.mark.parametrize(
    ("model", "filename"),
    [
        (TranscriptAppendRequest, "transcript_append_request.json"),
        (TranscriptAppendAcknowledgement, "transcript_append_acknowledgement.json"),
        (CallCompletionRequest, "call_completion_request.json"),
        (CallCompletionAcknowledgement, "call_completion_acknowledgement.json"),
        (VerificationCompletionRequest, "verification_completion_request.json"),
        (VerificationCompletionAcknowledgement, "verification_completion_acknowledgement.json"),
    ],
)
def test_http_boundary_round_trips_each_versioned_golden_fixture(model, filename: str) -> None:
    fixture = json.loads((FIXTURES / filename).read_text())

    assert dump_contract(parse_contract(model, fixture)) == fixture


def test_consumer_accepts_additive_acknowledgement_fields() -> None:
    acknowledgement = parse_contract(
        TranscriptAppendAcknowledgement,
        {
            "schema_version": 1,
            "status": "stored",
            "sequence_number": 1,
            "future": "ignored",
        },
    )

    assert acknowledgement.sequence_number == 1
