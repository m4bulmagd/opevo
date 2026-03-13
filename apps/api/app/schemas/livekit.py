from pydantic import BaseModel


class LiveKitDispatchMetadata(BaseModel):
    user_id: str
    agent_config_id: str
    call_id: str
    minutes_remaining: int
    called_number: str
    caller_number: str | None = None
    agent_name: str
    owner_name: str
    owner_context: str | None = None
    system_prompt: str
    knowledge_base: str
    pipeline_mode: str
