from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.database import get_session
from app.core.dispatch_token import create_dispatch_token
from app.models.agent_config import AgentConfig
from app.models.call import Call
from app.models.call_message import CallMessage
from app.schemas.agent_identity import AuthenticatedAgentIdentity


class CapturingQueue:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def enqueue(self, payload: dict) -> str:
        self.payloads.append(payload)
        return f"call-finalization:{payload['call_id']}"


def test_agent_identity_requires_scoped_claims() -> None:
    user_id = uuid4()
    config_id = uuid4()

    identity = AuthenticatedAgentIdentity(
        user_id=user_id,
        agent_config_id=config_id,
    )
    assert identity.user_id == user_id
    assert identity.agent_config_id == config_id

    invalid_identities = [
        {},
        {"user_id": user_id},
        {"agent_config_id": config_id},
        {"trusted_development": True},
        {
            "user_id": user_id,
            "agent_config_id": config_id,
            "trusted_development": True,
        },
    ]
    for identity in invalid_identities:
        with pytest.raises(ValidationError):
            AuthenticatedAgentIdentity(**identity)


async def _runtime_call(db_session, active_user, *, status: str = "pending"):
    config = AgentConfig(
        user_id=active_user.id,
        agent_name="Runtime",
        system_prompt="Be helpful",
        knowledge_base="",
        is_enabled=True,
    )
    db_session.add(config)
    await db_session.flush()
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        agent_config_id=config.id,
        status=status,
    )
    db_session.add(call)
    await db_session.commit()
    token = create_dispatch_token(
        call_id=str(call.id),
        user_id=str(active_user.id),
        agent_config_id=str(config.id),
    )
    return call, config, token


def _runtime_app(db_session, *, queue: CapturingQueue | None = None) -> FastAPI:
    from app.routers.agent import router

    async def override_session():
        yield db_session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session
    if queue is not None:
        app.state.call_finalization_queue = queue
    return app


async def _post(
    app: FastAPI,
    path: str,
    token: str,
    payload: dict,
    *,
    raise_app_exceptions: bool = True,
) -> httpx.Response:
    if "schema_version" not in payload:
        payload = (
            {
                "schema_version": 1,
                "duration_seconds": payload["duration_seconds"],
                "transcript": payload.get("transcript", []),
            }
            if path.endswith("/complete")
            else {"schema_version": 1, "segment": payload}
        )
    transport = httpx.ASGITransport(
        app=app,
        raise_app_exceptions=raise_app_exceptions,
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.post(
            path,
            headers={"x-agent-token": token},
            json=payload,
        )


@pytest.mark.anyio
async def test_deactivating_account_call_can_append_and_replay_transcript(
    db_session,
    active_user,
) -> None:
    call, _, token = await _runtime_call(db_session, active_user)
    active_user.status = "deactivating"
    await db_session.commit()
    call_id = call.id
    app = _runtime_app(db_session)
    path = f"/api/agent/calls/{call_id}/transcript"

    stored = await _post(
        app,
        path,
        token,
        {"sequence_number": 1, "speaker": "CALLER", "text": "  Hello  "},
    )
    duplicate = await _post(
        app,
        path,
        token,
        {"sequence_number": 1, "speaker": "CALLER", "text": "Hello"},
    )

    assert stored.status_code == 200
    assert stored.json() == {
        "schema_version": 1,
        "status": "stored",
        "sequence_number": 1,
    }
    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "schema_version": 1,
        "status": "duplicate",
        "sequence_number": 1,
    }
    rows = list(
        (
            await db_session.execute(
                select(CallMessage).where(CallMessage.call_id == call.id)
            )
        ).scalars()
    )
    assert [(row.sequence_number, row.speaker, row.text) for row in rows] == [
        (1, "CALLER", "Hello")
    ]


@pytest.mark.anyio
async def test_append_sequence_conflict_is_first_write_wins(
    db_session,
    active_user,
    caplog,
) -> None:
    call, _, token = await _runtime_call(db_session, active_user)
    call_id = call.id
    app = _runtime_app(db_session)
    path = f"/api/agent/calls/{call_id}/transcript"
    await _post(
        app,
        path,
        token,
        {"sequence_number": 1, "speaker": "CALLER", "text": "First"},
    )

    response = await _post(
        app,
        path,
        token,
        {
            "sequence_number": 1,
            "speaker": "AGENT",
            "text": "TRANSCRIPT_LOG_SENTINEL_DIFFERENT",
        },
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "sequence_conflict"}
    row = await db_session.scalar(
        select(CallMessage).where(
            CallMessage.call_id == call_id,
            CallMessage.sequence_number == 1,
        )
    )
    assert row is not None
    assert (row.speaker, row.text) == ("CALLER", "First")
    assert "TRANSCRIPT_LOG_SENTINEL_DIFFERENT" not in caplog.text


@pytest.mark.anyio
async def test_append_rejects_token_for_a_different_call(
    db_session,
    active_user,
) -> None:
    call, config, _ = await _runtime_call(db_session, active_user)
    wrong_token = create_dispatch_token(
        call_id=str(uuid4()),
        user_id=str(active_user.id),
        agent_config_id=str(config.id),
    )

    response = await _post(
        _runtime_app(db_session),
        f"/api/agent/calls/{call.id}/transcript",
        wrong_token,
        {"sequence_number": 2, "speaker": "CALLER", "text": "No access"},
    )

    assert response.status_code == 401
    assert await db_session.scalar(
        select(CallMessage).where(CallMessage.call_id == call.id)
    ) is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"sequence_number": 0, "speaker": "CALLER", "text": "Hello"},
        {"sequence_number": 1, "speaker": "UNKNOWN", "text": "Hello"},
        {"sequence_number": 1, "speaker": "CALLER", "text": "   "},
        {"sequence_number": 1, "speaker": "CALLER", "text": "x" * 4001},
    ],
)
async def test_append_validates_sequence_speaker_and_text(
    db_session,
    active_user,
    payload: dict,
) -> None:
    call, _, token = await _runtime_call(db_session, active_user)

    response = await _post(
        _runtime_app(db_session),
        f"/api/agent/calls/{call.id}/transcript",
        token,
        payload,
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_terminal_call_allows_exact_replay_but_rejects_new_sequence(
    db_session,
    active_user,
) -> None:
    call, _, token = await _runtime_call(db_session, active_user, status="connected")
    app = _runtime_app(db_session)
    path = f"/api/agent/calls/{call.id}/transcript"
    await _post(
        app,
        path,
        token,
        {"sequence_number": 1, "speaker": "CALLER", "text": "Persist me"},
    )
    call.status = "completed"
    await db_session.commit()

    duplicate = await _post(
        app,
        path,
        token,
        {"sequence_number": 1, "speaker": "CALLER", "text": "Persist me"},
    )
    rejected = await _post(
        app,
        path,
        token,
        {"sequence_number": 2, "speaker": "AGENT", "text": "Too late"},
    )

    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    assert rejected.status_code == 409
    assert rejected.json() == {"detail": "call_not_accepting_transcript"}


@pytest.mark.anyio
@pytest.mark.parametrize("call_status", ["ending", "finalizing"])
async def test_nonterminal_transition_accepts_new_sequence(
    db_session,
    active_user,
    call_status: str,
) -> None:
    call, _, token = await _runtime_call(
        db_session,
        active_user,
        status=call_status,
    )

    response = await _post(
        _runtime_app(db_session),
        f"/api/agent/calls/{call.id}/transcript",
        token,
        {"sequence_number": 1, "speaker": "CALLER", "text": "Still open"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "status": "stored",
        "sequence_number": 1,
    }


@pytest.mark.anyio
async def test_completion_commits_recovery_before_queue_unavailable_response(
    db_session,
    active_user,
) -> None:
    call, _, token = await _runtime_call(db_session, active_user)

    response = await _post(
        _runtime_app(db_session),
        f"/api/agent/calls/{call.id}/complete",
        token,
        {
            "duration_seconds": 3,
            "transcript": [
                {"sequence_number": 1, "speaker": "CALLER", "text": "Durable tail"}
            ],
        },
    )

    assert response.status_code == 503
    fresh_session_factory = async_sessionmaker(
        db_session.bind,
        expire_on_commit=False,
    )
    async with fresh_session_factory() as fresh_session:
        row = await fresh_session.scalar(
            select(CallMessage).where(
                CallMessage.call_id == call.id,
                CallMessage.sequence_number == 1,
            )
        )
    assert row is not None
    assert row.text == "Durable tail"


@pytest.mark.anyio
async def test_completion_queues_only_a_reference_after_recovery_merge(
    db_session,
    active_user,
) -> None:
    call, _, token = await _runtime_call(db_session, active_user)
    queue = CapturingQueue()

    response = await _post(
        _runtime_app(db_session, queue=queue),
        f"/api/agent/calls/{call.id}/complete",
        token,
        {
            "duration_seconds": 3,
            "transcript": [
                {"sequence_number": 1, "speaker": "AGENT", "text": "Recovered"}
            ],
        },
    )

    assert response.status_code == 202
    assert len(queue.payloads) == 1
    assert "transcript" not in queue.payloads[0]


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", ["transcript", "complete"])
async def test_locked_row_claim_mismatch_maps_to_401_before_persistence_or_queue(
    db_session,
    active_user,
    endpoint: str,
) -> None:
    from app.routers.agent import require_agent_auth

    call, _, token = await _runtime_call(db_session, active_user)
    call_id = call.id
    app = _runtime_app(db_session, queue=CapturingQueue())

    async def stale_previously_authenticated_identity():
        return AuthenticatedAgentIdentity(
            user_id=uuid4(),
            agent_config_id=uuid4(),
        )

    app.dependency_overrides[require_agent_auth] = (
        stale_previously_authenticated_identity
    )
    payload = (
        {"sequence_number": 1, "speaker": "CALLER", "text": "No write"}
        if endpoint == "transcript"
        else {"duration_seconds": 1, "transcript": []}
    )

    response = await _post(
        app,
        f"/api/agent/calls/{call_id}/{endpoint}",
        token,
        payload,
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_agent_token"}
    assert await db_session.scalar(
        select(CallMessage).where(CallMessage.call_id == call_id)
    ) is None
