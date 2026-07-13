import asyncio
import logging
import sys
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

import agent.main as agent_main
from agent.main import build_worker_options
from agent.main import entrypoint
from agent.main import _safe_task
from agent.main import _send_initial_greeting
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
        "dispatch_token": "dispatch-token",
    }
    defaults.update(overrides)
    return DispatchMetadata(**defaults)


def test_build_worker_options_sets_prewarm_hook() -> None:
    options = build_worker_options()

    assert options.prewarm_fnc is not None
    assert options.prewarm_fnc.__name__ == "prewarm_assets"


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
        self.generate_reply_calls: list[dict] = []

    async def say(self, text: str):
        self.say_calls.append(text)

    async def generate_reply(self, **kwargs):
        self.generate_reply_calls.append(kwargs)


def _run_scheduled_coroutine(monkeypatch) -> None:
    def run_now(coro):
        asyncio.run(coro)
        return None

    monkeypatch.setattr("agent.main.asyncio.create_task", run_now)


def test_register_standard_session_handlers_forwards_final_caller_and_agent_text(monkeypatch) -> None:
    _run_scheduled_coroutine(monkeypatch)
    session = FakeSession()
    runtime = FakeRuntime()
    metadata = make_metadata(call_id="call-1", user_id="user-1")

    _register_standard_session_handlers(session, runtime, metadata)

    session.handlers["user_input_transcribed"](FakeTranscriptEvent("Hello", is_final=True))
    session.handlers["conversation_item_added"](FakeConversationEvent("assistant", "Hi there"))

    assert runtime.caller_text == ["Hello"]
    assert runtime.agent_text == ["Hi there"]


def test_register_sts_session_handlers_forwards_caller_and_agent_text(monkeypatch) -> None:
    _run_scheduled_coroutine(monkeypatch)
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

    async def connect(self, **_kwargs) -> None:
        self.events.append("connect")

    async def wait_for_participant(self, *, kind) -> object:
        self.events.append(("wait_for_participant", kind))
        return SimpleNamespace(identity="sip-caller")

    def add_shutdown_callback(self, callback) -> None:
        self.shutdown_callbacks.append(callback)


class FakeEntrypointSession(FakeSession):
    def __init__(self) -> None:
        super().__init__()
        self.started = False
        self.start_kwargs: dict = {}
        self.say_calls: list[str] = []

    async def start(self, **kwargs) -> None:
        self.started = True
        self.start_kwargs = kwargs

    async def say(self, text: str) -> None:
        self.say_calls.append(text)


@pytest.mark.anyio
async def test_entrypoint_connects_then_waits_only_for_sip_participant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = make_metadata()
    context = FakeJobContext(metadata)
    session = FakeEntrypointSession()
    monkeypatch.setattr(
        "agent.main.build_agent_runtime",
        lambda *_args, **_kwargs: (object(), session),
    )

    await entrypoint(context)

    assert context.events == ["connect", ("wait_for_participant", 3)]
    assert session.started is True
    assert session.start_kwargs["room_options"].participant_identity == "sip-caller"
    assert session.start_kwargs["room_options"].participant_kinds == [3]


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
