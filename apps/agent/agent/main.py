import json
import time
import asyncio
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
from pydantic import ValidationError

from agent.api_client import AgentApiClient
from agent.config import get_settings
from agent.event_publisher import EventPublisher
from agent.pipeline_factory import build_agent_runtime
from agent.pipeline_factory import _resolve_speechmatics_turn_detection_mode
from agent.providers import PipelineMode
from agent.runtime_validation import validate_agent_runtime
from agent.safe_logging import report_safe_exception
from agent.schemas import DispatchMetadata
from agent.session_runtime import SessionRuntime


logger = logging.getLogger(__name__)
SIP_PARTICIPANT_KIND = rtc.ParticipantKind.Value("PARTICIPANT_KIND_SIP")


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


async def _handle_standard_user_input_transcribed(runtime: SessionRuntime, metadata: dict, event) -> None:
    if not getattr(event, "is_final", False) or not getattr(event, "transcript", None):
        return
    await runtime.handle_caller_transcript(metadata, event.transcript)


async def _handle_standard_conversation_item_added(runtime: SessionRuntime, metadata: dict, event) -> None:
    item = getattr(event, "item", None)
    if item is None or getattr(item, "type", None) != "message":
        return
    if getattr(item, "role", None) != "assistant":
        return
    text = getattr(item, "text_content", None)
    if not text:
        return
    await runtime.handle_agent_utterance(metadata, text)


async def _handle_sts_conversation_item_added(runtime: SessionRuntime, metadata: dict, event) -> None:
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


def _register_standard_session_handlers(session, runtime: SessionRuntime, metadata: DispatchMetadata) -> None:
    def on_user_input_transcribed(event) -> None:
        asyncio.create_task(_safe_task(_handle_standard_user_input_transcribed(runtime, metadata, event)))

    def on_conversation_item_added(event) -> None:
        asyncio.create_task(_safe_task(_handle_standard_conversation_item_added(runtime, metadata, event)))

    session.on("user_input_transcribed", on_user_input_transcribed)
    session.on("conversation_item_added", on_conversation_item_added)


def _register_sts_session_handlers(session, runtime: SessionRuntime, metadata: DispatchMetadata) -> None:
    def on_conversation_item_added(event) -> None:
        asyncio.create_task(_safe_task(_handle_sts_conversation_item_added(runtime, metadata, event)))

    session.on("conversation_item_added", on_conversation_item_added)


def _register_session_handlers(session, runtime: SessionRuntime, metadata: DispatchMetadata) -> None:
    if getattr(metadata, "pipeline_mode", PipelineMode.STT_LLM_TTS.value) == PipelineMode.STS.value:
        _register_sts_session_handlers(session, runtime, metadata)
        return
    _register_standard_session_handlers(session, runtime, metadata)

async def _send_initial_greeting(session, metadata: DispatchMetadata) -> None:
    greeting = (
        f"Hello, I'm {metadata.agent_name}, an AI assistant representing "
        f"{metadata.owner_name}. This call may be recorded. How can I help you?"
    )

    if getattr(metadata, "pipeline_mode", PipelineMode.STT_LLM_TTS.value) == PipelineMode.STS.value:
        result = session.generate_reply(
            instructions=f'Start the conversation by saying exactly: "{greeting}"'
        )
    else:
        result = session.say(greeting)

    if inspect.isawaitable(result):
        await result


async def handle_job_request(request: JobRequest) -> None:
    try:
        metadata_dict = json.loads(request.job.metadata or "{}")
        metadata = DispatchMetadata.model_validate(metadata_dict)
        if metadata.agent_identity != f"agent-call-{metadata.call_id}":
            raise ValueError("invalid agent identity")
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
        logger.warning("job_request_rejected reason=invalid_dispatch_metadata")
        await request.reject(terminate=True)
        return

    await request.accept(
        name=metadata.agent_name,
        identity=metadata.agent_identity,
    )


async def entrypoint(context: JobContext) -> None:
    metadata_dict = json.loads(context.job.metadata or "{}")
    metadata = DispatchMetadata.model_validate(metadata_dict)
    metadata_dict = metadata.model_dump()
    started_at = time.monotonic()
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
    runtime = SessionRuntime(EventPublisher(), api_client=AgentApiClient())
    _register_session_handlers(session, runtime, metadata)
    context.add_shutdown_callback(
        lambda *_: runtime.finalize(
            metadata,
            duration_seconds=max(1, int(time.monotonic() - started_at)),
        )
    )
    await session.start(
        agent=agent,
        room=context.room,
        room_options=room_io.RoomOptions(
            participant_identity=sip_participant.identity,
            participant_kinds=[SIP_PARTICIPANT_KIND],
        ),
    )
    await _send_initial_greeting(session, metadata)


def prewarm_assets(proc) -> None:
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
        if _resolve_speechmatics_turn_detection_mode(speechmatics) == speechmatics.TurnDetectionMode.SMART_TURN:
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
    )


if __name__ == "__main__":
    cli.run_app(build_worker_options())
