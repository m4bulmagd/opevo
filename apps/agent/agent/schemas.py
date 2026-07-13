from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DispatchMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    agent_config_id: str = Field(min_length=1)
    agent_identity: str = Field(min_length=1)
    agent_name: str = Field(min_length=1)
    owner_name: str = Field(min_length=1)
    owner_context: str | None = None
    system_prompt: str
    knowledge_base: str
    pipeline_mode: str = Field(min_length=1)
    minutes_remaining: int = Field(ge=0)
    dispatch_token: str = Field(min_length=1)


class CallTranscriptItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence_number: int = Field(ge=1)
    speaker: Literal["CALLER", "AGENT"]
    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class CallCompletionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    duration_seconds: int = Field(ge=0)
    transcript: list[CallTranscriptItem] = Field(default_factory=list)
