from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError


ActionItem = Annotated[str, StringConstraints(min_length=1, max_length=300)]


class CallSummaryProjection(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    caller_intent: str = Field(min_length=1, max_length=200)
    action_items: list[ActionItem] = Field(max_length=10)
    sentiment: str = Field(min_length=1, max_length=32)
    follow_up_required: bool

    @classmethod
    def from_stored(cls, stored: object) -> Self | None:
        if not isinstance(stored, dict):
            return None
        try:
            return cls.model_validate(stored)
        except ValidationError:
            return None
