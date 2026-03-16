from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class TranscriptLineRequest(BaseModel):
    speaker: str
    text: str


class AgentCallCompletionRequest(BaseModel):
    user_id: UUID
    duration_seconds: int
    minutes_remaining: int
    transcript: list[TranscriptLineRequest] = []
    caller_number: str | None = None
    recording_bytes_base64: str | None = None


class AgentCallCompletionResponse(BaseModel):
    status: str
    queued: bool
    job_id: str


class CallResponse(BaseModel):
    id: UUID
    status: str
    caller_number: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    minutes_charged: int | None
    summary_text: str | None
    recording_url: str | None
