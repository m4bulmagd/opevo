import asyncio
from agent.main import build_worker_options
from agent.main import _send_initial_greeting
from agent.main import _register_standard_session_handlers
from agent.main import _register_sts_session_handlers
from agent.schemas import DispatchMetadata
from pathlib import Path


def test_build_worker_options_sets_prewarm_hook() -> None:
    options = build_worker_options()

    assert options.prewarm_fnc is not None
    assert options.prewarm_fnc.__name__ == "prewarm_assets"


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
    metadata = DispatchMetadata(call_id="call-1", user_id="user-1", agent_name="Agent", owner_name="Owner")

    _register_standard_session_handlers(session, runtime, metadata)

    session.handlers["user_input_transcribed"](FakeTranscriptEvent("Hello", is_final=True))
    session.handlers["conversation_item_added"](FakeConversationEvent("assistant", "Hi there"))

    assert runtime.caller_text == ["Hello"]
    assert runtime.agent_text == ["Hi there"]


def test_register_sts_session_handlers_forwards_caller_and_agent_text(monkeypatch) -> None:
    _run_scheduled_coroutine(monkeypatch)
    session = FakeSession()
    runtime = FakeRuntime()
    metadata = DispatchMetadata(call_id="call-1", user_id="user-1", agent_name="Agent", owner_name="Owner")

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
            DispatchMetadata(
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
            DispatchMetadata(
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
