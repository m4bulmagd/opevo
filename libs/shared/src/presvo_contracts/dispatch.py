"""Dispatch payload wire contracts shared by the API and voice worker."""

from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import Field, StrictInt, StringConstraints, TypeAdapter, field_validator

from .versioning import (
    NonBlankString,
    VersionedContract,
    _parse_contract_union,
)


AGENT_NAME_MAX_LENGTH = 100
OWNER_NAME_MAX_LENGTH = 255
OWNER_CONTEXT_MAX_LENGTH = 4_000
SYSTEM_PROMPT_MAX_LENGTH = 8_000
KNOWLEDGE_BASE_MAX_LENGTH = 32_000
VERIFICATION_MESSAGE = "Forwarding test successful. Return to Presvo to go live."

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
        min_length=1,
        max_length=OWNER_CONTEXT_MAX_LENGTH,
    ),
]
SystemPrompt = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=SYSTEM_PROMPT_MAX_LENGTH,
    ),
]
KnowledgeBase = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=KNOWLEDGE_BASE_MAX_LENGTH,
    ),
]


class CustomerCallDispatch(VersionedContract):
    job_type: Literal["customer_call"]
    call_id: UUID
    user_id: UUID
    agent_config_id: UUID
    agent_identity: NonBlankString
    agent_name: AgentName
    owner_name: OwnerName
    owner_context: OwnerContext | None = None
    system_prompt: SystemPrompt
    knowledge_base: KnowledgeBase
    pipeline_mode: Literal["stt_llm_tts", "sts"]
    minutes_remaining: StrictInt = Field(ge=0)
    allowed_duration_seconds: StrictInt = Field(gt=0)
    dispatch_token: str = Field(min_length=1, repr=False)

    @field_validator("dispatch_token", mode="before")
    @classmethod
    def reject_blank_dispatch_token(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("dispatch token must not be blank")
        return value


class ForwardingVerificationDispatch(VersionedContract):
    job_type: Literal["forwarding_verification"]
    verification_session_id: UUID
    user_id: UUID
    agent_identity: NonBlankString
    completion_token: str = Field(min_length=1, repr=False)
    message: Literal["Forwarding test successful. Return to Presvo to go live."]
    tts_provider: Literal["speechmatics", "elevenlabs"]

    @field_validator("completion_token", mode="before")
    @classmethod
    def reject_blank_completion_token(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("completion token must not be blank")
        return value


DispatchContract: TypeAlias = Annotated[
    CustomerCallDispatch | ForwardingVerificationDispatch,
    Field(discriminator="job_type"),
]

_DISPATCH_ADAPTER: TypeAdapter[DispatchContract] = TypeAdapter(DispatchContract)


def parse_dispatch(value: object) -> DispatchContract:
    return _parse_contract_union(_DISPATCH_ADAPTER, "DispatchContract", value)
