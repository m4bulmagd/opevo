from pydantic import BaseModel, ConfigDict, Field


class LiveKitDispatchMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    agent_config_id: str
    call_id: str
    agent_identity: str
    minutes_remaining: int
    allowed_duration_seconds: int = Field(gt=0)
    agent_name: str
    owner_name: str
    owner_context: str | None = None
    system_prompt: str
    knowledge_base: str
    pipeline_mode: str
    dispatch_token: str
