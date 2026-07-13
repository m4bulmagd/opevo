import json
import pytest
import httpx

from agent.api_client import AgentApiClient


@pytest.mark.anyio
async def test_complete_call_sends_correct_payload() -> None:
    api_url = "http://localhost:8000"
    token = "test_token"
    
    payload = {
        "call_id": "call_abc",
        "duration_seconds": 120,
        "transcript": [{"speaker": "CALLER", "text": "hello"}],
        "caller_number": "+1234567890",
        "recording_bytes_base64": "cmVjb3JkaW5nLWJ5dGVz",
    }
    
    request_captured = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_captured
        request_captured = request
        return httpx.Response(200, json={"status": "success"})

    transport = httpx.MockTransport(handler)
    mock_client = httpx.AsyncClient(transport=transport)

    client = AgentApiClient(base_url=api_url, agent_token=token, http_client=mock_client)
    result = await client.complete_call(payload)
    
    assert result == {"status": "success"}
    assert request_captured is not None
    
    assert request_captured.headers["x-agent-token"] == "test_token"
    assert str(request_captured.url) == f"{api_url}/api/agent/calls/call_abc/complete"
    
    body = request_captured.read().decode("utf-8")
    data = json.loads(body)
    assert data["duration_seconds"] == 120
    assert "user_id" not in data
    assert "minutes_remaining" not in data
    assert data["caller_number"] == "+1234567890"
    assert data["transcript"] == [{"speaker": "CALLER", "text": "hello"}]
    assert data["recording_bytes_base64"] == "cmVjb3JkaW5nLWJ5dGVz"


@pytest.mark.anyio
async def test_complete_call_handles_missing_optional_fields() -> None:
    api_url = "http://localhost:8000"
    
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

    client = AgentApiClient(base_url=api_url, agent_token="t", http_client=mock_client)
    await client.complete_call(payload)
    
    assert request_captured is not None
    data = json.loads(request_captured.read().decode("utf-8"))
    
    assert data["transcript"] == []
    assert data["caller_number"] is None

@pytest.mark.anyio
async def test_complete_call_raises_without_token(monkeypatch) -> None:
    from agent.config import AgentSettings
    
    # Mock settings so it doesn't fall back to env var
    monkeypatch.setattr("agent.api_client.get_settings", lambda: AgentSettings(api_base_url="http://test", agent_internal_api_token=""))
    
    client = AgentApiClient(base_url="http://test", agent_token="")
    with pytest.raises(ValueError, match="AGENT_INTERNAL_API_TOKEN is required"):
        await client.complete_call({"call_id": "call_1", "duration_seconds": 1})
