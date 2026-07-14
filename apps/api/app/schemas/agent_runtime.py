from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TranscriptSpeaker(str, Enum):
    CALLER = "CALLER"
    AGENT = "AGENT"


class AuthenticatedAgentIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: UUID
    agent_config_id: UUID


class TranscriptAppendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence_number: int = Field(ge=1)
    speaker: TranscriptSpeaker
    text: str = Field(min_length=1, max_length=4000)

    @field_validator("speaker", mode="before")
    @classmethod
    def normalize_speaker(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("text", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class TranscriptAppendResponse(BaseModel):
    status: Literal["stored", "duplicate"]
    sequence_number: int
