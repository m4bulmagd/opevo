from pydantic import BaseModel


class StripeWebhookEnvelope(BaseModel):
    id: str
    type: str
    data: dict
