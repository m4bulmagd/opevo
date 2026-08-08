"""Transcript wire contracts shared by API and voice workers."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StringConstraints

from .versioning import VersionedContract, WireValue


TRANSCRIPT_TEXT_MAX_LENGTH = 4_000


class TranscriptSpeaker(StrEnum):
    CALLER = "CALLER"
    AGENT = "AGENT"


TranscriptSequenceNumber = Annotated[StrictInt, Field(ge=1)]
TranscriptText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=TRANSCRIPT_TEXT_MAX_LENGTH,
    ),
]


class TranscriptSegment(WireValue):
    sequence_number: TranscriptSequenceNumber
    speaker: TranscriptSpeaker
    text: TranscriptText


class TranscriptAppendRequest(VersionedContract):
    segment: TranscriptSegment


class TranscriptAppendAcknowledgement(VersionedContract):
    status: Literal["stored", "duplicate"]
    sequence_number: TranscriptSequenceNumber
