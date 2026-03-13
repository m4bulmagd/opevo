from pydantic import BaseModel


class LiveKitDispatchMetadata(BaseModel):
    user_id: str
    agent_config_id: str
    call_id: str
    called_number: str
    caller_number: str | None = None
