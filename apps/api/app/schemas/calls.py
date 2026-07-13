from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.agent_runtime import TranscriptAppendRequest


class AgentCallCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_seconds: int = Field(ge=0)
    transcript: list[TranscriptAppendRequest] = Field(
        default_factory=list,
        max_length=2000,
    )
    caller_number: str | None = None


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
