import json
from pathlib import Path
from uuid import UUID

import pytest
from opevo_contracts import dump_contract, parse_dispatch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.dispatch_token import DispatchTokenConfig
from app.models.agent_config import AgentConfig
from app.models.usage_ledger import UsageLedger
from app.workers.outbox.customer_dispatch import deliver_livekit_dispatch
from app.workers.outbox.verification_dispatch import (
    deliver_livekit_verification_dispatch,
)
from tests.workers.test_forwarding_verification_dispatch_outbox import (
    FIXED_NOW,
    _Provider as VerificationProvider,
    _seed_verification_dispatch,
)
from tests.workers.test_livekit_dispatch_outbox import (
    _Provider as CustomerProvider,
    _seed_dispatch,
)


FIXTURE_ROOT = Path(__file__).resolve().parents[4] / "libs/shared/tests/fixtures/v1"
CUSTOMER_CALL_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
AGENT_CONFIG_ID = UUID("33333333-3333-4333-8333-333333333333")
VERIFICATION_SESSION_ID = "44444444-4444-4444-8444-444444444444"
ACTIVATION_ID = UUID("55555555-5555-4555-8555-555555555555")


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / name).read_text())


@pytest.mark.anyio
async def test_customer_outbox_producer_matches_golden_fixture_exactly(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call, event, subscription = await _seed_dispatch(
        db_session,
        owner_name="Fixture Owner",
        user_id=USER_ID,
        config_id=AGENT_CONFIG_ID,
        call_id=CUSTOMER_CALL_ID,
    )
    config = await db_session.get(AgentConfig, call.agent_config_id)
    usage = await db_session.scalar(select(UsageLedger))
    assert config is not None
    assert usage is not None
    config.agent_name = "Fixture Agent"
    config.owner_context = "Fixture customer-success context."
    config.system_prompt = "Help the fixture caller clearly."
    config.knowledge_base = "Fixture knowledge base."
    config.pipeline_mode = "stt_llm_tts"
    subscription.allocated_minutes = 12
    usage.minutes_delta = 12
    usage.balance_after = 12
    await db_session.commit()

    provider = CustomerProvider()
    monkeypatch.setenv("ACTIVATION_FLOW_ENABLED", "true")
    monkeypatch.setenv("LIVEKIT_AGENT_NAME", "poison-environment-agent")
    monkeypatch.setenv("MAX_CALL_DURATION_SECONDS", "999")
    monkeypatch.setattr(
        "app.workers.outbox.customer_dispatch.create_dispatch_token",
        lambda **_kwargs: "fixture-dispatch-token",
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    await deliver_livekit_dispatch(
        event,
        session_factory=session_factory,
        provider=provider,
        token_config=DispatchTokenConfig(
            secret="fixture-explicit-customer-secret",
            ttl_seconds=300,
        ),
        livekit_agent_name="fixture-worker",
        activation_flow_enabled=False,
        max_call_duration_seconds=300,
        now=lambda: FIXED_NOW,
    )

    assert len(provider.create_calls) == 1
    produced = dump_contract(parse_dispatch(provider.create_calls[0]["metadata"]))
    assert produced == _fixture("customer_call_dispatch.json")


@pytest.mark.anyio
async def test_verification_outbox_producer_matches_golden_fixture_exactly(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user, _activation, event = await _seed_verification_dispatch(
        db_session,
        user_id=USER_ID,
        activation_id=ACTIVATION_ID,
        session_id=VERIFICATION_SESSION_ID,
    )
    provider = VerificationProvider()
    monkeypatch.setenv("LIVEKIT_AGENT_NAME", "poison-environment-agent")
    monkeypatch.setattr(
        "app.workers.outbox.verification_dispatch.create_verification_token",
        lambda **_kwargs: "fixture-completion-token",
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    await deliver_livekit_verification_dispatch(
        event,
        session_factory=session_factory,
        provider=provider,
        token_config=DispatchTokenConfig(
            secret="fixture-explicit-verification-secret",
            ttl_seconds=300,
        ),
        livekit_agent_name="fixture-worker",
        now=lambda: FIXED_NOW,
    )

    assert len(provider.create_calls) == 1
    produced = dump_contract(parse_dispatch(provider.create_calls[0]["metadata"]))
    assert produced == _fixture("forwarding_verification_dispatch.json")
