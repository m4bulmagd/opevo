from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TranscriptSpeaker(str, Enum):
    CALLER = "CALLER"
    AGENT = "AGENT"


class AuthenticatedAgentIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: UUID | None = None
    agent_config_id: UUID | None = None
    trusted_development: bool = False

    @model_validator(mode="after")
    def require_scoped_claims_or_explicit_development_trust(self):
        has_scoped_claims = (
            self.user_id is not None and self.agent_config_id is not None
        )
        is_explicit_development_identity = (
            self.trusted_development
            and self.user_id is None
            and self.agent_config_id is None
        )
        if not (has_scoped_claims or is_explicit_development_identity):
            raise ValueError(
                "agent identity requires scoped claims or explicit development trust"
            )
        if has_scoped_claims and self.trusted_development:
            raise ValueError("scoped agent identity cannot use development trust")
        return self


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
