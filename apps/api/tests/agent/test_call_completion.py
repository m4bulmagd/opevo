from dataclasses import dataclass
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI


@dataclass
class FakeQueueCall:
    job_name: str
    payload: dict
    job_id: str


class FakeCallFinalizationQueue:
    def __init__(self) -> None:
        self.calls: list[FakeQueueCall] = []

    async def enqueue(self, payload: dict) -> str:
        job_id = f"call-finalization:{payload['call_id']}"
        self.calls.append(
            FakeQueueCall(
                job_name="call_finalization_job",
                payload=payload,
                job_id=job_id,
            )
        )
        return job_id


@pytest.mark.anyio
async def test_agent_completion_endpoint_enqueues_call_finalization_job() -> None:
    call_id = uuid4()
    fake_queue = FakeCallFinalizationQueue()

    from app.routers.agent import get_call_finalization_queue
    from app.routers.agent import router as agent_router

    async def override_get_call_finalization_queue():
        return fake_queue

    app = FastAPI()
    app.include_router(agent_router)
    app.dependency_overrides[get_call_finalization_queue] = override_get_call_finalization_queue

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            f"/api/agent/calls/{call_id}/complete",
            headers={"x-agent-token": "test-agent-token"},
            json={
                "duration_seconds": 61,
                "transcript": [
                    {"speaker": "CALLER", "text": "What time do you open?"},
                    {"speaker": "AGENT", "text": "We open at nine."},
                ],
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
                "transcript": [
                    {"speaker": "CALLER", "text": "What time do you open?"},
                    {"speaker": "AGENT", "text": "We open at nine."},
                ],
                "recording_bytes": None,
            },
            job_id=f"call-finalization:{call_id}",
        )
    ]


@pytest.mark.anyio
async def test_agent_completion_endpoint_rejects_accounting_authority_fields() -> None:
    call_id = uuid4()
    fake_queue = FakeCallFinalizationQueue()

    from app.routers.agent import get_call_finalization_queue
    from app.routers.agent import router as agent_router

    async def override_get_call_finalization_queue():
        return fake_queue

    app = FastAPI()
    app.include_router(agent_router)
    app.dependency_overrides[get_call_finalization_queue] = (
        override_get_call_finalization_queue
    )

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
async def test_agent_completion_endpoint_rejects_negative_duration() -> None:
    call_id = uuid4()
    fake_queue = FakeCallFinalizationQueue()

    from app.routers.agent import get_call_finalization_queue
    from app.routers.agent import router as agent_router

    async def override_get_call_finalization_queue():
        return fake_queue

    app = FastAPI()
    app.include_router(agent_router)
    app.dependency_overrides[get_call_finalization_queue] = (
        override_get_call_finalization_queue
    )

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
