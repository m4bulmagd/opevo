from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuthenticatedAgentIdentity(BaseModel):
    """Application-local identity derived from the dispatch-token header."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: UUID
    agent_config_id: UUID
