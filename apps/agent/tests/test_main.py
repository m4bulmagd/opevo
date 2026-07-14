import asyncio
import json
import logging
import sys
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
from livekit.agents import JobExecutorType
from pydantic import ValidationError

import agent.main as agent_main
from agent.main import build_worker_options
from agent.main import entrypoint
from agent.main import _safe_task
from agent.main import _disconnect_at_call_limit
from agent.main import _send_initial_greeting
from agent.main import _play_call_limit_message
from agent.main import _register_standard_session_handlers
from agent.main import _register_sts_session_handlers
from agent.main import prewarm_assets
from agent.schemas import DispatchMetadata
from pathlib import Path


def make_metadata(**overrides) -> DispatchMetadata:
    call_id = overrides.pop("call_id", str(uuid4()))
    defaults = {
        "call_id": call_id,
        "user_id": str(uuid4()),
        "agent_config_id": str(uuid4()),
        "agent_identity": f"agent-call-{call_id}",
        "agent_name": "Agent",
        "owner_name": "Owner",
        "owner_context": None,
        "system_prompt": "Be helpful.",
        "knowledge_base": "Open weekdays.",
        "pipeline_mode": "stt_llm_tts",
        "minutes_remaining": 10,
        "allowed_duration_seconds": 600,
        "dispatch_token": "dispatch-token",
    }
    defaults.update(overrides)
    return DispatchMetadata(**defaults)


def test_build_worker_options_sets_prewarm_hook() -> None:
    options = build_worker_options()

    assert options.prewarm_fnc is not None
    assert options.prewarm_fnc.__name__ == "prewarm_assets"
    assert options.job_executor_type is JobExecutorType.PROCESS


def test_build_worker_options_registers_job_request_handler() -> None:
    options = build_worker_options()

    assert options.request_fnc is agent_main.handle_job_request


def test_build_worker_options_registers_inference_runners(monkeypatch) -> None:
    called = []

    monkeypatch.setattr("agent.main._register_inference_runners", lambda: called.append(True))

    build_worker_options()

    assert called == [True]


def test_agent_env_example_documents_debug_stream_flag() -> None:
    env_example = (
        Path(__file__).resolve().parents[1] / ".env.example"
    ).read_text()

    assert "AGENT_DEBUG_STREAMS=false" in env_example
    assert "AGENT_MIN_ENDPOINTING_DELAY=0.25" in env_example
    assert "AGENT_MAX_ENDPOINTING_DELAY=1.5" in env_example
    assert "LIVEKIT_SILERO_VAD_ENABLED=true" in env_example
    assert "LIVEKIT_TURN_DETECTOR_ENABLED=true" in env_example
    assert "OTEL_SERVICE_NAME=presvo-agent" in env_example
    assert "OTEL_EXPORTER_OTLP_ENDPOINT=" in env_example
    assert "OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf" in env_example


class FakeSession:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def on(self, event_name: str, handler) -> None:
        self.handlers[event_name] = handler


class FakeRuntime:
    def __init__(self) -> None:
        self.caller_text: list[str] = []
        self.agent_text: list[str] = []

    async def handle_caller_transcript(self, _metadata: DispatchMetadata, text: str) -> None:
        self.caller_text.append(text)

    async def handle_agent_utterance(self, _metadata: DispatchMetadata, text: str) -> None:
        self.agent_text.append(text)

    def create_handler_task(self, factory) -> bool:
        asyncio.run(factory())
        return True


class FakeTranscriptEvent:
    def __init__(self, transcript: str, *, is_final: bool) -> None:
        self.transcript = transcript
        self.is_final = is_final


class FakeConversationItem:
    def __init__(self, role: str, text: str) -> None:
        self.type = "message"
        self.role = role
        self.text_content = text


class FakeConversationEvent:
    def __init__(self, role: str, text: str) -> None:
        self.item = FakeConversationItem(role, text)


class FakeGreetingSession:
    def __init__(self) -> None:
        self.say_calls: list[str] = []
        self.say_kwargs: list[dict] = []
        self.generate_reply_calls: list[dict] = []

    async def say(self, text: str, **kwargs):
        self.say_calls.append(text)
        self.say_kwargs.append(kwargs)

    async def generate_reply(self, **kwargs):
        self.generate_reply_calls.append(kwargs)


def test_register_standard_session_handlers_forwards_final_caller_and_agent_text() -> None:
    session = FakeSession()
    runtime = FakeRuntime()
    metadata = make_metadata(call_id="call-1", user_id="user-1")

    _register_standard_session_handlers(session, runtime, metadata)

    session.handlers["user_input_transcribed"](FakeTranscriptEvent("Hello", is_final=True))
    session.handlers["conversation_item_added"](FakeConversationEvent("assistant", "Hi there"))

    assert runtime.caller_text == ["Hello"]
    assert runtime.agent_text == ["Hi there"]


def test_register_sts_session_handlers_forwards_caller_and_agent_text() -> None:
    session = FakeSession()
    runtime = FakeRuntime()
    metadata = make_metadata(call_id="call-1", user_id="user-1")

    _register_sts_session_handlers(session, runtime, metadata)

    session.handlers["conversation_item_added"](FakeConversationEvent("user", "Need help"))
    session.handlers["conversation_item_added"](FakeConversationEvent("assistant", "Sure"))

    assert runtime.caller_text == ["Need help"]
    assert runtime.agent_text == ["Sure"]


def test_send_initial_greeting_uses_say_for_standard_mode() -> None:
    session = FakeGreetingSession()

    asyncio.run(
        _send_initial_greeting(
            session,
            make_metadata(
                call_id="test",
                user_id="test",
                agent_name="Assistant",
                owner_name="Sam",
                pipeline_mode="stt_llm_tts",
            ),
        )
    )

    assert session.say_calls == [
        "Hello, I'm Assistant, an AI assistant representing Sam. This call may be recorded. How can I help you?"
    ]
    assert session.generate_reply_calls == []


def test_send_initial_greeting_uses_generate_reply_for_sts_mode() -> None:
    session = FakeGreetingSession()

    asyncio.run(
        _send_initial_greeting(
            session,
            make_metadata(
                call_id="test",
                user_id="test",
                agent_name="Assistant",
                owner_name="Sam",
                pipeline_mode="sts",
            ),
        )
    )

    assert session.say_calls == []
    assert len(session.generate_reply_calls) == 1
    assert "Hello, I'm Assistant, an AI assistant representing Sam." in session.generate_reply_calls[0]["instructions"]


def test_call_limit_message_uses_generate_reply_for_sts_mode() -> None:
    session = FakeGreetingSession()
    message = "Attention, il vous reste une minute avant la fin de cet appel."

    asyncio.run(
        _play_call_limit_message(
            session,
            make_metadata(pipeline_mode="sts"),
            message,
        )
    )

    assert session.say_calls == []
    assert session.generate_reply_calls == [
        {
            "instructions": f'Say exactly in French: "{message}"',
            "allow_interruptions": False,
        }
    ]


def test_call_limit_message_disables_interruptions_for_standard_mode() -> None:
    session = FakeGreetingSession()
    message = "Attention, il vous reste une minute avant la fin de cet appel."

    asyncio.run(
        _play_call_limit_message(
            session,
            make_metadata(pipeline_mode="stt_llm_tts"),
            message,
        )
    )

    assert session.say_calls == [message]
    assert session.say_kwargs == [{"allow_interruptions": False}]


def test_background_task_failure_does_not_render_exception_message(caplog) -> None:
    async def fail_background_task() -> None:
        raise RuntimeError("BACKGROUND_EVENT_TRANSCRIPT_SENTINEL")

    with caplog.at_level(logging.ERROR):
        asyncio.run(_safe_task(fail_background_task()))

    assert "BACKGROUND_EVENT_TRANSCRIPT_SENTINEL" not in caplog.text
    assert "event=background_event_handler_failed" in caplog.text
    assert "operation=run_background_event_handler" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


class FakeJobRequest:
    def __init__(self, metadata: str) -> None:
        self.job = SimpleNamespace(metadata=metadata)
        self.accepted: list[dict] = []
        self.rejected: list[dict] = []

    async def accept(self, **kwargs) -> None:
        self.accepted.append(kwargs)

    async def reject(self, **kwargs) -> None:
        self.rejected.append(kwargs)


@pytest.mark.anyio
async def test_job_request_accepts_exact_deterministic_identity() -> None:
    metadata = make_metadata()
    request = FakeJobRequest(metadata.model_dump_json())

    await agent_main.handle_job_request(request)

    assert request.accepted == [
        {"name": metadata.agent_name, "identity": metadata.agent_identity}
    ]
    assert request.rejected == []


@pytest.mark.anyio
async def test_job_request_rejects_mismatched_identity_without_logging_metadata(
    caplog,
) -> None:
    metadata = make_metadata(agent_identity="metadata-secret-sentinel")
    request = FakeJobRequest(metadata.model_dump_json())

    with caplog.at_level(logging.WARNING):
        await agent_main.handle_job_request(request)

    assert request.accepted == []
    assert request.rejected == [{"terminate": True}]
    assert "metadata-secret-sentinel" not in caplog.text
    assert metadata.dispatch_token not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize("raw_metadata", ["{", "null", "[]", "{}"])
async def test_job_request_rejects_malformed_metadata(raw_metadata: str) -> None:
    request = FakeJobRequest(raw_metadata)

    await agent_main.handle_job_request(request)

    assert request.accepted == []
    assert request.rejected == [{"terminate": True}]


def test_dispatch_metadata_forbids_extra_fields() -> None:
    payload = make_metadata().model_dump()
    payload["caller_number"] = "+33123456789"

    with pytest.raises(ValidationError):
        DispatchMetadata.model_validate(payload)


class FakeJobContext:
    def __init__(self, metadata: DispatchMetadata) -> None:
        self.job = SimpleNamespace(metadata=metadata.model_dump_json())
        self.proc = SimpleNamespace(userdata={})
        self.inference_executor = object()
        self.room = object()
        self.events: list[object] = []
        self.shutdown_callbacks: list[object] = []
        self.shutdown_reasons: list[str] = []

    async def connect(self, **_kwargs) -> None:
        self.events.append("connect")

    async def wait_for_participant(self, *, kind) -> object:
        self.events.append(("wait_for_participant", kind))
        return SimpleNamespace(identity="sip-caller")

    def add_shutdown_callback(self, callback) -> None:
        self.shutdown_callbacks.append(callback)

    def shutdown(self, reason: str = "") -> None:
        self.shutdown_reasons.append(reason)


class FakeEntrypointSession(FakeSession):
    def __init__(self) -> None:
        super().__init__()
        self.started = False
        self.start_kwargs: dict = {}
        self.say_calls: list[str] = []
        self.shutdown_calls: list[dict] = []
        self.events: list[tuple[str, object]] = []
        self.input = SimpleNamespace(set_audio_enabled=self._set_audio_enabled)

    async def start(self, **kwargs) -> None:
        self.started = True
        self.start_kwargs = kwargs
        self.events.append(("start", None))

    async def say(self, text: str, **kwargs) -> None:
        self.say_calls.append(text)
        self.events.append(("say", {"text": text, **kwargs}))

    async def interrupt(self, **kwargs) -> None:
        self.events.append(("interrupt", kwargs))

    def _set_audio_enabled(self, enabled: bool) -> None:
        self.events.append(("set_audio_enabled", enabled))

    def shutdown(self, **kwargs) -> None:
        self.shutdown_calls.append(kwargs)
        self.events.append(("shutdown", kwargs))


@pytest.mark.anyio
async def test_expiry_interrupts_input_and_speech_before_noninterruptible_close() -> None:
    session = FakeEntrypointSession()
    metadata = make_metadata()

    await _disconnect_at_call_limit(session, metadata)

    assert session.events == [
        ("interrupt", {"force": True}),
        ("set_audio_enabled", False),
        (
            "say",
            {
                "text": "La durée maximale de cet appel est atteinte. Merci de votre appel. Au revoir.",
                "allow_interruptions": False,
            },
        ),
        ("shutdown", {"drain": True}),
    ]


@pytest.mark.anyio
async def test_entrypoint_connects_then_waits_only_for_sip_participant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = make_metadata()
    context = FakeJobContext(metadata)
    session = FakeEntrypointSession()
    initialize_calls: list[bool] = []
    monkeypatch.setattr(
        agent_main,
        "initialize_observability",
        lambda: initialize_calls.append(True),
        raising=False,
    )
    monkeypatch.setattr(
        "agent.main.build_agent_runtime",
        lambda *_args, **_kwargs: (object(), session),
    )

    await entrypoint(context)

    assert context.events == ["connect", ("wait_for_participant", 3)]
    assert initialize_calls == [True]
    assert session.started is True
    assert session.start_kwargs["record"] == {
        "audio": False,
        "transcript": False,
        "traces": False,
        "logs": False,
    }
    assert session.start_kwargs["room_options"].participant_identity == "sip-caller"
    assert session.start_kwargs["room_options"].participant_kinds == [3]
    assert session.start_kwargs["room_options"].close_on_disconnect is True
    assert session.start_kwargs["room_options"].delete_room_on_close is True


@pytest.mark.anyio
async def test_entrypoint_records_only_fixed_lifecycle_and_provider_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = make_metadata()
    context = FakeJobContext(metadata)
    session = FakeEntrypointSession()
    spans: list[tuple[str, dict[str, object]]] = []

    @contextmanager
    def capture_lifecycle(**kwargs):
        spans.append(("lifecycle", kwargs))
        yield

    @contextmanager
    def capture_provider(**kwargs):
        spans.append(("provider", kwargs))
        yield

    monkeypatch.setattr(
        "agent.main.build_agent_runtime",
        lambda *_args, **_kwargs: (object(), session),
    )
    monkeypatch.setattr(
        agent_main,
        "agent_lifecycle_span",
        capture_lifecycle,
        raising=False,
    )
    monkeypatch.setattr(
        agent_main,
        "agent_provider_span",
        capture_provider,
        raising=False,
    )

    await entrypoint(context)

    assert spans == [
        (
            "lifecycle",
            {
                "call_id": metadata.call_id,
                "pipeline_mode": metadata.pipeline_mode,
            },
        ),
        (
            "provider",
            {
                "provider": "livekit",
                "operation": "connect",
                "call_id": metadata.call_id,
            },
        ),
        (
            "provider",
            {
                "provider": "livekit",
                "operation": "session_start",
                "call_id": metadata.call_id,
            },
        ),
    ]


@pytest.mark.anyio
async def test_entrypoint_registers_bounded_observability_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = make_metadata()
    context = FakeJobContext(metadata)
    session = FakeEntrypointSession()
    shutdown_calls: list[bool] = []

    async def capture_shutdown() -> None:
        shutdown_calls.append(True)

    monkeypatch.setattr(
        "agent.main.build_agent_runtime",
        lambda *_args, **_kwargs: (object(), session),
    )
    monkeypatch.setattr(
        agent_main,
        "shutdown_observability",
        capture_shutdown,
        raising=False,
    )

    await entrypoint(context)
    assert len(context.shutdown_callbacks) == 2
    assert context.shutdown_callbacks[0] is capture_shutdown
    await context.shutdown_callbacks[0]()

    assert shutdown_calls == [True]


@pytest.mark.anyio
async def test_entrypoint_registers_observability_shutdown_before_metadata_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = make_metadata()
    context = FakeJobContext(metadata)
    context.job.metadata = "{"
    shutdown_calls: list[bool] = []

    async def capture_shutdown() -> None:
        shutdown_calls.append(True)

    monkeypatch.setattr(
        agent_main,
        "shutdown_observability",
        capture_shutdown,
        raising=False,
    )

    with pytest.raises(json.JSONDecodeError):
        await entrypoint(context)

    assert context.shutdown_callbacks == [capture_shutdown]
    await context.shutdown_callbacks[0]()
    assert shutdown_calls == [True]

@pytest.mark.anyio
async def test_entrypoint_arms_call_limit_only_after_session_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = make_metadata()
    context = FakeJobContext(metadata)
    session = FakeEntrypointSession()

    class OrderingRuntime:
        call_limit_expired_on_start = False
        call_limit_task = None

        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def enforce_call_limit(self, _metadata, _disconnect) -> None:
            session.events.append(("enforce_call_limit", session.started))

        async def finalize(self, *_args, **_kwargs) -> None:
            return None

    monkeypatch.setattr(
        "agent.main.build_agent_runtime",
        lambda *_args, **_kwargs: (object(), session),
    )
    monkeypatch.setattr("agent.main.SessionRuntime", OrderingRuntime)

    await entrypoint(context)

    assert session.events[0:2] == [
        ("start", None),
        ("enforce_call_limit", True),
    ]


@pytest.mark.anyio
async def test_entrypoint_awaits_immediate_expiry_without_initial_greeting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = make_metadata(allowed_duration_seconds=1)
    context = FakeJobContext(metadata)
    session = FakeEntrypointSession()
    captured: dict[str, object] = {}

    class ExpiredRuntime:
        call_limit_expired_on_start = True
        call_limit_task = None

        def __init__(self, *_args, **_kwargs) -> None:
            captured["runtime"] = self

        def enforce_call_limit(self, _metadata, disconnect) -> None:
            session.events.append(("enforce_call_limit", session.started))
            self.call_limit_task = asyncio.create_task(disconnect())

        async def finalize(self, *_args, **_kwargs) -> None:
            return None

    monkeypatch.setattr(
        "agent.main.build_agent_runtime",
        lambda *_args, **_kwargs: (object(), session),
    )
    monkeypatch.setattr("agent.main.SessionRuntime", ExpiredRuntime)

    await entrypoint(context)
    runtime = captured["runtime"]
    if runtime.call_limit_task is not None:
        await runtime.call_limit_task

    greeting = (
        "Hello, I'm Agent, an AI assistant representing Owner. "
        "This call may be recorded. How can I help you?"
    )
    assert greeting not in session.say_calls
    assert session.events[:2] == [
        ("start", None),
        ("enforce_call_limit", True),
    ]
    assert session.events[-1] == ("shutdown", {"drain": True})


@pytest.mark.anyio
async def test_entrypoint_injects_job_shutdown_into_session_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = make_metadata()
    context = FakeJobContext(metadata)
    session = FakeEntrypointSession()
    captured: dict = {}

    class CapturingRuntime:
        def __init__(self, *_args, **kwargs) -> None:
            captured.update(kwargs)
            self.call_limit_expired_on_start = False
            self.call_limit_task = None

        def create_handler_task(self, _factory) -> bool:
            return True

        def enforce_call_limit(self, limit_metadata, disconnect) -> None:
            captured["limit_metadata"] = limit_metadata
            captured["disconnect"] = disconnect

        async def finalize(self, *_args, **_kwargs) -> None:
            return None

    monkeypatch.setattr(
        "agent.main.build_agent_runtime",
        lambda *_args, **_kwargs: (object(), session),
    )
    monkeypatch.setattr("agent.main.SessionRuntime", CapturingRuntime)
    monkeypatch.setattr("agent.main.time.monotonic", lambda: 100.0)

    await entrypoint(context)
    captured["fatal_shutdown"]("transcript_buffer_overflow")

    assert context.shutdown_reasons == ["transcript_buffer_overflow"]
    assert captured["limit_metadata"] == metadata
    assert captured["call_limit_started_at"] == 100.0

    await captured["warning_callback"](
        "Attention, il vous reste une minute avant la fin de cet appel."
    )
    await captured["disconnect"]()

    assert session.events[-5:] == [
        (
            "say",
            {
                "text": "Attention, il vous reste une minute avant la fin de cet appel.",
                "allow_interruptions": False,
            },
        ),
        ("interrupt", {"force": True}),
        ("set_audio_enabled", False),
        (
            "say",
            {
                "text": "La durée maximale de cet appel est atteinte. Merci de votre appel. Au revoir.",
                "allow_interruptions": False,
            },
        ),
        ("shutdown", {"drain": True}),
    ]


def test_silero_prewarm_failure_does_not_render_exception_message(
    monkeypatch,
    caplog,
) -> None:
    from livekit import plugins

    class FailingVad:
        @staticmethod
        def load():
            raise RuntimeError("SILERO_AUTHORIZATION_SENTINEL")

    fake_silero = ModuleType("livekit.plugins.silero")
    fake_silero.VAD = FailingVad
    monkeypatch.setattr(plugins, "silero", fake_silero, raising=False)
    monkeypatch.setitem(sys.modules, "livekit.plugins.silero", fake_silero)
    monkeypatch.setattr(
        "agent.main.get_settings",
        lambda: SimpleNamespace(
            livekit_silero_vad_enabled=True,
            livekit_turn_detector_enabled=False,
        ),
    )

    with caplog.at_level(logging.ERROR):
        prewarm_assets(SimpleNamespace(userdata={}))

    assert "SILERO_AUTHORIZATION_SENTINEL" not in caplog.text
    assert "event=silero_prewarm_failed" in caplog.text
    assert "operation=load_silero_vad" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_observability_initialization_failure_does_not_prevent_prewarm(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from livekit import plugins

    def fail_initialization() -> None:
        raise RuntimeError("OTEL_EXPORTER_CREDENTIAL_SENTINEL")

    adaptive_mode = object()
    smart_turn_mode = object()
    fake_speechmatics = ModuleType("livekit.plugins.speechmatics")
    fake_speechmatics.TurnDetectionMode = SimpleNamespace(
        ADAPTIVE=adaptive_mode,
        SMART_TURN=smart_turn_mode,
    )
    fake_smart_turn = ModuleType("speechmatics.voice._smart_turn")
    fake_smart_turn.SmartTurnDetector = object
    monkeypatch.setattr(plugins, "speechmatics", fake_speechmatics, raising=False)
    monkeypatch.setitem(sys.modules, "livekit.plugins.speechmatics", fake_speechmatics)
    monkeypatch.setitem(sys.modules, "speechmatics.voice._smart_turn", fake_smart_turn)

    monkeypatch.setattr(
        agent_main,
        "initialize_observability",
        fail_initialization,
        raising=False,
    )
    monkeypatch.setattr(
        "agent.main.get_settings",
        lambda: SimpleNamespace(
            livekit_silero_vad_enabled=False,
            livekit_turn_detector_enabled=False,
        ),
    )
    monkeypatch.setattr(
        "agent.main._resolve_speechmatics_turn_detection_mode",
        lambda _plugin: adaptive_mode,
    )

    with caplog.at_level(logging.ERROR):
        prewarm_assets(SimpleNamespace(userdata={}))

    assert "OTEL_EXPORTER_CREDENTIAL_SENTINEL" not in caplog.text
    assert "event=agent_observability_initialization_failed" in caplog.text
    assert "operation=initialize_agent_observability" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_speechmatics_prewarm_failure_does_not_render_exception_message(
    monkeypatch,
    caplog,
) -> None:
    from livekit import plugins

    smart_turn_mode = object()
    fake_speechmatics = ModuleType("livekit.plugins.speechmatics")
    fake_speechmatics.TurnDetectionMode = SimpleNamespace(SMART_TURN=smart_turn_mode)

    class FailingSmartTurnDetector:
        def setup(self) -> None:
            raise RuntimeError("SPEECHMATICS_TOKEN_SENTINEL")

    fake_smart_turn = ModuleType("speechmatics.voice._smart_turn")
    fake_smart_turn.SmartTurnDetector = FailingSmartTurnDetector
    monkeypatch.setattr(plugins, "speechmatics", fake_speechmatics, raising=False)
    monkeypatch.setitem(sys.modules, "livekit.plugins.speechmatics", fake_speechmatics)
    monkeypatch.setitem(sys.modules, "speechmatics.voice._smart_turn", fake_smart_turn)
    monkeypatch.setattr(
        "agent.main.get_settings",
        lambda: SimpleNamespace(
            livekit_silero_vad_enabled=False,
            livekit_turn_detector_enabled=False,
        ),
    )
    monkeypatch.setattr(
        "agent.main._resolve_speechmatics_turn_detection_mode",
        lambda _plugin: smart_turn_mode,
    )

    with caplog.at_level(logging.ERROR):
        prewarm_assets(SimpleNamespace(userdata={}))

    assert "SPEECHMATICS_TOKEN_SENTINEL" not in caplog.text
    assert "event=speechmatics_prewarm_failed" in caplog.text
    assert "operation=setup_smart_turn_detector" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
