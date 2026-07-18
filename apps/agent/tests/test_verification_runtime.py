import asyncio
import json
import importlib
import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

import agent.main as agent_main
import agent.schemas as schemas


VERIFICATION_MESSAGE = (
    "Forwarding test successful. Return to Presvo to go live."
)


def verification_metadata(**overrides: object) -> dict[str, object]:
    session_id = str(overrides.pop("verification_session_id", uuid4()))
    payload: dict[str, object] = {
        "job_type": "forwarding_verification",
        "verification_session_id": session_id,
        "user_id": str(uuid4()),
        "agent_identity": f"agent-verification-{session_id}",
        "completion_token": "verification-token-sentinel",
        "message": VERIFICATION_MESSAGE,
        "tts_provider": "speechmatics",
    }
    payload.update(overrides)
    return payload


def customer_metadata(**overrides: object) -> dict[str, object]:
    call_id = str(overrides.pop("call_id", uuid4()))
    payload: dict[str, object] = {
        "call_id": call_id,
        "user_id": str(uuid4()),
        "agent_config_id": str(uuid4()),
        "agent_identity": f"agent-call-{call_id}",
        "agent_name": "Ava",
        "owner_name": "Sam",
        "owner_context": None,
        "system_prompt": "Be helpful.",
        "knowledge_base": "Open weekdays.",
        "pipeline_mode": "stt_llm_tts",
        "minutes_remaining": 10,
        "allowed_duration_seconds": 600,
        "dispatch_token": "dispatch-token",
    }
    payload.update(overrides)
    return payload


class FakeJobRequest:
    def __init__(self, metadata: dict[str, object]) -> None:
        self.job = SimpleNamespace(metadata=json.dumps(metadata))
        self.accepted: list[dict[str, object]] = []
        self.rejected: list[dict[str, object]] = []

    async def accept(self, **kwargs: object) -> None:
        self.accepted.append(kwargs)

    async def reject(self, **kwargs: object) -> None:
        self.rejected.append(kwargs)


def test_customer_metadata_without_job_type_remains_compatible() -> None:
    metadata = schemas.parse_job_metadata(customer_metadata())

    assert isinstance(metadata, schemas.CustomerCallDispatchMetadata)
    assert metadata.job_type == "customer_call"
    assert schemas.DispatchMetadata is schemas.CustomerCallDispatchMetadata


def test_verification_metadata_is_discriminated_and_forbids_customer_fields() -> None:
    metadata = schemas.parse_job_metadata(verification_metadata())

    assert isinstance(metadata, schemas.ForwardingVerificationDispatchMetadata)
    assert metadata.job_type == "forwarding_verification"

    with pytest.raises(ValidationError):
        schemas.parse_job_metadata(
            verification_metadata(system_prompt="customer-only-secret")
        )


def test_explicit_customer_job_type_is_accepted() -> None:
    metadata = schemas.parse_job_metadata(
        customer_metadata(job_type="customer_call")
    )

    assert isinstance(metadata, schemas.CustomerCallDispatchMetadata)


@pytest.mark.parametrize(
    "field",
    [
        "call_id",
        "agent_config_id",
        "agent_name",
        "owner_name",
        "owner_context",
        "system_prompt",
        "knowledge_base",
        "pipeline_mode",
        "minutes_remaining",
        "allowed_duration_seconds",
        "dispatch_token",
    ],
)
def test_verification_metadata_rejects_every_customer_only_field(
    field: str,
) -> None:
    with pytest.raises(ValidationError):
        schemas.parse_job_metadata(verification_metadata(**{field: "secret"}))


@pytest.mark.parametrize(
    "field",
    [
        "verification_session_id",
        "completion_token",
        "message",
        "tts_provider",
    ],
)
def test_customer_metadata_rejects_every_verification_only_field(
    field: str,
) -> None:
    with pytest.raises(ValidationError):
        schemas.parse_job_metadata(customer_metadata(**{field: "secret"}))


@pytest.mark.parametrize(
    "overrides",
    [
        {"job_type": "unknown"},
        {"verification_session_id": "not-a-uuid"},
        {"user_id": "not-a-uuid"},
        {"completion_token": ""},
        {"completion_token": "   "},
    ],
)
def test_verification_metadata_rejects_invalid_discriminator_ids_and_secret(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        schemas.parse_job_metadata(verification_metadata(**overrides))


@pytest.mark.anyio
async def test_job_request_accepts_exact_verification_identity_with_safe_name() -> None:
    metadata = verification_metadata()
    request = FakeJobRequest(metadata)

    await agent_main.handle_job_request(request)

    assert request.accepted == [
        {
            "name": "Presvo forwarding verification",
            "identity": metadata["agent_identity"],
        }
    ]
    assert request.rejected == []


@pytest.mark.anyio
async def test_job_request_rejects_verification_identity_mismatch_without_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "VERIFICATION_TOKEN_SENTINEL"
    session_id = str(uuid4())
    user_id = str(uuid4())
    request = FakeJobRequest(
        verification_metadata(
            verification_session_id=session_id,
            user_id=user_id,
            agent_identity="mismatched-verification-identity",
            completion_token=token,
        )
    )

    with caplog.at_level(logging.WARNING):
        await agent_main.handle_job_request(request)

    assert request.accepted == []
    assert request.rejected == [{"terminate": True}]
    assert token not in caplog.text
    assert session_id not in caplog.text
    assert user_id not in caplog.text
    assert "mismatched-verification-identity" not in caplog.text


@pytest.mark.anyio
async def test_job_request_rejects_invalid_verification_metadata_without_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    message = "MESSAGE_SENTINEL"
    token = "TOKEN_SENTINEL"
    request = FakeJobRequest(
        verification_metadata(message=message, completion_token=token)
    )

    with caplog.at_level(logging.WARNING):
        await agent_main.handle_job_request(request)

    assert request.accepted == []
    assert request.rejected == [{"terminate": True}]
    assert message not in caplog.text
    assert token not in caplog.text
    assert "literal_error" not in caplog.text


class FakeSpeechHandle:
    def __init__(
        self,
        events: list[object],
        error: BaseException | None = None,
    ) -> None:
        self.events = events
        self.error = error

    def __await__(self):
        async def wait_for_playout() -> None:
            self.events.append("speech_complete")
            if self.error is not None:
                raise self.error

        return wait_for_playout().__await__()


class FakeVerificationSession:
    def __init__(
        self,
        events: list[object],
        speech_error: BaseException | None = None,
        start_error: BaseException | None = None,
        shutdown_error: BaseException | None = None,
    ) -> None:
        self.events = events
        self.speech_error = speech_error
        self.start_error = start_error
        self.shutdown_error = shutdown_error
        self.stt = None
        self.llm = None
        self.start_kwargs: dict[str, object] = {}
        self.audio_states: list[bool] = []
        self.input = SimpleNamespace(set_audio_enabled=self._set_audio_enabled)

    def _set_audio_enabled(self, enabled: bool) -> None:
        self.audio_states.append(enabled)
        self.events.append(("audio", enabled))

    async def start(self, **kwargs: object) -> None:
        self.start_kwargs = kwargs
        self.events.append("start")
        if self.start_error is not None:
            raise self.start_error

    def say(self, text: str, **kwargs: object) -> FakeSpeechHandle:
        self.events.append(("say", text, kwargs))
        return FakeSpeechHandle(self.events, self.speech_error)

    def shutdown(self, **kwargs: object) -> None:
        self.events.append(("shutdown", kwargs))
        if self.shutdown_error is not None:
            raise self.shutdown_error

    def on(self, *_args: object, **_kwargs: object) -> None:
        pytest.fail("verification runtime registered a conversation handler")


class FakeVerificationContext:
    def __init__(self, metadata: dict[str, object] | None = None) -> None:
        self.events: list[object] = []
        self.room = object()
        self.job = SimpleNamespace(metadata=json.dumps(metadata or {}))
        self.shutdown_callbacks: list[object] = []

    async def connect(self, **kwargs: object) -> None:
        self.events.append(("connect", kwargs))

    async def wait_for_participant(self, **kwargs: object) -> object:
        self.events.append(("wait_for_participant", kwargs))
        return SimpleNamespace(identity="sip-caller")

    def add_shutdown_callback(self, callback: object) -> None:
        self.shutdown_callbacks.append(callback)


class FakeVerificationApiClient:
    def __init__(
        self,
        events: list[object],
        completion_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.events = events
        self.completion_error = completion_error
        self.close_error = close_error

    async def complete_verification(self, session_id: str, token: str) -> dict:
        self.events.append(("complete", session_id, token))
        if self.completion_error is not None:
            raise self.completion_error
        return {"status": "verified", "session_id": session_id}

    async def aclose(self) -> None:
        self.events.append("close_api")
        if self.close_error is not None:
            raise self.close_error


def _runtime_module():
    return importlib.import_module("agent.verification_runtime")


@pytest.mark.anyio
async def test_verification_runtime_plays_exact_message_then_completes_and_cleans_up() -> None:
    runtime = _runtime_module()
    metadata = schemas.parse_job_metadata(verification_metadata())
    context = FakeVerificationContext()
    events = context.events
    session = FakeVerificationSession(events)
    api_client = FakeVerificationApiClient(events)
    agent = object()

    await runtime.run_forwarding_verification(
        context,
        metadata,
        session_factory=lambda provider: events.append(
            ("build_session", provider)
        )
        or session,
        agent_factory=lambda: events.append("build_agent") or agent,
        api_client_factory=lambda: events.append("build_api") or api_client,
    )

    assert events == [
        ("connect", {"auto_subscribe": agent_main.AutoSubscribe.SUBSCRIBE_NONE}),
        ("wait_for_participant", {"kind": agent_main.SIP_PARTICIPANT_KIND}),
        ("build_session", "speechmatics"),
        "build_agent",
        "build_api",
        ("audio", False),
        "start",
        (
            "say",
            VERIFICATION_MESSAGE,
            {"allow_interruptions": False},
        ),
        "speech_complete",
        (
            "complete",
            metadata.verification_session_id,
            metadata.completion_token,
        ),
        ("shutdown", {"drain": True}),
        "close_api",
    ]
    assert session.audio_states == [False]
    assert session.start_kwargs["agent"] is agent
    assert session.start_kwargs["room"] is context.room
    assert session.start_kwargs["record"] == {
        "audio": False,
        "transcript": False,
        "traces": False,
        "logs": False,
    }
    room_options = session.start_kwargs["room_options"]
    assert room_options.participant_identity == "sip-caller"
    assert room_options.participant_kinds == [agent_main.SIP_PARTICIPANT_KIND]
    assert room_options.close_on_disconnect is True
    assert room_options.delete_room_on_close is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("speech_error", "completion_error", "expected_error"),
    [
        (RuntimeError("tts failed"), None, RuntimeError),
        (None, RuntimeError("completion failed"), RuntimeError),
    ],
)
async def test_verification_runtime_cleans_up_on_speech_or_completion_failure(
    speech_error: BaseException | None,
    completion_error: BaseException | None,
    expected_error: type[BaseException],
) -> None:
    runtime = _runtime_module()
    metadata = schemas.parse_job_metadata(verification_metadata())
    context = FakeVerificationContext()
    events = context.events
    session = FakeVerificationSession(events, speech_error=speech_error)
    api_client = FakeVerificationApiClient(events, completion_error)

    with pytest.raises(expected_error):
        await runtime.run_forwarding_verification(
            context,
            metadata,
            session_factory=lambda _provider: session,
            agent_factory=object,
            api_client_factory=lambda: api_client,
        )

    assert ("shutdown", {"drain": True}) in events
    assert events[-1] == "close_api"
    if speech_error is not None:
        assert not any(event[0] == "complete" for event in events if isinstance(event, tuple))


@pytest.mark.anyio
async def test_verification_runtime_preserves_cancellation_and_cleans_up() -> None:
    runtime = _runtime_module()
    metadata = schemas.parse_job_metadata(verification_metadata())
    context = FakeVerificationContext()
    events = context.events
    session = FakeVerificationSession(events, asyncio.CancelledError())
    api_client = FakeVerificationApiClient(events)

    with pytest.raises(asyncio.CancelledError):
        await runtime.run_forwarding_verification(
            context,
            metadata,
            session_factory=lambda _provider: session,
            agent_factory=object,
            api_client_factory=lambda: api_client,
        )

    assert not any(event[0] == "complete" for event in events if isinstance(event, tuple))
    assert events[-2:] == [("shutdown", {"drain": True}), "close_api"]


@pytest.mark.anyio
async def test_verification_runtime_start_failure_still_drains_and_closes() -> None:
    runtime = _runtime_module()
    metadata = schemas.parse_job_metadata(verification_metadata())
    context = FakeVerificationContext()
    events = context.events
    session = FakeVerificationSession(
        events,
        start_error=RuntimeError("start failed"),
    )
    api_client = FakeVerificationApiClient(events)

    with pytest.raises(RuntimeError, match="start failed"):
        await runtime.run_forwarding_verification(
            context,
            metadata,
            session_factory=lambda _provider: session,
            agent_factory=object,
            api_client_factory=lambda: api_client,
        )

    assert events[-2:] == [("shutdown", {"drain": True}), "close_api"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "primary_error",
    [RuntimeError("speech primary failure"), asyncio.CancelledError()],
)
async def test_verification_runtime_cleanup_errors_do_not_mask_primary_failure(
    primary_error: BaseException,
) -> None:
    runtime = _runtime_module()
    metadata = schemas.parse_job_metadata(verification_metadata())
    context = FakeVerificationContext()
    events = context.events
    session = FakeVerificationSession(
        events,
        speech_error=primary_error,
        shutdown_error=RuntimeError("SHUTDOWN_ERROR_SENTINEL"),
    )
    api_client = FakeVerificationApiClient(
        events,
        close_error=RuntimeError("CLOSE_ERROR_SENTINEL"),
    )

    with pytest.raises(type(primary_error)) as caught:
        await runtime.run_forwarding_verification(
            context,
            metadata,
            session_factory=lambda _provider: session,
            agent_factory=object,
            api_client_factory=lambda: api_client,
        )

    if isinstance(primary_error, RuntimeError):
        assert str(caught.value) == "speech primary failure"
    assert events[-2:] == [("shutdown", {"drain": True}), "close_api"]


@pytest.mark.anyio
async def test_verification_runtime_preserves_cleanup_cancellation_after_all_cleanup() -> None:
    runtime = _runtime_module()
    metadata = schemas.parse_job_metadata(verification_metadata())
    context = FakeVerificationContext()
    events = context.events
    session = FakeVerificationSession(
        events,
        shutdown_error=asyncio.CancelledError(),
    )
    api_client = FakeVerificationApiClient(events)

    with pytest.raises(asyncio.CancelledError):
        await runtime.run_forwarding_verification(
            context,
            metadata,
            session_factory=lambda _provider: session,
            agent_factory=object,
            api_client_factory=lambda: api_client,
        )

    assert events[-2:] == [("shutdown", {"drain": True}), "close_api"]


@pytest.mark.anyio
async def test_verification_runtime_does_not_close_injected_api_client() -> None:
    runtime = _runtime_module()
    metadata = schemas.parse_job_metadata(verification_metadata())
    context = FakeVerificationContext()
    events = context.events
    session = FakeVerificationSession(events)
    api_client = FakeVerificationApiClient(events)

    await runtime.run_forwarding_verification(
        context,
        metadata,
        session_factory=lambda _provider: session,
        agent_factory=object,
        api_client=api_client,
        api_client_factory=lambda: pytest.fail(
            "injected client unexpectedly replaced"
        ),
    )

    assert "close_api" not in events


@pytest.mark.anyio
async def test_entrypoint_branches_to_verification_before_normal_call_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = verification_metadata()
    metadata = schemas.parse_job_metadata(payload)
    context = FakeVerificationContext(payload)
    calls: list[object] = []

    async def capture_verification(resolved_context, resolved_metadata) -> None:
        calls.append((resolved_context, resolved_metadata))

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("verification entered the normal customer-call path")

    parse_calls = 0

    def capture_parse(value: object):
        nonlocal parse_calls
        parse_calls += 1
        return schemas.parse_job_metadata(value)

    monkeypatch.setattr(agent_main, "run_forwarding_verification", capture_verification)
    monkeypatch.setattr(agent_main, "parse_job_metadata", capture_parse)
    for name in [
        "build_agent_runtime",
        "SessionRuntime",
        "EventPublisher",
        "agent_lifecycle_span",
        "agent_provider_span",
        "_send_initial_greeting",
    ]:
        monkeypatch.setattr(agent_main, name, forbidden)

    await agent_main.entrypoint(context)

    assert parse_calls == 1
    assert calls == [(context, metadata)]
    assert len(context.shutdown_callbacks) == 1
