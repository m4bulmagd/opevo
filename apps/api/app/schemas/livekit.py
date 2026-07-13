from pydantic import BaseModel, ConfigDict


class LiveKitDispatchMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    agent_config_id: str
    call_id: str
    agent_identity: str
    minutes_remaining: int
    agent_name: str
    owner_name: str
    owner_context: str | None = None
    system_prompt: str
    knowledge_base: str
    pipeline_mode: str
    dispatch_token: str
