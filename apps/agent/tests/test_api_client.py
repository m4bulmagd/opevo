import json
import logging
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from presvo_contracts import (
    CallCompletionRequest,
    TranscriptSegment,
    create_contract,
)

import agent.api_client as api_client_module
from agent.api_client import (
    AgentApiClient,
    CallCompletionAcknowledgementError,
    TranscriptAppendPermanentError,
    TranscriptAppendRetryableError,
    VerificationCompletionAcknowledgementError,
    VerificationCompletionPermanentError,
)


def _segment(sequence_number: int = 1) -> TranscriptSegment:
    return TranscriptSegment(
        sequence_number=sequence_number, speaker="CALLER", text="private transcript"
    )


def _call_request() -> CallCompletionRequest:
    return create_contract(CallCompletionRequest, duration_seconds=1, transcript=())


@pytest.mark.anyio
async def test_append_transcript_sends_versioned_nested_contract_and_parses_additive_ack() -> None:
    call_id = uuid4()
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"schema_version": 1, "status": "stored", "sequence_number": 1, "future": True},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        acknowledgement = await AgentApiClient(base_url="http://api.test", http_client=http).append_transcript(
            call_id, "dispatch-token", _segment()
        )

    assert acknowledgement.status == "stored"
    assert json.loads(captured[0].content) == {
        "schema_version": 1,
        "segment": {"sequence_number": 1, "speaker": "CALLER", "text": "private transcript"},
    }
    assert captured[0].headers["x-agent-token"] == "dispatch-token"


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [408, 425, 429, 500, 599])
async def test_append_transcript_classifies_retryable_statuses(status_code: int) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status_code))) as http:
        with pytest.raises(TranscriptAppendRetryableError):
            await AgentApiClient(base_url="http://api.test", http_client=http).append_transcript(
                uuid4(), "token", _segment()
            )


@pytest.mark.anyio
@pytest.mark.parametrize("body", [b"not-json", b"[]", b'{"schema_version":2}', b'{"schema_version":1,"status":"stored","sequence_number":2}'])
async def test_append_transcript_rejects_malformed_or_mismatched_acknowledgement(body: bytes) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))) as http:
        with pytest.raises(TranscriptAppendPermanentError) as caught:
            await AgentApiClient(base_url="http://api.test", http_client=http).append_transcript(uuid4(), "TOKEN_SENTINEL", _segment())
    assert "TOKEN_SENTINEL" not in str(caught.value)


@pytest.mark.anyio
async def test_complete_call_sends_credentials_only_in_header_and_correlates_job() -> None:
    call_id = uuid4()
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(202, json={"schema_version": 1, "status": "accepted", "queued": True, "job_id": f"call-finalization:{call_id}"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        acknowledgement = await AgentApiClient(base_url="http://api.test", http_client=http).complete_call(
            call_id, "DISPATCH_SENTINEL", _call_request()
        )

    assert acknowledgement.job_id == f"call-finalization:{call_id}"
    assert json.loads(captured[0].content) == {"schema_version": 1, "duration_seconds": 1, "transcript": []}
    assert captured[0].headers["x-agent-token"] == "DISPATCH_SENTINEL"
    assert "DISPATCH_SENTINEL" not in captured[0].content.decode()


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [408, 425, 429, 500, 503])
async def test_complete_call_retries_every_retryable_status(status_code: int, monkeypatch: pytest.MonkeyPatch) -> None:
    call_id = uuid4()
    attempts = 0

    async def no_sleep(_: float) -> None: pass
    monkeypatch.setattr(api_client_module.asyncio, "sleep", no_sleep)

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(status_code, text="RESPONSE_SENTINEL")
        return httpx.Response(202, json={"schema_version": 1, "status": "accepted", "queued": True, "job_id": f"call-finalization:{call_id}"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await AgentApiClient(base_url="http://api.test", http_client=http, max_retries=2).complete_call(call_id, "token", _call_request())
    assert attempts == 2


@pytest.mark.anyio
async def test_complete_call_rejects_bad_acknowledgement_as_safe_permanent_error() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(202, content=b'{"schema_version":1,"status":"accepted","queued":true,"job_id":"wrong"}'))) as http:
        with pytest.raises(CallCompletionAcknowledgementError):
            await AgentApiClient(base_url="http://api.test", http_client=http).complete_call(uuid4(), "token", _call_request())


@pytest.mark.anyio
async def test_complete_verification_sends_version_only_body_and_correlates_session() -> None:
    session_id = uuid4()
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"schema_version": 1, "status": "verified", "session_id": str(session_id), "extra": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        acknowledgement = await AgentApiClient(base_url="http://api.test", http_client=http).complete_verification(session_id, "verification-token")

    assert acknowledgement.session_id == session_id
    assert json.loads(captured[0].content) == {"schema_version": 1}
    assert captured[0].headers["x-verification-token"] == "verification-token"


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 409, 422])
async def test_completion_4xx_is_permanent_without_retry(status_code: int) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status_code))) as http:
        client = AgentApiClient(base_url="http://api.test", http_client=http)
        with pytest.raises(VerificationCompletionPermanentError):
            await client.complete_verification(uuid4(), "token")


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [500, 502, 599])
async def test_verification_retries_every_server_error(status_code: int, monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = uuid4()
    attempts = 0

    async def no_sleep(_: float) -> None: pass
    monkeypatch.setattr(api_client_module.asyncio, "sleep", no_sleep)

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(status_code, text="RESPONSE_SENTINEL")
        return httpx.Response(200, json={"schema_version": 1, "status": "verified", "session_id": str(session_id)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await AgentApiClient(base_url="http://api.test", http_client=http, max_retries=2).complete_verification(session_id, "token")
    assert attempts == 2


@pytest.mark.anyio
async def test_completion_transport_failures_are_retryable_without_body_or_token_leaks() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: (_ for _ in ()).throw(httpx.ReadTimeout("TRANSPORT_SENTINEL")))) as http:
        with pytest.raises(TranscriptAppendRetryableError) as caught:
            await AgentApiClient(base_url="http://api.test", http_client=http).append_transcript(uuid4(), "TOKEN_SENTINEL", _segment())
    assert "TRANSPORT_SENTINEL" not in str(caught.value)
    assert "TOKEN_SENTINEL" not in str(caught.value)


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 409, 422])
async def test_call_completion_4xx_is_permanent_without_retry(status_code: int) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status_code))) as http:
        with pytest.raises(ValueError):
            await AgentApiClient(base_url="http://api.test", http_client=http).complete_call(uuid4(), "token", _call_request())


@pytest.mark.anyio
async def test_verification_malformed_acknowledgement_has_no_exception_chain_or_secret_logs(caplog: pytest.LogCaptureFixture) -> None:
    session_id = uuid4()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b'{"schema_version":1,"status":"verified","session_id":"not-a-uuid"}'))) as http:
        with caplog.at_level(logging.WARNING), pytest.raises(VerificationCompletionAcknowledgementError) as caught:
            await AgentApiClient(base_url="http://api.test", http_client=http).complete_verification(session_id, "TOKEN_SENTINEL")
    assert caught.value.__cause__ is None
    assert "TOKEN_SENTINEL" not in caplog.text


def test_transcript_segment_remains_strict_and_normalizes_text() -> None:
    assert _segment().text == "private transcript"
    with pytest.raises(ValidationError):
        TranscriptSegment(sequence_number=0, speaker="CALLER", text="x")
