from pydantic import BaseModel, ConfigDict


class ClerkWebhookEnvelope(BaseModel):
    type: str
    data: dict


class UserIdentityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
