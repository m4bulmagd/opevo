from pydantic import BaseModel


class PhoneNumberResponse(BaseModel):
    e164: str
    country_code: str
    provider: str
    is_active: bool
