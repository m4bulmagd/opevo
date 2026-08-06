"""Compatibility checks for agent-produced realtime wire events."""

import json
from pathlib import Path
from uuid import UUID

import pytest

from agent.composition import build_event_publisher
from agent.config import AgentSettings
from agent.event_publisher import EventPublisher, RedisEventBus
from agent.session_runtime import SessionRuntime
from presvo_contracts import (
    CustomerCallDispatch,
    TranscriptObservedEvent,
    create_contract,
    realtime_channel,
)


FIXTURES = (
    Path(__file__).resolve().parents[3]
    / "libs/shared/tests/fixtures/v1"
)
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
CALL_ID = UUID("11111111-1111-4111-8111-111111111111")


class _Redis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))


def _metadata() -> CustomerCallDispatch:
    return create_contract(
        CustomerCallDispatch,
        call_id=CALL_ID,
        job_type="customer_call",
        user_id=USER_ID,
        agent_config_id=UUID("33333333-3333-4333-8333-333333333333"),
        agent_identity=f"agent-call-{CALL_ID}",
        agent_name="A",
        owner_name="O",
        owner_context=None,
        system_prompt="Be helpful.",
        knowledge_base="Open weekdays.",
        pipeline_mode="stt_llm_tts",
        minutes_remaining=10,
        allowed_duration_seconds=600,
        dispatch_token="dispatch-token",
    )


@pytest.mark.anyio
async def test_event_publisher_factory_uses_explicit_configured_client() -> None:
    redis = _Redis()
    created: list[tuple[str, bool]] = []

    def from_url(url: str, *, decode_responses: bool) -> _Redis:
        created.append((url, decode_responses))
        return redis

    settings = AgentSettings(redis_url="redis://redis.test:6380/7")
    event = create_contract(
        TranscriptObservedEvent,
        type="transcript_observed",
        user_id=USER_ID,
        call_id=CALL_ID,
        sequence_number=1,
        speaker="CALLER",
        text="Configured client event.",
    )

    publisher = build_event_publisher(settings, redis_factory=from_url)
    await publisher.publish(event)

    assert created == [("redis://redis.test:6380/7", True)]
    assert redis.published == [
        (
            "realtime:user:22222222-2222-4222-8222-222222222222",
            '{"call_id":"11111111-1111-4111-8111-111111111111",'
            '"schema_version":1,"sequence_number":1,"speaker":"CALLER",'
            '"text":"Configured client event.","type":"transcript_observed",'
            '"user_id":"22222222-2222-4222-8222-222222222222"}',
        )
    ]


@pytest.mark.anyio
async def test_agent_realtime_producers_match_golden_contracts() -> None:
    redis = _Redis()
    publisher = EventPublisher(RedisEventBus(redis, owns_client=False))
    runtime = SessionRuntime(publisher)
    metadata = _metadata()

    await runtime.handle_caller_transcript(metadata, "Fixture caller text.")
    await runtime.finalize(metadata, duration_seconds=42)

    expected_json = [
        json.dumps(
            json.loads((FIXTURES / fixture).read_text()),
            separators=(",", ":"),
            sort_keys=True,
        )
        for fixture in (
            "transcript_observed_event.json",
            "agent_session_ended_event.json",
        )
    ]
    assert redis.published == [
        (realtime_channel(USER_ID), expected_json[0]),
        (realtime_channel(USER_ID), expected_json[1]),
    ]
