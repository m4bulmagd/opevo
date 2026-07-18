import json
import logging
from types import SimpleNamespace

import pytest
import httpx
from pydantic import ValidationError

import agent.api_client as api_client_module
from agent.api_client import (
    AgentApiClient,
    TranscriptAppendPermanentError,
    TranscriptAppendRetryableError,
)
from agent.schemas import CallTranscriptItem


async def _completed_sleep() -> None:
    return None


@pytest.mark.anyio
async def test_complete_verification_posts_only_scoped_token_and_empty_body() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"status": "verified", "session_id": "session-1"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AgentApiClient(
            base_url="http://api.test",
            http_client=http_client,
        )

        result = await client.complete_verification(
            "session-1",
            "verification-token",
        )

    assert result == {"status": "verified", "session_id": "session-1"}
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == (
        "http://api.test/api/activation/verification/session-1/complete"
    )
    assert request.headers["x-verification-token"] == "verification-token"
    assert "x-agent-token" not in request.headers
    assert json.loads(request.content) == {}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("session_id", "token"),
    [
        ("", "token"),
        ("   ", "token"),
        ("session", ""),
        ("session", "   "),
        (None, "token"),
        (123, "token"),
        ("session", None),
        ("session", object()),
    ],
)
async def test_complete_verification_rejects_empty_inputs(
    session_id: object,
    token: object,
) -> None:
    client = AgentApiClient(base_url="http://api.test")

    with pytest.raises(api_client_module.VerificationCompletionPermanentError):
        await client.complete_verification(session_id, token)


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [408, 425, 429, 500, 502, 599])
async def test_complete_verification_retries_only_transient_statuses(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    attempts = 0
    sleeps: list[float] = []

    async def no_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(api_client_module.asyncio, "sleep", no_sleep)

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(status_code, text="RESPONSE_BODY_SENTINEL")
        return httpx.Response(
            200,
            json={"status": "verified", "session_id": "session-1"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AgentApiClient(
            base_url="http://api.test",
            http_client=http_client,
            max_retries=2,
        )

        await client.complete_verification("session-1", "token-sentinel")

    assert attempts == 2
    assert sleeps == [1]


@pytest.mark.anyio
async def test_complete_verification_retries_transport_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    monkeypatch.setattr(
        api_client_module.asyncio,
        "sleep",
        lambda _delay: _completed_sleep(),
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("TRANSPORT_MESSAGE_SENTINEL")
        return httpx.Response(
            200,
            json={"status": "verified", "session_id": "session-1"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AgentApiClient(
            base_url="http://api.test",
            http_client=http_client,
            max_retries=2,
        )

        await client.complete_verification("session-1", "token-sentinel")

    assert attempts == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status_code",
    [400, 401, 403, 404, 409, 422, 600, 601],
)
async def test_complete_verification_rejects_permanent_status_without_retry(
    status_code: int,
) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code, text="RESPONSE_BODY_SENTINEL")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AgentApiClient(
            base_url="http://api.test",
            http_client=http_client,
            max_retries=3,
        )

        with pytest.raises(api_client_module.VerificationCompletionPermanentError):
            await client.complete_verification("session-1", "token-sentinel")

    assert attempts == 1


@pytest.mark.anyio
@pytest.mark.parametrize("log_level", [logging.INFO, logging.DEBUG])
async def test_complete_verification_filters_http_client_urls_at_verbose_levels(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    log_level: int,
) -> None:
    session_id = "SESSION_URL_SENTINEL"
    token = "VERIFICATION_TOKEN_SENTINEL"
    base_url = "http://api.test/PATH_SENTINEL"
    attempts = 0
    monkeypatch.setattr(
        api_client_module.asyncio,
        "sleep",
        lambda _delay: _completed_sleep(),
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(500, text="RESPONSE_BODY_SENTINEL")
        return httpx.Response(
            200,
            json={"status": "verified", "session_id": session_id},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AgentApiClient(
            base_url=base_url,
            http_client=http_client,
            max_retries=2,
        )

        with caplog.at_level(log_level):
            logging.getLogger("httpcore.connection").log(
                log_level,
                "httpcore path=%s",
                f"/PATH_SENTINEL/{session_id}",
            )
            await client.complete_verification(session_id, token)

    assert "complete_verification attempt 1/2" in caplog.text
    assert "classification=http status=500" in caplog.text
    for sentinel in [
        session_id,
        token,
        "PATH_SENTINEL",
        "RESPONSE_BODY_SENTINEL",
    ]:
        assert sentinel not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json={"status": "verified"}),
        httpx.Response(200, json={"status": "wrong", "session_id": "session-1"}),
        httpx.Response(200, json={"status": "verified", "session_id": "wrong"}),
        httpx.Response(
            200,
            json={"status": "verified", "session_id": "session-1", "extra": True},
        ),
    ],
)
async def test_complete_verification_rejects_malformed_or_mismatched_success(
    response: httpx.Response,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: response)
    ) as http_client:
        client = AgentApiClient(base_url="http://api.test", http_client=http_client)

        with pytest.raises(
            api_client_module.VerificationCompletionAcknowledgementError
        ):
            await client.complete_verification("session-1", "token-sentinel")


@pytest.mark.anyio
async def test_complete_verification_exhaustion_redacts_all_sensitive_values(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session_id = "SESSION_ID_SENTINEL"
    token = "VERIFICATION_TOKEN_SENTINEL"
    base_url = "http://api.test/PATH_SENTINEL"
    monkeypatch.setattr(
        api_client_module.asyncio,
        "sleep",
        lambda _delay: _completed_sleep(),
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("TRANSPORT_MESSAGE_SENTINEL")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AgentApiClient(
            base_url=base_url,
            http_client=http_client,
            max_retries=2,
        )

        with caplog.at_level(logging.WARNING), pytest.raises(
            api_client_module.VerificationCompletionRetryableError
        ) as caught:
            await client.complete_verification(session_id, token)

    combined = caplog.text + str(caught.value)
    assert "attempt 1/2" in combined
    assert "classification=transport" in combined
    for sentinel in [
        session_id,
        token,
        "PATH_SENTINEL",
        "TRANSPORT_MESSAGE_SENTINEL",
    ]:
        assert sentinel not in combined


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
            api_timeout_seconds=10.0,
            api_max_retries=3,
        ),
    )

    payload = {
        "call_id": "call_abc",
        "duration_seconds": 120,
        "transcript": [{"speaker": "CALLER", "text": "hello"}],
        "caller_number": "+1234567890",
        "dispatch_token": dispatch_token,
    }

    request_captured = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_captured
        request_captured = request
        return httpx.Response(
            200,
            json={
                "status": "accepted",
                "queued": True,
                "job_id": "call-finalization:call_abc",
            },
        )

    transport = httpx.MockTransport(handler)
    mock_client = httpx.AsyncClient(transport=transport)

    client = AgentApiClient(base_url=api_url, http_client=mock_client)
    result = await client.complete_call(payload)

    assert result == {
        "status": "accepted",
        "queued": True,
        "job_id": "call-finalization:call_abc",
    }
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
    assert "recording_bytes_base64" not in data


@pytest.mark.anyio
async def test_complete_call_requires_dispatch_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent.api_client.get_settings",
        lambda: SimpleNamespace(
            api_base_url="http://test",
            api_timeout_seconds=10.0,
            api_max_retries=3,
        ),
    )

    client = AgentApiClient(base_url="http://test")
    with pytest.raises(ValueError, match="Dispatch token is required"):
        await client.complete_call({"call_id": "call_1", "duration_seconds": 1})


def test_transcript_item_normalizes_and_validates_the_wire_contract() -> None:
    item = CallTranscriptItem(
        sequence_number=1,
        speaker="CALLER",
        text="  bonjour  ",
    )

    assert item.model_dump() == {
        "sequence_number": 1,
        "speaker": "CALLER",
        "text": "bonjour",
    }

    with pytest.raises(ValidationError):
        item.sequence_number = 2
    with pytest.raises(ValidationError):
        CallTranscriptItem(sequence_number=0, speaker="CALLER", text="bonjour")
    with pytest.raises(ValidationError):
        CallTranscriptItem(sequence_number=1, speaker="SYSTEM", text="bonjour")
    with pytest.raises(ValidationError):
        CallTranscriptItem(sequence_number=1, speaker="AGENT", text="   ")
    with pytest.raises(ValidationError):
        CallTranscriptItem(sequence_number=1, speaker="AGENT", text="x" * 4001)


@pytest.mark.anyio
async def test_append_transcript_posts_one_call_scoped_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"status": "stored", "sequence_number": 7},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AgentApiClient(base_url="http://api.test", http_client=http_client)
        item = CallTranscriptItem(
            sequence_number=7,
            speaker="AGENT",
            text="ready",
        )

        result = await client.append_transcript(
            "call_7",
            "dispatch-secret",
            item,
        )

    assert result == {"status": "stored", "sequence_number": 7}
    assert len(requests) == 1
    assert str(requests[0].url) == "http://api.test/api/agent/calls/call_7/transcript"
    assert requests[0].headers["x-agent-token"] == "dispatch-secret"
    assert json.loads(requests[0].content) == item.model_dump()


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [408, 425, 429, 500, 502, 503, 599])
async def test_append_transcript_classifies_retryable_http_statuses(
    status_code: int,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="TRANSCRIPT_RESPONSE_SECRET")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AgentApiClient(base_url="http://api.test", http_client=http_client)

        with pytest.raises(TranscriptAppendRetryableError) as caught:
            await client.append_transcript(
                "call_1",
                "dispatch-secret",
                CallTranscriptItem(
                    sequence_number=1,
                    speaker="CALLER",
                    text="sensitive transcript",
                ),
            )

    assert "TRANSCRIPT_RESPONSE_SECRET" not in str(caught.value)
    assert "dispatch-secret" not in str(caught.value)
    assert "sensitive transcript" not in str(caught.value)


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 409, 422])
async def test_append_transcript_classifies_permanent_http_statuses(
    status_code: int,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="TRANSCRIPT_RESPONSE_SECRET")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AgentApiClient(base_url="http://api.test", http_client=http_client)

        with pytest.raises(TranscriptAppendPermanentError):
            await client.append_transcript(
                "call_1",
                "dispatch-secret",
                CallTranscriptItem(
                    sequence_number=1,
                    speaker="CALLER",
                    text="sensitive transcript",
                ),
            )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("response_body", "expected_sequence"),
    [
        ({"status": "stored", "sequence_number": 2}, 1),
        ({"status": "unknown", "sequence_number": 1}, 1),
        ({"status": "stored"}, 1),
        ({"status": "stored", "sequence_number": "1"}, 1),
    ],
)
async def test_append_transcript_rejects_malformed_or_mismatched_acknowledgements(
    response_body: dict,
    expected_sequence: int,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AgentApiClient(base_url="http://api.test", http_client=http_client)

        with pytest.raises(TranscriptAppendPermanentError):
            await client.append_transcript(
                "call_1",
                "dispatch-secret",
                CallTranscriptItem(
                    sequence_number=expected_sequence,
                    speaker="CALLER",
                    text="bonjour",
                ),
            )


@pytest.mark.anyio
async def test_append_transcript_rejects_non_json_success() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AgentApiClient(base_url="http://api.test", http_client=http_client)

        with pytest.raises(TranscriptAppendPermanentError):
            await client.append_transcript(
                "call_1",
                "dispatch-secret",
                CallTranscriptItem(
                    sequence_number=1,
                    speaker="CALLER",
                    text="bonjour",
                ),
            )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "transport_error",
    [
        httpx.ConnectError("AUTHORIZATION_SENTINEL"),
        httpx.ReadTimeout("TEXT_SENTINEL"),
        httpx.ReadError("TEXT_SENTINEL"),
        httpx.WriteError("TEXT_SENTINEL"),
        httpx.RemoteProtocolError("TEXT_SENTINEL"),
    ],
)
async def test_append_transcript_classifies_transport_failures_without_logging_secrets(
    transport_error: Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise transport_error

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AgentApiClient(base_url="http://api.test", http_client=http_client)

        with caplog.at_level(logging.DEBUG), pytest.raises(
            TranscriptAppendRetryableError
        ) as caught:
            await client.append_transcript(
                "call_1",
                "dispatch-secret",
                CallTranscriptItem(
                    sequence_number=1,
                    speaker="CALLER",
                    text="sensitive transcript",
                ),
            )

    combined = caplog.text + str(caught.value)
    assert "AUTHORIZATION_SENTINEL" not in combined
    assert "TEXT_SENTINEL" not in combined
    assert "dispatch-secret" not in combined
    assert "sensitive transcript" not in combined


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [408, 425, 429, 500, 503])
async def test_complete_call_retries_transient_statuses(
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("agent.api_client.asyncio.sleep", no_sleep)

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(status_code, text="RESPONSE_BODY_SENTINEL")
        return httpx.Response(
            200,
            json={
                "status": "accepted",
                "queued": True,
                "job_id": "call-finalization:call_1",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AgentApiClient(
            base_url="http://api.test",
            http_client=http_client,
            max_retries=2,
        )
        result = await client.complete_call(
            {
                "call_id": "call_1",
                "duration_seconds": 1,
                "dispatch_token": "dispatch-secret",
            }
        )

    assert attempts == 2
    assert result["job_id"] == "call-finalization:call_1"


@pytest.mark.anyio
async def test_complete_call_retries_broader_transport_errors_without_logging_content(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("agent.api_client.asyncio.sleep", no_sleep)

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.RemoteProtocolError("COMPLETION_TRANSPORT_SENTINEL")
        return httpx.Response(
            200,
            json={
                "status": "accepted",
                "queued": True,
                "job_id": "call-finalization:call_1",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AgentApiClient(
            base_url="http://api.test",
            http_client=http_client,
            max_retries=2,
        )
        with caplog.at_level(logging.WARNING):
            await client.complete_call(
                {
                    "call_id": "call_1",
                    "duration_seconds": 1,
                    "dispatch_token": "COMPLETION_TOKEN_SENTINEL",
                }
            )

    assert attempts == 2
    assert "COMPLETION_TRANSPORT_SENTINEL" not in caplog.text
    assert "COMPLETION_TOKEN_SENTINEL" not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response_body",
    [
        {"status": "accepted", "queued": False, "job_id": "call-finalization:call_1"},
        {"status": "success", "queued": True, "job_id": "call-finalization:call_1"},
        {"status": "accepted", "queued": True, "job_id": "wrong"},
        {"status": "accepted", "queued": True},
    ],
)
async def test_complete_call_rejects_malformed_or_mismatched_success(
    response_body: dict,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AgentApiClient(base_url="http://api.test", http_client=http_client)

        with pytest.raises(ValueError, match="acknowledgement"):
            await client.complete_call(
                {
                    "call_id": "call_1",
                    "duration_seconds": 1,
                    "dispatch_token": "dispatch-secret",
                }
            )


@pytest.mark.anyio
async def test_agent_api_client_reuses_and_safely_reopens_owned_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances: list[object] = []

    class OwnedClient:
        def __init__(self, **_kwargs) -> None:
            self.closed = False
            self.requests = 0
            instances.append(self)

        async def post(self, _url: str, **_kwargs) -> httpx.Response:
            self.requests += 1
            return httpx.Response(
                200,
                json={"status": "stored", "sequence_number": self.requests},
            )

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setattr("agent.api_client.httpx.AsyncClient", OwnedClient)
    client = AgentApiClient(base_url="http://api.test")

    for sequence in (1, 2):
        await client.append_transcript(
            "call_1",
            "dispatch-secret",
            CallTranscriptItem(
                sequence_number=sequence,
                speaker="CALLER",
                text=f"segment {sequence}",
            ),
        )

    assert len(instances) == 1
    assert instances[0].requests == 2

    await client.aclose()
    assert instances[0].closed is True

    await client.append_transcript(
        "call_1",
        "dispatch-secret",
        CallTranscriptItem(sequence_number=1, speaker="CALLER", text="retry"),
    )
    assert len(instances) == 2


@pytest.mark.anyio
async def test_agent_api_client_does_not_close_injected_http_client() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"status": "stored", "sequence_number": 1},
            )
        )
    ) as http_client:
        client = AgentApiClient(base_url="http://api.test", http_client=http_client)

        await client.aclose()

        assert http_client.is_closed is False
