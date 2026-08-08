"""Completion wire contracts shared by API and voice workers."""

from typing import Literal
from uuid import UUID

from pydantic import Field, StrictInt, field_validator

from .transcript import TranscriptSegment
from .versioning import NonBlankString, VersionedContract


CALL_COMPLETION_TRANSCRIPT_MAX_ITEMS = 2_000


class CallCompletionRequest(VersionedContract):
    duration_seconds: StrictInt = Field(ge=0)
    transcript: tuple[TranscriptSegment, ...] = Field(
        default=(),
        max_length=CALL_COMPLETION_TRANSCRIPT_MAX_ITEMS,
    )


class CallCompletionAcknowledgement(VersionedContract):
    status: Literal["accepted"]
    queued: Literal[True]
    job_id: NonBlankString

    @field_validator("queued", mode="before")
    @classmethod
    def require_true_boolean(cls, value: object) -> object:
        if value is not True:
            raise ValueError("queued must be true")
        return value


class VerificationCompletionRequest(VersionedContract):
    pass


class VerificationCompletionAcknowledgement(VersionedContract):
    status: Literal["verified"]
    session_id: UUID
