"""Compatibility checks for agent-produced realtime wire events."""

import json
from pathlib import Path
from uuid import UUID

import pytest

from agent.session_runtime import SessionRuntime
from presvo_contracts import CustomerCallDispatch, create_contract


FIXTURES = (
    Path(__file__).resolve().parents[3]
    / "libs/shared/tests/fixtures/v1"
)
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
CALL_ID = UUID("11111111-1111-4111-8111-111111111111")


class _Publisher:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)


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
async def test_agent_realtime_producers_match_golden_contracts() -> None:
    publisher = _Publisher()
    runtime = SessionRuntime(publisher)
    metadata = _metadata()

    await runtime.handle_caller_transcript(metadata, "Fixture caller text.")
    await runtime.finalize(metadata, duration_seconds=42)

    assert [event.model_dump(mode="json") for event in publisher.events] == [
        json.loads((FIXTURES / "transcript_observed_event.json").read_text()),
        json.loads((FIXTURES / "agent_session_ended_event.json").read_text()),
    ]
