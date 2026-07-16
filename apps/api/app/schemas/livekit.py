from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.agent_content import (
    AgentName,
    KnowledgeBase,
    OwnerContext,
    OwnerName,
    SystemPrompt,
)


class LiveKitDispatchMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    agent_config_id: str
    call_id: str
    agent_identity: str
    minutes_remaining: int
    allowed_duration_seconds: int = Field(gt=0)
    agent_name: AgentName
    owner_name: OwnerName
    owner_context: OwnerContext | None = None
    system_prompt: SystemPrompt
    knowledge_base: KnowledgeBase
    pipeline_mode: Literal["stt_llm_tts", "sts"]
    dispatch_token: str
