import asyncio
import time
import logging
import importlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from livekit import rtc
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    JobRequest,
    WorkerOptions,
    cli,
    room_io,
)
from presvo_contracts import (
    ContractError,
    CustomerCallDispatch,
    ForwardingVerificationDispatch,
    dump_contract,
    parse_dispatch,
)

from agent.composition import (
    build_agent_api_client,
    build_agent_process_runtime,
    build_event_publisher,
    publish_agent_process_runtime,
    require_agent_process_runtime,
)
from agent.config import AgentSettings, get_settings
from agent.observability import (
    agent_lifecycle_span,
    agent_provider_span,
    initialize_observability,
    shutdown_observability,
)
from agent.pipeline_factory import build_agent_runtime
from agent.prompt_builder import build_initial_greeting
from agent.providers import PipelineMode
from agent.runtime_validation import validate_agent_runtime
from agent.safe_logging import report_safe_exception
from agent.session_runtime import (
    CALL_LIMIT_EXPIRY_MESSAGE,
    SessionRuntime,
)
from agent.verification_runtime import run_forwarding_verification


logger = logging.getLogger(__name__)
SIP_PARTICIPANT_KIND = rtc.ParticipantKind.Value("PARTICIPANT_KIND_SIP")
WORKER_DRAIN_TIMEOUT_SECONDS = 3900
AgentRuntimeFactory = Callable[..., tuple[Any, Any]]
SessionRuntimeFactory = Callable[..., SessionRuntime]


def _initialize_observability_safely() -> None:
    try:
        initialize_observability()
    except Exception as exc:
        report_safe_exception(
            logger,
            event="agent_observability_initialization_failed",
            operation="initialize_agent_observability",
            error=exc,
        )


async def _safe_task(coro) -> None:
    try:
        await coro
    except Exception as exc:
        report_safe_exception(
            logger,
            event="background_event_handler_failed",
            operation="run_background_event_handler",
            error=exc,
        )


async def _join_ordered_shutdown(
    shutdown_task: asyncio.Task[None],
    shutdown_complete: asyncio.Future[None],
    shutdown_failure_reported: asyncio.Event,
) -> None:
    outer_cancellation: asyncio.CancelledError | None = None
    while not shutdown_complete.done():
        try:
            await asyncio.shield(shutdown_complete)
        except asyncio.CancelledError as error:
            if outer_cancellation is None:
                outer_cancellation = error

    shutdown_failure: BaseException | None = None
    try:
        shutdown_task.result()
    except BaseException as error:
        shutdown_failure = error

    if outer_cancellation is not None:
        if shutdown_failure is not None and not shutdown_failure_reported.is_set():
            shutdown_failure_reported.set()
            report_safe_exception(
                logger,
                event="agent_ordered_shutdown_failed",
                operation="run_agent_ordered_shutdown",
                error=shutdown_failure,
            )
        outer_cancellation.__traceback__ = None
        raise outer_cancellation from None
    if shutdown_failure is not None:
        raise shutdown_failure


def _register_inference_runners(settings: AgentSettings) -> None:
    if settings.livekit_turn_detector_enabled:
        importlib.import_module("livekit.plugins.turn_detector.multilingual")


async def _handle_standard_user_input_transcribed(
    runtime: SessionRuntime,
    metadata: CustomerCallDispatch,
    event,
) -> None:
    if not getattr(event, "is_final", False) or not getattr(event, "transcript", None):
        return
    await runtime.handle_caller_transcript(metadata, event.transcript)


async def _handle_standard_conversation_item_added(
    runtime: SessionRuntime,
    metadata: CustomerCallDispatch,
    event,
) -> None:
    item = getattr(event, "item", None)
    if item is None or getattr(item, "type", None) != "message":
        return
    if getattr(item, "role", None) != "assistant":
        return
    text = getattr(item, "text_content", None)
    if not text:
        return
    await runtime.handle_agent_utterance(metadata, text)


async def _handle_sts_conversation_item_added(
    runtime: SessionRuntime,
    metadata: CustomerCallDispatch,
    event,
) -> None:
    item = getattr(event, "item", None)
    if item is None or getattr(item, "type", None) != "message":
        return

    text = getattr(item, "text_content", None)
    if not text:
        return

    role = getattr(item, "role", None)
    if role == "user":
        await runtime.handle_caller_transcript(metadata, text)
    elif role == "assistant":
        await runtime.handle_agent_utterance(metadata, text)


def _register_standard_session_handlers(
    session, runtime: SessionRuntime, metadata: CustomerCallDispatch
) -> None:
    def on_user_input_transcribed(event) -> None:
        runtime.create_handler_task(
            lambda: _safe_task(
                _handle_standard_user_input_transcribed(
                    runtime,
                    metadata,
                    event,
                )
            )
        )

    def on_conversation_item_added(event) -> None:
        runtime.create_handler_task(
            lambda: _safe_task(
                _handle_standard_conversation_item_added(
                    runtime,
                    metadata,
                    event,
                )
            )
        )

    session.on("user_input_transcribed", on_user_input_transcribed)
    session.on("conversation_item_added", on_conversation_item_added)


def _register_sts_session_handlers(
    session, runtime: SessionRuntime, metadata: CustomerCallDispatch
) -> None:
    def on_conversation_item_added(event) -> None:
        runtime.create_handler_task(
            lambda: _safe_task(
                _handle_sts_conversation_item_added(runtime, metadata, event)
            )
        )

    session.on("conversation_item_added", on_conversation_item_added)


def _register_session_handlers(
    session, runtime: SessionRuntime, metadata: CustomerCallDispatch
) -> None:
    if metadata.pipeline_mode == PipelineMode.STS.value:
        _register_sts_session_handlers(session, runtime, metadata)
        return
    _register_standard_session_handlers(session, runtime, metadata)


async def _send_initial_greeting(session, metadata: CustomerCallDispatch) -> None:
    greeting = build_initial_greeting(
        agent_name=metadata.agent_name,
        owner_name=metadata.owner_name,
    )

    if metadata.pipeline_mode == PipelineMode.STS.value:
        result = session.generate_reply(
            instructions=(
                "Say exactly in English, without adding or removing words: "
                f'"{greeting}"'
            ),
            allow_interruptions=False,
        )
    else:
        result = session.say(greeting, allow_interruptions=False)

    if inspect.isawaitable(result):
        await result


async def _play_call_limit_message(
    session,
    metadata: CustomerCallDispatch,
    message: str,
) -> None:
    if metadata.pipeline_mode == PipelineMode.STS.value:
        result = session.generate_reply(
            instructions=(
                f'Say exactly in English, without adding or removing words: "{message}"'
            ),
            allow_interruptions=False,
        )
    else:
        result = session.say(message, allow_interruptions=False)

    if inspect.isawaitable(result):
        await result


async def _disconnect_at_call_limit(
    session,
    metadata: CustomerCallDispatch,
) -> None:
    try:
        await session.interrupt(force=True)
        session.input.set_audio_enabled(False)
        await _play_call_limit_message(
            session,
            metadata,
            CALL_LIMIT_EXPIRY_MESSAGE,
        )
    finally:
        session.shutdown(drain=True)


async def handle_job_request(request: JobRequest) -> None:
    try:
        metadata = parse_dispatch(request.job.metadata or "{}")
        if isinstance(metadata, ForwardingVerificationDispatch):
            expected_identity = f"agent-verification-{metadata.verification_session_id}"
            display_name = "Presvo forwarding verification"
        else:
            expected_identity = f"agent-call-{metadata.call_id}"
            display_name = metadata.agent_name
        if metadata.agent_identity != expected_identity:
            logger.warning("job_request_rejected reason=invalid_agent_identity")
            await request.reject(terminate=True)
            return
    except ContractError as error:
        logger.warning(
            "job_request_rejected contract_name=%s code=%s transport=livekit",
            error.contract_name,
            error.code,
        )
        await request.reject(terminate=True)
        return

    await request.accept(
        name=display_name,
        identity=metadata.agent_identity,
    )


async def entrypoint(
    context: JobContext,
    *,
    agent_runtime_factory: AgentRuntimeFactory = build_agent_runtime,
    session_runtime_factory: SessionRuntimeFactory = SessionRuntime,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    process_runtime = require_agent_process_runtime(context.proc)
    session_runtime: SessionRuntime | None = None
    customer_metadata: CustomerCallDispatch | None = None
    started_at = monotonic()
    entrypoint_use_complete = asyncio.Event()
    shutdown_failure_reported = asyncio.Event()
    shutdown_task: asyncio.Task[None] | None = None
    shutdown_complete: asyncio.Future[None] | None = None

    async def run_ordered_shutdown() -> None:
        await entrypoint_use_complete.wait()
        try:
            if session_runtime is not None and customer_metadata is not None:
                await session_runtime.finalize(
                    customer_metadata,
                    duration_seconds=max(1, int(monotonic() - started_at)),
                )
        finally:
            try:
                await process_runtime.aclose()
            finally:
                await shutdown_observability()

    async def shutdown_job(*_args: object) -> None:
        nonlocal shutdown_complete, shutdown_task
        if shutdown_task is None:
            completion = asyncio.get_running_loop().create_future()
            shutdown_complete = completion
            shutdown_task = asyncio.create_task(
                run_ordered_shutdown(),
                name="agent_ordered_shutdown",
            )

            def mark_shutdown_complete(_task: asyncio.Task[None]) -> None:
                completion.set_result(None)

            shutdown_task.add_done_callback(mark_shutdown_complete)

        assert shutdown_complete is not None
        await _join_ordered_shutdown(
            shutdown_task,
            shutdown_complete,
            shutdown_failure_reported,
        )

    context.add_shutdown_callback(shutdown_job)
    try:
        metadata = parse_dispatch(context.job.metadata or "{}")
        if isinstance(metadata, ForwardingVerificationDispatch):
            await run_forwarding_verification(
                context,
                metadata,
                settings=process_runtime.settings,
                api_client=process_runtime.api_client,
            )
            return
        customer_metadata = metadata
        metadata_dict = dump_contract(metadata)
        with agent_lifecycle_span(
            call_id=str(metadata.call_id),
            pipeline_mode=metadata.pipeline_mode,
        ):
            with agent_provider_span(
                provider="livekit",
                operation="connect",
                call_id=str(metadata.call_id),
            ):
                await context.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)
                sip_participant = await context.wait_for_participant(
                    kind=SIP_PARTICIPANT_KIND
                )

            agent, session = agent_runtime_factory(
                metadata_dict,
                settings=process_runtime.settings,
                vad=process_runtime.silero_vad,
            )
            session_runtime = session_runtime_factory(
                process_runtime.event_publisher,
                api_client=process_runtime.api_client,
                fatal_shutdown=context.shutdown,
                call_limit_started_at=started_at,
                warning_callback=lambda message: _play_call_limit_message(
                    session,
                    metadata,
                    message,
                ),
            )
            _register_session_handlers(session, session_runtime, metadata)
            session.input.set_audio_enabled(False)
            with agent_provider_span(
                provider="livekit",
                operation="session_start",
                call_id=str(metadata.call_id),
            ):
                await session.start(
                    agent=agent,
                    room=context.room,
                    room_options=room_io.RoomOptions(
                        participant_identity=sip_participant.identity,
                        participant_kinds=[SIP_PARTICIPANT_KIND],
                        close_on_disconnect=True,
                        delete_room_on_close=True,
                    ),
                    record={
                        "audio": False,
                        "transcript": False,
                        "traces": False,
                        "logs": False,
                    },
                )
            session_runtime.enforce_call_limit(
                metadata,
                lambda: _disconnect_at_call_limit(session, metadata),
            )
            if session_runtime.call_limit_expired_on_start:
                if session_runtime.call_limit_task is not None:
                    await session_runtime.call_limit_task
                return
            await _send_initial_greeting(session, metadata)
            session.input.set_audio_enabled(True)
    finally:
        entrypoint_use_complete.set()


def prewarm_assets(
    proc,
    *,
    settings: AgentSettings,
    api_client_factory=build_agent_api_client,
    event_publisher_factory=build_event_publisher,
) -> None:
    runtime = build_agent_process_runtime(
        settings,
        api_client_factory=api_client_factory,
        event_publisher_factory=event_publisher_factory,
    )
    silero_vad = None

    if settings.livekit_silero_vad_enabled:
        try:
            from livekit.plugins import silero

            silero_vad = silero.VAD.load()
        except ModuleNotFoundError:
            logger.info("silero prewarm skipped: optional package unavailable")
        except Exception as exc:
            report_safe_exception(
                logger,
                event="silero_prewarm_failed",
                operation="load_silero_vad",
                error=exc,
            )

    if settings.livekit_turn_detector_enabled:
        logger.info("turn detector will initialize in job context")

    runtime.silero_vad = silero_vad
    publish_agent_process_runtime(proc, runtime)
    _initialize_observability_safely()


@dataclass(frozen=True, slots=True)
class _PrewarmConfiguredAssets:
    settings: AgentSettings = field(repr=False)

    def __call__(self, proc: Any) -> None:
        prewarm_assets(proc, settings=self.settings)


def build_worker_options(settings: AgentSettings | None = None) -> WorkerOptions:
    configured = settings or get_settings()
    validate_agent_runtime(configured)
    _register_inference_runners(configured)

    return WorkerOptions(
        entrypoint_fnc=entrypoint,
        request_fnc=handle_job_request,
        prewarm_fnc=_PrewarmConfiguredAssets(configured),
        agent_name=configured.livekit_agent_name,
        ws_url=configured.livekit_url,
        api_key=configured.livekit_api_key,
        api_secret=configured.livekit_api_secret,
        drain_timeout=WORKER_DRAIN_TIMEOUT_SECONDS,
    )


if __name__ == "__main__":
    cli.run_app(build_worker_options())
