from typing import Literal

from pydantic import BaseModel, ConfigDict, HttpUrl

from app.providers.carrier_lookup.base import CarrierCode


ForwardingCondition = Literal["unanswered", "busy", "unreachable"]


class ForwardingStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    condition: ForwardingCondition
    title: str
    instructions: list[str]
    dial_code: str | None = None
    disable_code: str | None = None
    source_url: HttpUrl | None = None


class ForwardingGuide(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    carrier: CarrierCode
    number_type: str | None
    opevo_number: str
    warning: str
    steps: list[ForwardingStep]

    def step(self, condition: ForwardingCondition) -> ForwardingStep:
        return next(step for step in self.steps if step.condition == condition)
