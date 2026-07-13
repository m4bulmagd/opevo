import json
from types import SimpleNamespace

import pytest
import httpx

from agent.api_client import AgentApiClient


@pytest.mark.anyio
async def test_complete_call_prefers_dispatch_token_and_excludes_auth_and_phone_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_url = "http://localhost:8000"
    dispatch_token = "call-scoped-dispatch-token"
    monkeypatch.setattr(
        "agent.api_client.get_settings",
        lambda: SimpleNamespace(
            api_base_url=api_url,
            agent_internal_api_token=None,
            api_timeout_seconds=10.0,
            api_max_retries=3,
            app_env="test",
        ),
    )

    payload = {
        "call_id": "call_abc",
        "duration_seconds": 120,
        "transcript": [{"speaker": "CALLER", "text": "hello"}],
        "caller_number": "+1234567890",
        "recording_bytes_base64": "cmVjb3JkaW5nLWJ5dGVz",
        "dispatch_token": dispatch_token,
    }

    request_captured = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_captured
        request_captured = request
        return httpx.Response(200, json={"status": "success"})

    transport = httpx.MockTransport(handler)
    mock_client = httpx.AsyncClient(transport=transport)

    client = AgentApiClient(base_url=api_url, agent_token=None, http_client=mock_client)
    result = await client.complete_call(payload)

    assert result == {"status": "success"}
    assert request_captured is not None

    assert request_captured.headers["x-agent-token"] == dispatch_token
    assert str(request_captured.url) == f"{api_url}/api/agent/calls/call_abc/complete"

    body = request_captured.read().decode("utf-8")
    data = json.loads(body)
    assert data["duration_seconds"] == 120
    assert "dispatch_token" not in data
    assert "caller_number" not in data
    assert "user_id" not in data
    assert "minutes_remaining" not in data
    assert data["transcript"] == [{"speaker": "CALLER", "text": "hello"}]
    assert data["recording_bytes_base64"] == "cmVjb3JkaW5nLWJ5dGVz"


@pytest.mark.anyio
async def test_complete_call_uses_static_token_only_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_url = "http://localhost:8000"
    monkeypatch.setattr(
        "agent.api_client.get_settings",
        lambda: SimpleNamespace(
            api_base_url=api_url,
            agent_internal_api_token="development-static-token",
            api_timeout_seconds=10.0,
            api_max_retries=3,
            app_env="development",
        ),
    )
    payload = {
        "call_id": "call_xyz",
        "duration_seconds": 45,
    }
    
    request_captured = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_captured
        request_captured = request
        return httpx.Response(200, json={"status": "success"})

    transport = httpx.MockTransport(handler)
    mock_client = httpx.AsyncClient(transport=transport)

    client = AgentApiClient(base_url=api_url, http_client=mock_client)
    await client.complete_call(payload)

    assert request_captured is not None
    assert request_captured.headers["x-agent-token"] == "development-static-token"
    data = json.loads(request_captured.read().decode("utf-8"))

    assert data["transcript"] == []
    assert "caller_number" not in data


@pytest.mark.anyio
@pytest.mark.parametrize("app_env", ["test", "staging", "production"])
async def test_complete_call_requires_dispatch_token_outside_development(
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
) -> None:
    monkeypatch.setattr(
        "agent.api_client.get_settings",
        lambda: SimpleNamespace(
            api_base_url="http://test",
            agent_internal_api_token="legacy-static-token",
            api_timeout_seconds=10.0,
            api_max_retries=3,
            app_env=app_env,
        ),
    )

    client = AgentApiClient(base_url="http://test")
    with pytest.raises(ValueError, match="Dispatch token is required"):
        await client.complete_call({"call_id": "call_1", "duration_seconds": 1})
