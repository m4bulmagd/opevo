from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class DispatchMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    call_id: str
    user_id: str
    agent_name: str
    owner_name: str
    phone_number: str | None = None
    language: str | None = None
    prompt_context: dict[str, Any] | None = None
    pipeline_mode: str | None = None
    minutes_remaining: int = 0
    caller_number: str | None = None
    dispatch_token: str | None = None


class CallTranscriptItem(BaseModel):
    speaker: str
    text: str


class CallCompletionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    duration_seconds: int = Field(ge=0)
    caller_number: str | None = None
    transcript: list[CallTranscriptItem] = Field(default_factory=list)
    recording_bytes_base64: str | None = None
