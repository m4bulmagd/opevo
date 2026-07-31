from dataclasses import dataclass
from types import SimpleNamespace
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.core.database import get_session
from app.core.dispatch_token import create_dispatch_token
from app.models.agent_config import AgentConfig
from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.outbox_event import OutboxEvent
from app.models.recording_egress_operation import RecordingEgressOperation
from app.schemas.agent_identity import AuthenticatedAgentIdentity
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.outbox_service import OutboxService


@dataclass
class FakeQueueCall:
    job_name: str
    payload: dict
    job_id: str


class FakeCallFinalizationQueue:
    def __init__(self, *, session=None) -> None:
        self.calls: list[FakeQueueCall] = []
        self.session = session

    async def enqueue(self, payload: dict) -> str:
        if self.session is not None:
            if hasattr(self.session, "commits"):
                assert self.session.commits == 1
            else:
                assert self.session.in_transaction() is False
        job_id = f"call-finalization:{payload['call_id']}"
        self.calls.append(
            FakeQueueCall(
                job_name="call_finalization_job",
                payload=payload,
                job_id=job_id,
            )
        )
        return job_id


class FailingCallFinalizationQueue:
    async def enqueue(self, _payload: dict) -> str:
        raise RuntimeError("redis unavailable")


class FakeOutboxPool:
    def __init__(self, *, failure: bool = False) -> None:
        self.failure = failure
        self.jobs: list[tuple[str, dict]] = []

    async def enqueue_job(self, name: str, payload: dict) -> None:
        self.jobs.append((name, payload))
        if self.failure:
            raise RuntimeError("redis unavailable")


def _completion_payload(**values: object) -> dict[str, object]:
    return {"schema_version": 1, "transcript": [], **values}


class FakeAuthSession:
    def __init__(self, *, call=None, agent_config=None) -> None:
        self.call = call
        self.agent_config = agent_config
        self.lookups: list[tuple[type, object]] = []
        self.commits = 0

    async def get(self, model: type, object_id):
        self.lookups.append((model, object_id))
        if model is Call and self.call is not None and self.call.id == object_id:
            return self.call
        if (
            model is AgentConfig
            and self.agent_config is not None
            and self.agent_config.id == object_id
        ):
            return self.agent_config
        return None

    async def execute(self, statement):
        descriptions = getattr(statement, "column_descriptions", ())
        entity = descriptions[0].get("entity") if descriptions else None
        value = self.call if entity is Call else None
        return SimpleNamespace(scalar_one_or_none=lambda: value)

    async def commit(self) -> None:
        self.commits += 1

    async def flush(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _configure_auth(
    monkeypatch: pytest.MonkeyPatch,
    *,
    app_env: str,
    dispatch_secret: str | None = None,
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    if dispatch_secret is None:
        monkeypatch.delenv("AGENT_DISPATCH_JWT_SECRET", raising=False)
    else:
        monkeypatch.setenv("AGENT_DISPATCH_JWT_SECRET", dispatch_secret)

    from app.core.config import get_settings

    get_settings.cache_clear()


def _build_completion_app(
    fake_queue,
    *,
    auth_session=None,
    authenticated: bool = False,
    arq_pool=None,
):
    from app.routers.agent import get_call_finalization_queue, require_agent_auth
    from app.routers.agent import router as agent_router

    async def override_get_call_finalization_queue():
        return fake_queue

    app = FastAPI()
    app.include_router(agent_router)
    app.state.call_finalization_queue = fake_queue
    app.state.arq_pool = arq_pool
    app.dependency_overrides[get_call_finalization_queue] = (
        override_get_call_finalization_queue
    )
    user_id = uuid4()
    agent_config_id = uuid4()
    if authenticated:
        async def override_require_agent_auth() -> AuthenticatedAgentIdentity:
            return AuthenticatedAgentIdentity(
                user_id=user_id,
                agent_config_id=agent_config_id,
            )

        app.dependency_overrides[require_agent_auth] = override_require_agent_auth
    call_id = uuid4()
    session = auth_session or FakeAuthSession(
        call=SimpleNamespace(
            id=call_id,
            user_id=user_id,
            agent_config_id=agent_config_id,
            status="pending",
            ended_at=None,
            duration_seconds=None,
            started_at=None,
            created_at=datetime.now(UTC),
            failure_code=None,
            state_changed_at=datetime.now(UTC),
            recording_egress_id=None,
        ),
    )

    async def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    return app


@pytest.mark.anyio
async def test_agent_completion_endpoint_rejects_accounting_authority_fields(
) -> None:
    call_id = uuid4()
    fake_queue = FakeCallFinalizationQueue()

    app = _build_completion_app(fake_queue, authenticated=True)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/agent/calls/{call_id}/complete",
            json=_completion_payload(
                user_id=str(uuid4()),
                duration_seconds=61,
                minutes_remaining=999,
            ),
        )

    assert response.status_code == 401
    assert fake_queue.calls == []


@pytest.mark.anyio
async def test_agent_completion_endpoint_rejects_raw_recording_blob(
) -> None:
    call_id = uuid4()
    fake_queue = FakeCallFinalizationQueue()

    app = _build_completion_app(fake_queue, authenticated=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/agent/calls/{call_id}/complete",
            json=_completion_payload(
                duration_seconds=61,
                recording_bytes_base64="cmVjb3JkaW5nLWJ5dGVz",
            ),
        )

    assert response.status_code == 401
    assert fake_queue.calls == []


@pytest.mark.anyio
async def test_agent_completion_endpoint_rejects_negative_duration(
) -> None:
    call_id = uuid4()
    fake_queue = FakeCallFinalizationQueue()

    app = _build_completion_app(fake_queue, authenticated=True)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/agent/calls/{call_id}/complete",
            json=_completion_payload(duration_seconds=-1),
        )

    assert response.status_code == 422
    assert fake_queue.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "app_env",
    ["development", "test", "staging", "production"],
)
async def test_static_agent_token_is_rejected_in_every_environment(
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
) -> None:
    _configure_auth(monkeypatch, app_env=app_env)
    call_id = uuid4()
    fake_queue = FakeCallFinalizationQueue()
    app = _build_completion_app(fake_queue)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            f"/api/agent/calls/{call_id}/complete",
            headers={"x-agent-token": "test-agent-token"},
            json=_completion_payload(duration_seconds=1),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid agent token"}
    assert fake_queue.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("wakeup_fails", [False, True])
async def test_deactivating_account_call_finalizes_with_dispatch_jwt(
    monkeypatch: pytest.MonkeyPatch,
    db_session,
    active_user,
    wakeup_fails: bool,
) -> None:
    dispatch_secret = "dispatch-test-secret-with-enough-entropy-for-tests"
    _configure_auth(
        monkeypatch,
        app_env="test",
        dispatch_secret=dispatch_secret,
    )
    config = AgentConfig(
        user_id=active_user.id,
        agent_name="JWT runtime",
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
        status="connected",
    )
    db_session.add(call)
    active_user.status = "deactivating"
    await db_session.commit()
    fake_queue = FakeCallFinalizationQueue(session=db_session)
    outbox_pool = FakeOutboxPool(failure=wakeup_fails)
    app = _build_completion_app(
        fake_queue,
        auth_session=db_session,
        arq_pool=outbox_pool,
    )
    token = create_dispatch_token(
        call_id=str(call.id),
        user_id=str(active_user.id),
        agent_config_id=str(config.id),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            f"/api/agent/calls/{call.id}/complete",
            headers={"x-agent-token": token},
            json=_completion_payload(duration_seconds=1),
        )

    assert response.status_code == 202
    assert len(fake_queue.calls) == 1
    assert outbox_pool.jobs == [("outbox_delivery_job", {})]


@pytest.mark.anyio
async def test_queue_outage_preserves_end_facts_recovery_and_recording_stop_intent(
    monkeypatch: pytest.MonkeyPatch,
    db_session,
    active_user,
) -> None:
    _configure_auth(
        monkeypatch,
        app_env="test",
        dispatch_secret="dispatch-test-secret-with-enough-entropy-for-tests",
    )
    config = AgentConfig(
        user_id=active_user.id,
        agent_name="Durable completion",
        system_prompt="Be helpful",
        knowledge_base="",
        is_enabled=True,
    )
    db_session.add(config)
    await db_session.flush()
    call = Call(
        user_id=active_user.id,
        agent_config_id=config.id,
        status="connected",
        started_at=datetime.now(UTC),
        recording_egress_id="egress-durable-stop",
    )
    db_session.add(call)
    await db_session.commit()
    call_id = call.id
    app = _build_completion_app(
        FailingCallFinalizationQueue(),
        auth_session=db_session,
    )
    token = create_dispatch_token(
        call_id=str(call_id),
        user_id=str(active_user.id),
        agent_config_id=str(config.id),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/agent/calls/{call_id}/complete",
            headers={"x-agent-token": token},
            json=_completion_payload(
                duration_seconds=7,
                transcript=[
                    {
                        "sequence_number": 1,
                        "speaker": "CALLER",
                        "text": "Durable recovery tail",
                    }
                ],
            ),
        )

    assert response.status_code == 503
    db_session.expire_all()
    stored = await db_session.get(Call, call_id)
    assert stored.status == "ending"
    assert stored.duration_seconds == 7
    assert stored.ended_at is not None
    operation = await db_session.scalar(
        select(RecordingEgressOperation).where(
            RecordingEgressOperation.call_id == call_id
        )
    )
    assert operation is not None
    stop_intent = await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.topic == "recording.reconcile",
            OutboxEvent.aggregate_id == operation.id,
        )
    )
    assert stop_intent is not None
    assert operation.stop_requested_at is not None
    assert stop_intent.aggregate_type == "recording-egress-operation"
    assert stop_intent.payload == {"operation_id": str(operation.id)}
    recovery_rows = list(
        (
            await db_session.execute(
                select(CallMessage)
                .where(CallMessage.call_id == call_id)
                .order_by(CallMessage.sequence_number)
            )
        ).scalars()
    )
    assert [
        (row.sequence_number, row.speaker, row.text) for row in recovery_rows
    ] == [(1, "CALLER", "Durable recovery tail")]


@pytest.mark.anyio
async def test_failed_call_completion_is_conflict_and_does_not_enqueue(
    monkeypatch: pytest.MonkeyPatch,
    db_session,
    active_user,
) -> None:
    _configure_auth(
        monkeypatch,
        app_env="test",
        dispatch_secret="dispatch-test-secret-with-enough-entropy-for-tests",
    )
    config = AgentConfig(
        user_id=active_user.id,
        agent_name="Failed completion",
        system_prompt="Be helpful",
        knowledge_base="",
        is_enabled=True,
    )
    db_session.add(config)
    await db_session.flush()
    call = Call(
        user_id=active_user.id,
        agent_config_id=config.id,
        status="failed",
        failure_code="dispatch_timeout",
    )
    db_session.add(call)
    await db_session.commit()
    fake_queue = FakeCallFinalizationQueue()
    app = _build_completion_app(fake_queue, auth_session=db_session)
    token = create_dispatch_token(
        call_id=str(call.id),
        user_id=str(active_user.id),
        agent_config_id=str(config.id),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/agent/calls/{call.id}/complete",
            headers={"x-agent-token": token},
            json=_completion_payload(duration_seconds=1),
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "call_not_accepting_completion"}
    assert fake_queue.calls == []


@pytest.mark.anyio
async def test_completed_call_accepts_scoped_recovery_tail_then_finalizes_idempotently(
    monkeypatch: pytest.MonkeyPatch,
    db_session,
    active_user,
) -> None:
    _configure_auth(
        monkeypatch,
        app_env="test",
        dispatch_secret="dispatch-test-secret-with-enough-entropy-for-tests",
    )
    config = AgentConfig(
        user_id=active_user.id,
        agent_name="Terminal recovery",
        system_prompt="Be helpful",
        knowledge_base="",
        is_enabled=True,
    )
    db_session.add(config)
    await db_session.flush()
    call = Call(
        user_id=active_user.id,
        agent_config_id=config.id,
        status="completed",
        duration_seconds=10,
        minutes_charged=1,
        finalization_attempt_count=1,
        summary_text="Summary through sequence one",
        summary_data={"summary_text": "Summary through sequence one"},
    )
    call.summary_transcript_max_sequence = 1
    db_session.add(call)
    await db_session.flush()
    db_session.add(
        CallMessage(
            call_id=call.id,
            sequence_number=1,
            speaker="CALLER",
            text="Already durable",
        )
    )
    await OutboxService(db_session).add(
        topic="summary.generate",
        aggregate_type="call-summary",
        aggregate_id=call.id,
        idempotency_key=f"summary.generate:{call.id}:v1",
        payload={"call_id": str(call.id)},
    )
    await db_session.commit()
    queue = FakeCallFinalizationQueue(session=db_session)
    app = _build_completion_app(queue, auth_session=db_session)
    token = create_dispatch_token(
        call_id=str(call.id),
        user_id=str(active_user.id),
        agent_config_id=str(config.id),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/agent/calls/{call.id}/complete",
            headers={"x-agent-token": token},
            json=_completion_payload(
                duration_seconds=999,
                transcript=[
                    {
                        "sequence_number": 2,
                        "speaker": "AGENT",
                        "text": "Late recovery tail",
                    }
                ],
            ),
        )

    assert response.status_code == 202

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        duplicate = await client.post(
            f"/api/agent/calls/{call.id}/complete",
            headers={"x-agent-token": token},
            json=_completion_payload(
                duration_seconds=999,
                transcript=[
                    {
                        "sequence_number": 2,
                        "speaker": "AGENT",
                        "text": "Late recovery tail",
                    }
                ],
            ),
        )
    assert duplicate.status_code == 202
    messages = list(
        (
            await db_session.execute(
                select(CallMessage)
                .where(CallMessage.call_id == call.id)
                .order_by(CallMessage.sequence_number)
            )
        ).scalars()
    )
    assert [(message.sequence_number, message.text) for message in messages] == [
        (1, "Already durable"),
        (2, "Late recovery tail"),
    ]
    summary_intents = list(
        (
            await db_session.execute(
                select(OutboxEvent)
                .where(
                    OutboxEvent.topic == "summary.generate",
                    OutboxEvent.aggregate_id == call.id,
                )
                .order_by(OutboxEvent.idempotency_key)
            )
        ).scalars()
    )
    assert [intent.idempotency_key for intent in summary_intents] == [
        f"summary.generate:{call.id}:v1",
        f"summary.generate:{call.id}:v2",
    ]
    assert all(
        intent.payload == {"call_id": str(call.id)} for intent in summary_intents
    )
    result = await CallLifecycleService(db_session).complete_finalization(
        call.id,
        generation=1,
    )
    assert result.already_completed is True
    assert result.minutes_charged == 1


@pytest.mark.anyio
async def test_dispatch_jwt_for_call_a_cannot_complete_call_b(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_auth(
        monkeypatch,
        app_env="test",
        dispatch_secret="dispatch-test-secret-with-enough-entropy-for-tests",
    )
    call_a_id = uuid4()
    call_b_id = uuid4()
    user_id = uuid4()
    agent_config_id = uuid4()
    fake_queue = FakeCallFinalizationQueue()
    app = _build_completion_app(fake_queue, auth_session=FakeAuthSession())
    token = create_dispatch_token(
        call_id=str(call_a_id),
        user_id=str(user_id),
        agent_config_id=str(agent_config_id),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            f"/api/agent/calls/{call_b_id}/complete",
            headers={"x-agent-token": token},
            json=_completion_payload(duration_seconds=1),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid agent token"}
    assert fake_queue.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("call_user_matches", "call_config_matches", "config_user_matches"),
    [
        (False, True, True),
        (True, False, True),
        (True, True, False),
    ],
)
async def test_dispatch_jwt_rejects_durable_ownership_or_config_mismatch_before_queue(
    monkeypatch: pytest.MonkeyPatch,
    call_user_matches: bool,
    call_config_matches: bool,
    config_user_matches: bool,
) -> None:
    _configure_auth(
        monkeypatch,
        app_env="test",
        dispatch_secret="dispatch-test-secret-with-enough-entropy-for-tests",
    )
    call_id = uuid4()
    signed_user_id = uuid4()
    signed_agent_config_id = uuid4()
    call_user_id = signed_user_id if call_user_matches else uuid4()
    call_agent_config_id = (
        signed_agent_config_id if call_config_matches else uuid4()
    )
    config_user_id = signed_user_id if config_user_matches else uuid4()
    auth_session = FakeAuthSession(
        call=SimpleNamespace(
            id=call_id,
            user_id=call_user_id,
            agent_config_id=call_agent_config_id,
        ),
        agent_config=SimpleNamespace(
            id=signed_agent_config_id,
            user_id=config_user_id,
        ),
    )
    fake_queue = FakeCallFinalizationQueue()
    app = _build_completion_app(fake_queue, auth_session=auth_session)
    token = create_dispatch_token(
        call_id=str(call_id),
        user_id=str(signed_user_id),
        agent_config_id=str(signed_agent_config_id),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            f"/api/agent/calls/{call_id}/complete",
            headers={"x-agent-token": token},
            json=_completion_payload(duration_seconds=1),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid agent token"}
    assert fake_queue.calls == []
