from dataclasses import dataclass
from uuid import uuid4
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI


@dataclass
class FakeFinalizationResult:
    minutes_charged: int
    summary_text: str | None
    recording_key: str | None
    number_disabled: bool


class FakeLifecycleService:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def finalize_call(self, payload: dict) -> FakeFinalizationResult:
        self.payloads.append(payload)
        return FakeFinalizationResult(
            minutes_charged=2,
            summary_text="Caller request: What time do you open?",
            recording_key=None,
            number_disabled=False,
        )


@pytest.mark.anyio
async def test_agent_completion_endpoint_forwards_payload_to_lifecycle_service(
) -> None:
    call_id = uuid4()
    fake_service = FakeLifecycleService()

    from app.routers.agent import get_call_lifecycle_service
    from app.routers.agent import router as agent_router

    async def override_get_call_lifecycle_service():
        return fake_service

    app = FastAPI()
    app.include_router(agent_router)
    app.dependency_overrides[get_call_lifecycle_service] = override_get_call_lifecycle_service

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            f"/api/agent/calls/{call_id}/complete",
            headers={"x-agent-token": "test-agent-token"},
            json={
                "user_id": "2a8d01e5-09ed-4788-b9b7-f6a37aa2b1c0",
                "duration_seconds": 61,
                "minutes_remaining": 10,
                "transcript": [
                    {"speaker": "CALLER", "text": "What time do you open?"},
                    {"speaker": "AGENT", "text": "We open at nine."},
                ],
            },
        )

    assert response.status_code == 202
    assert response.json() == {
        "status": "accepted",
        "minutes_charged": 2,
        "summary_text": "Caller request: What time do you open?",
        "recording_key": None,
        "number_disabled": False,
    }
    assert fake_service.payloads == [
        {
            "call_id": str(call_id),
            "user_id": UUID("2a8d01e5-09ed-4788-b9b7-f6a37aa2b1c0"),
            "duration_seconds": 61,
            "minutes_remaining": 10,
            "caller_number": None,
            "transcript": [
                {"speaker": "CALLER", "text": "What time do you open?"},
                {"speaker": "AGENT", "text": "We open at nine."},
            ],
            "recording_bytes": None,
        }
    ]
