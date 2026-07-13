from pydantic import BaseModel


class StripeWebhookEnvelope(BaseModel):
    id: str
    created: int
    type: str
    data: dict
