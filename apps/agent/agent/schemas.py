from pydantic import BaseModel, ConfigDict, Field


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
    speaker: str
    text: str


class CallCompletionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    duration_seconds: int = Field(ge=0)
    transcript: list[CallTranscriptItem] = Field(default_factory=list)
    recording_bytes_base64: str | None = None
