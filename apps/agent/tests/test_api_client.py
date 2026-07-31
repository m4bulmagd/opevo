import json
import logging
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError
from presvo_contracts import (
    CallCompletionAcknowledgement,
    CallCompletionRequest,
    TranscriptAppendAcknowledgement,
    TranscriptAppendRequest,
    TranscriptSegment,
    VerificationCompletionAcknowledgement,
    VerificationCompletionRequest,
    create_contract,
    parse_contract,
)

import agent.api_client as api_client_module
from agent.api_client import (
    AgentApiClient,
    CallCompletionAcknowledgementError,
    CallCompletionRetryableError,
    TranscriptAppendPermanentError,
    TranscriptAppendRetryableError,
    VerificationCompletionAcknowledgementError,
    VerificationCompletionPermanentError,
    VerificationCompletionRetryableError,
)


FIXTURES = Path(__file__).parents[3] / "libs/shared/tests/fixtures/v1"
FIXTURE_CALL_ID = UUID("11111111-1111-4111-8111-111111111111")
RETRYABLE_HTTP_STATUSES = (408, 425, 429, *range(500, 600))


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _segment(sequence_number: int = 1) -> TranscriptSegment:
    return TranscriptSegment(
        sequence_number=sequence_number, speaker="CALLER", text="private transcript"
    )


def _call_request() -> CallCompletionRequest:
    return create_contract(CallCompletionRequest, duration_seconds=1, transcript=())


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("operation", "expected_error"),
    [
        ("append", TranscriptAppendPermanentError),
        ("call", ValueError),
        ("verification", VerificationCompletionPermanentError),
    ],
)
async def test_client_rejects_missing_credentials_before_http(
    operation: str,
    expected_error: type[Exception],
) -> None:
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AgentApiClient(base_url="http://api.test", http_client=http)
        with pytest.raises(expected_error):
            if operation == "append":
                await client.append_transcript(uuid4(), "", _segment())
            elif operation == "call":
                await client.complete_call(uuid4(), "", _call_request())
            else:
                await client.complete_verification(uuid4(), " ")

    assert requests == 0


@pytest.mark.anyio
async def test_zero_verification_retries_fails_without_http_attempt() -> None:
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AgentApiClient(
            base_url="http://api.test",
            http_client=http,
            max_retries=0,
        )
        with pytest.raises(VerificationCompletionRetryableError):
            await client.complete_verification(uuid4(), "token")

    assert requests == 0


@pytest.mark.anyio
async def test_client_matches_transcript_golden_request_and_acknowledgement() -> None:
    request_fixture = _fixture("transcript_append_request.json")
    acknowledgement_fixture = _fixture("transcript_append_acknowledgement.json")
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=acknowledgement_fixture)

    request = parse_contract(TranscriptAppendRequest, request_fixture)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        acknowledgement = await AgentApiClient(
            base_url="http://api.test", http_client=http
        ).append_transcript(FIXTURE_CALL_ID, "token", request.segment)

    assert captured == [request_fixture]
    assert acknowledgement == parse_contract(
        TranscriptAppendAcknowledgement, acknowledgement_fixture
    )


@pytest.mark.anyio
async def test_client_matches_call_completion_golden_request_and_acknowledgement() -> None:
    request_fixture = _fixture("call_completion_request.json")
    acknowledgement_fixture = _fixture("call_completion_acknowledgement.json")
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(202, json=acknowledgement_fixture)

    request = parse_contract(CallCompletionRequest, request_fixture)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        acknowledgement = await AgentApiClient(
            base_url="http://api.test", http_client=http
        ).complete_call(FIXTURE_CALL_ID, "token", request)

    assert captured == [request_fixture]
    assert acknowledgement == parse_contract(
        CallCompletionAcknowledgement, acknowledgement_fixture
    )


@pytest.mark.anyio
async def test_client_matches_verification_golden_request_and_acknowledgement() -> None:
    request_fixture = _fixture("verification_completion_request.json")
    acknowledgement_fixture = _fixture(
        "verification_completion_acknowledgement.json"
    )
    session_id = UUID(acknowledgement_fixture["session_id"])
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=acknowledgement_fixture)

    parse_contract(VerificationCompletionRequest, request_fixture)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        acknowledgement = await AgentApiClient(
            base_url="http://api.test", http_client=http
        ).complete_verification(session_id, "token")

    assert captured == [request_fixture]
    assert acknowledgement == parse_contract(
        VerificationCompletionAcknowledgement, acknowledgement_fixture
    )


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
@pytest.mark.parametrize("status_code", RETRYABLE_HTTP_STATUSES)
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
    combined = str(caught.value) + str(caught.value.__context__)
    assert "TOKEN_SENTINEL" not in combined
    assert "private transcript" not in combined
    assert body.decode() not in combined


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status_code",
    [code for code in range(400, 500) if code not in {408, 425, 429}],
)
async def test_append_transcript_classifies_every_permanent_4xx(
    status_code: int,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status_code))
    ) as http:
        with pytest.raises(TranscriptAppendPermanentError):
            await AgentApiClient(
                base_url="http://api.test", http_client=http
            ).append_transcript(uuid4(), "token", _segment())


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
@pytest.mark.parametrize("status_code", RETRYABLE_HTTP_STATUSES)
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
@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b'["valid-json-non-object"]',
        b'{"status":"accepted","queued":true,"job_id":"missing-version"}',
        b'{"schema_version":2,"status":"accepted","queued":true,"job_id":"unsupported"}',
    ],
)
async def test_complete_call_rejects_each_unsafe_acknowledgement_shape(
    body: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(202, content=body)
        )
    ) as http:
        with caplog.at_level(logging.WARNING), pytest.raises(
            CallCompletionAcknowledgementError
        ) as caught:
            await AgentApiClient(
                base_url="http://api.test/PATH_SENTINEL", http_client=http
            ).complete_call(FIXTURE_CALL_ID, "TOKEN_SENTINEL", _call_request())

    assert caught.value.__cause__ is None
    combined = (
        caplog.text
        + str(caught.value)
        + str(caught.value.__context__)
    )
    assert "TOKEN_SENTINEL" not in combined
    assert "PATH_SENTINEL" not in combined
    assert body.decode() not in combined


@pytest.mark.anyio
async def test_complete_call_accepts_additive_acknowledgement_fields() -> None:
    acknowledgement_fixture = _fixture("call_completion_acknowledgement.json")
    acknowledgement_fixture["future"] = {"additive": True}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(202, json=acknowledgement_fixture)
        )
    ) as http:
        acknowledgement = await AgentApiClient(
            base_url="http://api.test", http_client=http
        ).complete_call(FIXTURE_CALL_ID, "token", _call_request())

    assert acknowledgement.job_id == f"call-finalization:{FIXTURE_CALL_ID}"


@pytest.mark.anyio
async def test_complete_call_sends_exactly_two_thousand_recovery_segments() -> None:
    segments = tuple(
        TranscriptSegment(
            sequence_number=sequence,
            speaker="CALLER",
            text=f"recovery {sequence}",
        )
        for sequence in range(1, 2_001)
    )
    request = create_contract(
        CallCompletionRequest,
        duration_seconds=42,
        transcript=segments,
    )
    captured: list[dict] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(http_request.content))
        return httpx.Response(
            202, json=_fixture("call_completion_acknowledgement.json")
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http:
        await AgentApiClient(
            base_url="http://api.test", http_client=http
        ).complete_call(FIXTURE_CALL_ID, "token", request)

    assert len(captured[0]["transcript"]) == 2_000
    assert captured[0]["transcript"][0]["sequence_number"] == 1
    assert captured[0]["transcript"][-1]["sequence_number"] == 2_000


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
@pytest.mark.parametrize("status_code", RETRYABLE_HTTP_STATUSES)
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
async def test_verification_rejects_session_mismatch() -> None:
    session_id = uuid4()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "schema_version": 1,
                    "status": "verified",
                    "session_id": str(uuid4()),
                },
            )
        )
    ) as http:
        with pytest.raises(VerificationCompletionAcknowledgementError):
            await AgentApiClient(
                base_url="http://api.test", http_client=http
            ).complete_verification(session_id, "token")


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["call", "verification"])
async def test_completion_transport_retry_then_success(
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    call_id = FIXTURE_CALL_ID
    session_id = UUID("44444444-4444-4444-8444-444444444444")

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(api_client_module.asyncio, "sleep", no_sleep)

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("TRANSPORT_RETRY_SENTINEL")
        if operation == "call":
            return httpx.Response(
                202, json=_fixture("call_completion_acknowledgement.json")
            )
        return httpx.Response(
            200, json=_fixture("verification_completion_acknowledgement.json")
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http:
        client = AgentApiClient(
            base_url="http://api.test", http_client=http, max_retries=2
        )
        if operation == "call":
            await client.complete_call(call_id, "TOKEN_SENTINEL", _call_request())
        else:
            await client.complete_verification(session_id, "TOKEN_SENTINEL")

    assert attempts == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("operation", "error_type"),
    [
        ("call", CallCompletionRetryableError),
        ("verification", VerificationCompletionRetryableError),
    ],
)
async def test_completion_transport_exhaustion_is_safe(
    operation: str,
    error_type: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(api_client_module.asyncio, "sleep", no_sleep)

    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("TRANSPORT_EXHAUSTION_SENTINEL")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http:
        client = AgentApiClient(
            base_url="http://api.test/PATH_SENTINEL",
            http_client=http,
            max_retries=2,
        )
        with caplog.at_level(logging.WARNING), pytest.raises(error_type) as caught:
            if operation == "call":
                await client.complete_call(
                    FIXTURE_CALL_ID, "TOKEN_SENTINEL", _call_request()
                )
            else:
                await client.complete_verification(
                    UUID("44444444-4444-4444-8444-444444444444"),
                    "TOKEN_SENTINEL",
                )

    combined = caplog.text + str(caught.value)
    for sentinel in (
        "TRANSPORT_EXHAUSTION_SENTINEL",
        "PATH_SENTINEL",
        "TOKEN_SENTINEL",
    ):
        assert sentinel not in combined


@pytest.mark.anyio
async def test_owned_http_client_is_reused_closed_and_reopened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances: list[object] = []

    class OwnedClient:
        def __init__(self, **_kwargs) -> None:
            self.closed = False
            instances.append(self)

        async def post(self, _url: str, **kwargs) -> httpx.Response:
            sequence = kwargs["json"]["segment"]["sequence_number"]
            return httpx.Response(
                200,
                json={
                    "schema_version": 1,
                    "status": "stored",
                    "sequence_number": sequence,
                },
            )

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setattr(api_client_module.httpx, "AsyncClient", OwnedClient)
    client = AgentApiClient(base_url="http://api.test")

    await client.append_transcript(FIXTURE_CALL_ID, "token", _segment(1))
    await client.append_transcript(FIXTURE_CALL_ID, "token", _segment(2))
    assert len(instances) == 1

    await client.aclose()
    assert instances[0].closed is True
    await client.append_transcript(FIXTURE_CALL_ID, "token", _segment(3))
    assert len(instances) == 2


@pytest.mark.anyio
async def test_injected_http_client_is_not_closed() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "schema_version": 1,
                    "status": "stored",
                    "sequence_number": 1,
                },
            )
        )
    ) as http:
        client = AgentApiClient(base_url="http://api.test", http_client=http)
        await client.aclose()
        assert http.is_closed is False


@pytest.mark.anyio
async def test_verification_malformed_acknowledgement_has_no_exception_chain_or_secret_logs(caplog: pytest.LogCaptureFixture) -> None:
    session_id = uuid4()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b'{"schema_version":1,"status":"verified","session_id":"not-a-uuid","future":"ACK_BODY_SENTINEL"}'))) as http:
        with caplog.at_level(logging.WARNING), pytest.raises(VerificationCompletionAcknowledgementError) as caught:
            await AgentApiClient(base_url="http://api.test", http_client=http).complete_verification(session_id, "TOKEN_SENTINEL")
    assert caught.value.__cause__ is None
    combined = caplog.text + str(caught.value) + str(caught.value.__context__)
    assert "TOKEN_SENTINEL" not in combined
    assert "ACK_BODY_SENTINEL" not in combined


def test_transcript_segment_remains_strict_and_normalizes_text() -> None:
    assert _segment().text == "private transcript"
    with pytest.raises(ValidationError):
        TranscriptSegment(sequence_number=0, speaker="CALLER", text="x")
