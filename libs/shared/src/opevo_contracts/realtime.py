"""Realtime event wire contracts shared by API clients and workers."""

from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import Field, StrictInt, StringConstraints, TypeAdapter

from .transcript import TranscriptSequenceNumber, TranscriptSpeaker, TranscriptText
from .versioning import NonBlankString, VersionedContract, _parse_contract_union


REALTIME_CHANNEL_PREFIX = "realtime:user:"


class TranscriptObservedEvent(VersionedContract):
    type: Literal["transcript_observed"]
    user_id: UUID
    call_id: UUID
    sequence_number: TranscriptSequenceNumber
    speaker: TranscriptSpeaker
    text: TranscriptText


class CallStartedEvent(VersionedContract):
    type: Literal["call_started"]
    user_id: UUID
    call_id: UUID
    room_name: NonBlankString


class AgentSessionEndedEvent(VersionedContract):
    type: Literal["agent_session_ended"]
    user_id: UUID
    call_id: UUID
    duration_seconds: StrictInt = Field(ge=0)


class CallFinalizedEvent(VersionedContract):
    type: Literal["call_finalized"]
    user_id: UUID
    call_id: UUID
    minutes_charged: StrictInt = Field(ge=0)
    summary_text: Annotated[str, StringConstraints(max_length=8_000)] | None = None


RealtimeEvent: TypeAlias = Annotated[
    TranscriptObservedEvent | CallStartedEvent | AgentSessionEndedEvent | CallFinalizedEvent,
    Field(discriminator="type"),
]

_REALTIME_EVENT_ADAPTER: TypeAdapter[RealtimeEvent] = TypeAdapter(RealtimeEvent)


def parse_realtime_event(value: object) -> RealtimeEvent:
    return _parse_contract_union(_REALTIME_EVENT_ADAPTER, "RealtimeEvent", value)


def realtime_channel(user_id: UUID) -> str:
    return f"{REALTIME_CHANNEL_PREFIX}{user_id}"
