from datetime import datetime, time
from itertools import pairwise
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_serializer,
    field_validator,
    model_validator,
)

from app.providers.telephony.telnyx import normalize_french_number


CarrierCode = Literal["orange", "sfr", "bouygues", "free", "other"]
WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
NAME_MAX_LENGTH = 100
BUSINESS_TYPE_MAX_LENGTH = 100
PUBLIC_DESCRIPTION_MAX_LENGTH = 1_000
FAQ_MAX_ITEMS = 20
FAQ_QUESTION_MAX_LENGTH = 200
FAQ_ANSWER_MAX_LENGTH = 800
INSTRUCTIONS_MAX_LENGTH = 2_000
ESCALATION_NOTES_MAX_LENGTH = 2_000


class FaqItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=FAQ_QUESTION_MAX_LENGTH)
    answer: str = Field(min_length=1, max_length=FAQ_ANSWER_MAX_LENGTH)


class OpeningInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: time
    end: time

    @model_validator(mode="after")
    def require_forward_interval(self) -> Self:
        if self.end <= self.start:
            raise ValueError("Opening interval must end after it starts")
        return self

    @field_serializer("start", "end")
    def serialize_time(self, value: time) -> str:
        return value.strftime("%H:%M")


class DayHours(BaseModel):
    model_config = ConfigDict(extra="forbid")

    closed: bool
    intervals: list[OpeningInterval] = Field(max_length=2)

    @model_validator(mode="after")
    def validate_day(self) -> Self:
        if self.closed and self.intervals:
            raise ValueError("Closed days cannot contain intervals")
        ordered = sorted(self.intervals, key=lambda interval: interval.start)
        if any(left.end > right.start for left, right in pairwise(ordered)):
            raise ValueError("Opening intervals cannot overlap")
        if not self.closed and not ordered:
            raise ValueError("Open days require at least one interval")
        self.intervals = ordered
        return self


class BusinessHours(RootModel[dict[str, DayHours]]):
    @model_validator(mode="after")
    def require_exact_weekdays(self) -> Self:
        if set(self.root) != set(WEEKDAYS):
            raise ValueError("Business hours must contain exactly the seven weekdays")
        return self


class BusinessProfileDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_name: str | None = Field(
        default=None, min_length=1, max_length=NAME_MAX_LENGTH
    )
    business_name: str | None = Field(
        default=None, min_length=1, max_length=NAME_MAX_LENGTH
    )
    business_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=BUSINESS_TYPE_MAX_LENGTH,
    )
    public_description: str | None = Field(
        default=None,
        min_length=1,
        max_length=PUBLIC_DESCRIPTION_MAX_LENGTH,
    )
    timezone: str | None = None
    business_hours: BusinessHours | None = None
    existing_phone_e164: str | None = None
    confirmed_carrier: CarrierCode | None = None
    receptionist_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=NAME_MAX_LENGTH,
    )
    faqs: list[FaqItem] = Field(default_factory=list, max_length=FAQ_MAX_ITEMS)
    special_instructions: str | None = Field(
        default=None,
        max_length=INSTRUCTIONS_MAX_LENGTH,
    )
    escalation_notes: str | None = Field(
        default=None,
        max_length=ESCALATION_NOTES_MAX_LENGTH,
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("A valid IANA timezone is required") from None
        return value

    @field_validator("existing_phone_e164")
    @classmethod
    def normalize_existing_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_french_number(value)

    def to_storage_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class BusinessProfileResponse(BusinessProfileDraft):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    detected_carrier: str | None = None
    detected_number_type: str | None = None
    carrier_lookup_status: str | None = None
    carrier_looked_up_at: datetime | None = None
    content_revision: int
    routing_revision: int


class BusinessProfileConstraints(BaseModel):
    model_config = ConfigDict(frozen=True)

    name_max_length: int = NAME_MAX_LENGTH
    business_type_max_length: int = BUSINESS_TYPE_MAX_LENGTH
    public_description_max_length: int = PUBLIC_DESCRIPTION_MAX_LENGTH
    faq_max_items: int = FAQ_MAX_ITEMS
    faq_question_max_length: int = FAQ_QUESTION_MAX_LENGTH
    faq_answer_max_length: int = FAQ_ANSWER_MAX_LENGTH
    special_instructions_max_length: int = INSTRUCTIONS_MAX_LENGTH
    escalation_notes_max_length: int = ESCALATION_NOTES_MAX_LENGTH
    max_intervals_per_day: int = 2
    phone_country: Literal["FR"] = "FR"
