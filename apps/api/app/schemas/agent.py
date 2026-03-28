from typing import Literal

from pydantic import BaseModel, ConfigDict


class AgentConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    agent_name: str
    owner_context: str | None
    system_prompt: str
    knowledge_base: str
    pipeline_mode: Literal["stt_llm_tts", "sts"]
    is_enabled: bool


class AgentConfigPatchRequest(BaseModel):
    agent_name: str | None = None
    owner_context: str | None = None
    system_prompt: str | None = None
    knowledge_base: str | None = None
    pipeline_mode: Literal["stt_llm_tts", "sts"] | None = None
    is_enabled: bool | None = None
