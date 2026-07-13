import base64
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.core.database import get_session
from app.core.dispatch_token import create_dispatch_token
from app.models.agent_config import AgentConfig
from app.models.call import Call


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

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.call)

    async def commit(self) -> None:
        self.commits += 1


def _configure_auth(
    monkeypatch: pytest.MonkeyPatch,
    *,
    app_env: str,
    static_token: str = "test-agent-token",
    dispatch_secret: str | None = None,
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("AGENT_INTERNAL_API_TOKEN", static_token)
    if dispatch_secret is None:
        monkeypatch.delenv("AGENT_DISPATCH_JWT_SECRET", raising=False)
    else:
        monkeypatch.setenv("AGENT_DISPATCH_JWT_SECRET", dispatch_secret)

    from app.core.config import get_settings

    get_settings.cache_clear()


def _build_completion_app(fake_queue, *, auth_session=None):
    from app.routers.agent import get_call_finalization_queue
    from app.routers.agent import router as agent_router

    async def override_get_call_finalization_queue():
        return fake_queue

    app = FastAPI()
    app.include_router(agent_router)
    app.state.call_finalization_queue = fake_queue
    app.dependency_overrides[get_call_finalization_queue] = (
        override_get_call_finalization_queue
    )
    session = auth_session or FakeAuthSession(
        call=SimpleNamespace(status="pending"),
    )

    async def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    return app


@pytest.mark.anyio
async def test_agent_completion_endpoint_enqueues_call_finalization_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_auth(monkeypatch, app_env="development")
    call_id = uuid4()
    fake_queue = FakeCallFinalizationQueue()
    app = _build_completion_app(fake_queue)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            f"/api/agent/calls/{call_id}/complete",
            headers={"x-agent-token": "test-agent-token"},
            json={
                "duration_seconds": 61,
                "transcript": [],
            },
        )

    assert response.status_code == 202
    assert response.json() == {
        "status": "accepted",
        "queued": True,
        "job_id": f"call-finalization:{call_id}",
    }
    assert fake_queue.calls == [
        FakeQueueCall(
            job_name="call_finalization_job",
            payload={
                "call_id": str(call_id),
                "duration_seconds": 61,
                "caller_number": None,
                "recording_bytes": None,
            },
            job_id=f"call-finalization:{call_id}",
        )
    ]


@pytest.mark.anyio
async def test_agent_completion_endpoint_rejects_accounting_authority_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_auth(monkeypatch, app_env="development")
    call_id = uuid4()
    fake_queue = FakeCallFinalizationQueue()

    app = _build_completion_app(fake_queue)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/agent/calls/{call_id}/complete",
            headers={"x-agent-token": "test-agent-token"},
            json={
                "user_id": str(uuid4()),
                "duration_seconds": 61,
                "minutes_remaining": 999,
                "transcript": [],
            },
        )

    assert response.status_code == 422
    assert fake_queue.calls == []


@pytest.mark.anyio
async def test_agent_completion_endpoint_decodes_recording_for_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_auth(monkeypatch, app_env="development")
    call_id = uuid4()
    fake_queue = FakeCallFinalizationQueue()

    app = _build_completion_app(fake_queue)
    recording_bytes = b"recording-bytes"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/agent/calls/{call_id}/complete",
            headers={"x-agent-token": "test-agent-token"},
            json={
                "duration_seconds": 61,
                "recording_bytes_base64": base64.b64encode(
                    recording_bytes
                ).decode("ascii"),
            },
        )

    assert response.status_code == 202
    assert fake_queue.calls[0].payload["recording_bytes"] == recording_bytes


@pytest.mark.anyio
async def test_agent_completion_endpoint_rejects_negative_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_auth(monkeypatch, app_env="development")
    call_id = uuid4()
    fake_queue = FakeCallFinalizationQueue()

    app = _build_completion_app(fake_queue)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/agent/calls/{call_id}/complete",
            headers={"x-agent-token": "test-agent-token"},
            json={"duration_seconds": -1, "transcript": []},
        )

    assert response.status_code == 422
    assert fake_queue.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("app_env", ["test", "staging", "production"])
async def test_static_agent_token_is_rejected_outside_development(
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
            json={"duration_seconds": 1},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid agent token"}
    assert fake_queue.calls == []


@pytest.mark.anyio
async def test_dispatch_jwt_completes_call_without_static_token(
    monkeypatch: pytest.MonkeyPatch,
    db_session,
    active_user,
) -> None:
    dispatch_secret = "dispatch-test-secret-with-enough-entropy-for-tests"
    _configure_auth(
        monkeypatch,
        app_env="test",
        static_token="",
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
    await db_session.commit()
    fake_queue = FakeCallFinalizationQueue(session=db_session)
    app = _build_completion_app(fake_queue, auth_session=db_session)
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
            json={"duration_seconds": 1},
        )

    assert response.status_code == 202
    assert len(fake_queue.calls) == 1


@pytest.mark.anyio
async def test_dispatch_jwt_for_call_a_cannot_complete_call_b(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_auth(
        monkeypatch,
        app_env="test",
        static_token="",
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
            json={"duration_seconds": 1},
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
        static_token="",
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
            json={"duration_seconds": 1},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid agent token"}
    assert fake_queue.calls == []
