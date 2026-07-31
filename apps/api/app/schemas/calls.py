from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

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


class CallSummaryResponseFields(BaseModel):
    summary_status: Literal["processing", "ready", "unavailable"]
    caller_intent: str | None
    action_items: list[str] | None
    sentiment: str | None
    follow_up_required: bool | None


class CallHistoryListItem(CallSummaryResponseFields):
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
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    has_more: bool


class CallTranscriptLineResponse(BaseModel):
    speaker: str
    text: str
    sequence_number: int
    created_at: datetime


class CallDetailResponse(CallSummaryResponseFields):
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
