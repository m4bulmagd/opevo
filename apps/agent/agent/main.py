import time
import logging
import importlib
import inspect

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

from agent.api_client import AgentApiClient
from agent.config import get_settings
from agent.event_publisher import EventPublisher
from agent.observability import (
    agent_lifecycle_span,
    agent_provider_span,
    initialize_observability,
    shutdown_observability,
)
from agent.pipeline_factory import build_agent_runtime
from agent.pipeline_factory import _resolve_speechmatics_turn_detection_mode
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


def _register_inference_runners() -> None:
    if get_settings().livekit_turn_detector_enabled:
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
    if (
        getattr(metadata, "pipeline_mode", PipelineMode.STT_LLM_TTS.value)
        == PipelineMode.STS.value
    ):
        _register_sts_session_handlers(session, runtime, metadata)
        return
    _register_standard_session_handlers(session, runtime, metadata)


async def _send_initial_greeting(session, metadata: CustomerCallDispatch) -> None:
    greeting = build_initial_greeting(
        agent_name=metadata.agent_name,
        owner_name=metadata.owner_name,
    )

    if (
        getattr(metadata, "pipeline_mode", PipelineMode.STT_LLM_TTS.value)
        == PipelineMode.STS.value
    ):
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


async def entrypoint(context: JobContext) -> None:
    _initialize_observability_safely()
    context.add_shutdown_callback(shutdown_observability)
    metadata = parse_dispatch(context.job.metadata or "{}")
    if isinstance(metadata, ForwardingVerificationDispatch):
        await run_forwarding_verification(context, metadata)
        return
    metadata_dict = dump_contract(metadata)
    started_at = time.monotonic()
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

        prewarmed = getattr(context.proc, "userdata", {}) or {}
        agent, session = build_agent_runtime(
            metadata_dict,
            vad=prewarmed.get("silero_vad"),
            inference_executor=context.inference_executor,
        )
        runtime = SessionRuntime(
            EventPublisher(),
            api_client=AgentApiClient(),
            fatal_shutdown=context.shutdown,
            call_limit_started_at=started_at,
            warning_callback=lambda message: _play_call_limit_message(
                session,
                metadata,
                message,
            ),
        )
        _register_session_handlers(session, runtime, metadata)
        context.add_shutdown_callback(
            lambda *_: runtime.finalize(
                metadata,
                duration_seconds=max(1, int(time.monotonic() - started_at)),
            )
        )
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
        runtime.enforce_call_limit(
            metadata,
            lambda: _disconnect_at_call_limit(session, metadata),
        )
        if runtime.call_limit_expired_on_start:
            if runtime.call_limit_task is not None:
                await runtime.call_limit_task
            return
        await _send_initial_greeting(session, metadata)
        session.input.set_audio_enabled(True)


def prewarm_assets(proc) -> None:
    _initialize_observability_safely()
    settings = get_settings()
    userdata = getattr(proc, "userdata", None)
    if userdata is None:
        userdata = {}
        proc.userdata = userdata

    if settings.livekit_silero_vad_enabled:
        try:
            from livekit.plugins import silero

            userdata["silero_vad"] = silero.VAD.load()
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

    try:
        from livekit.plugins import speechmatics
        from speechmatics.voice._smart_turn import SmartTurnDetector
    except ModuleNotFoundError:
        logger.info("speechmatics prewarm skipped: optional packages unavailable")
        return

    try:
        if (
            _resolve_speechmatics_turn_detection_mode(speechmatics)
            == speechmatics.TurnDetectionMode.SMART_TURN
        ):
            SmartTurnDetector().setup()
    except Exception as exc:
        report_safe_exception(
            logger,
            event="speechmatics_prewarm_failed",
            operation="setup_smart_turn_detector",
            error=exc,
        )


def build_worker_options() -> WorkerOptions:
    settings = get_settings()
    validate_agent_runtime(settings)
    _register_inference_runners()
    return WorkerOptions(
        entrypoint_fnc=entrypoint,
        request_fnc=handle_job_request,
        prewarm_fnc=prewarm_assets,
        agent_name=settings.livekit_agent_name,
        ws_url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        drain_timeout=WORKER_DRAIN_TIMEOUT_SECONDS,
    )


if __name__ == "__main__":
    cli.run_app(build_worker_options())
