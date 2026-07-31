from typing import Literal

from pydantic import BaseModel, ConfigDict

from presvo_contracts import (
    AgentName,
    KnowledgeBase,
    OwnerContext,
    SystemPrompt,
)


class AgentConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    agent_name: str
    owner_context: str | None
    system_prompt: str
    knowledge_base: str
    pipeline_mode: Literal["stt_llm_tts", "sts"]
    is_enabled: bool


class AgentConfigPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: AgentName | None = None
    owner_context: OwnerContext | None = None
    system_prompt: SystemPrompt | None = None
    knowledge_base: KnowledgeBase | None = None
    pipeline_mode: Literal["stt_llm_tts", "sts"] | None = None
    is_enabled: bool | None = None
