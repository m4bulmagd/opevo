from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TranscriptLineRequest(BaseModel):
    speaker: str
    text: str


class AgentCallCompletionRequest(BaseModel):
    user_id: UUID
    duration_seconds: int
    minutes_remaining: int
    transcript: list[TranscriptLineRequest] = Field(default=[], max_length=2000)
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


class CallHistoryListItem(BaseModel):
    id: UUID
    status: str
    caller_number: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    minutes_charged: int | None
    summary_text: str | None
    has_recording: bool


class CallHistoryListResponse(BaseModel):
    calls: list[CallHistoryListItem]


class CallTranscriptLineResponse(BaseModel):
    speaker: str
    text: str
    sequence_number: int
    created_at: datetime


class CallDetailResponse(BaseModel):
    id: UUID
    status: str
    caller_number: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    minutes_charged: int | None
    summary_text: str | None
    recording_url: str | None
    transcript: list[CallTranscriptLineResponse]
