from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


TranscriptSpeaker = Literal["CALLER", "AGENT"]

AGENT_NAME_MAX_LENGTH = 100
OWNER_NAME_MAX_LENGTH = 255
OWNER_CONTEXT_MAX_LENGTH = 4_000
SYSTEM_PROMPT_MAX_LENGTH = 8_000
KNOWLEDGE_BASE_MAX_LENGTH = 32_000

AgentName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=AGENT_NAME_MAX_LENGTH,
    ),
]
OwnerName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=OWNER_NAME_MAX_LENGTH,
    ),
]
OwnerContext = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        max_length=OWNER_CONTEXT_MAX_LENGTH,
    ),
]
SystemPrompt = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        max_length=SYSTEM_PROMPT_MAX_LENGTH,
    ),
]
KnowledgeBase = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        max_length=KNOWLEDGE_BASE_MAX_LENGTH,
    ),
]


class DispatchMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    agent_config_id: str = Field(min_length=1)
    agent_identity: str = Field(min_length=1)
    agent_name: AgentName
    owner_name: OwnerName
    owner_context: OwnerContext | None = None
    system_prompt: SystemPrompt
    knowledge_base: KnowledgeBase
    pipeline_mode: Literal["stt_llm_tts", "sts"]
    minutes_remaining: int = Field(ge=0)
    allowed_duration_seconds: int = Field(gt=0)
    dispatch_token: str = Field(min_length=1)


class CallTranscriptItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence_number: int = Field(ge=1)
    speaker: TranscriptSpeaker
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
