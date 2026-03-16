from pydantic import BaseModel


class WebSocketAuthMessage(BaseModel):
    type: str
    token: str | None = None
