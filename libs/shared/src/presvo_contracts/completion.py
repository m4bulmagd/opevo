"""Completion wire contracts shared by API and voice workers."""

from typing import Literal
from uuid import UUID

from pydantic import Field, StrictInt

from .transcript import TranscriptSegment
from .versioning import NonBlankString, VersionedContract


class CallCompletionRequest(VersionedContract):
    duration_seconds: StrictInt = Field(ge=0)
    transcript: tuple[TranscriptSegment, ...] = Field(default=(), max_length=2_000)


class CallCompletionAcknowledgement(VersionedContract):
    status: Literal["accepted"]
    queued: Literal[True]
    job_id: NonBlankString


class VerificationCompletionRequest(VersionedContract):
    pass


class VerificationCompletionAcknowledgement(VersionedContract):
    status: Literal["verified"]
    session_id: UUID
