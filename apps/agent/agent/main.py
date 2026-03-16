import json
import os
import time
import asyncio
import logging
import importlib

from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli

from agent.api_client import AgentApiClient
from agent.event_publisher import EventPublisher
from agent.pipeline_factory import build_agent_runtime
from agent.pipeline_factory import _env_bool
from agent.pipeline_factory import _resolve_speechmatics_turn_detection_mode
from agent.providers import PipelineMode
from agent.session_runtime import SessionRuntime


logger = logging.getLogger(__name__)


def _register_inference_runners() -> None:
    if _env_bool("LIVEKIT_TURN_DETECTOR_ENABLED", True):
        importlib.import_module("livekit.plugins.turn_detector.multilingual")


async def _handle_legacy_user_input_transcribed(runtime: SessionRuntime, metadata: dict, event) -> None:
    if not getattr(event, "is_final", False) or not getattr(event, "transcript", None):
        return
    await runtime.handle_caller_transcript(metadata, event.transcript)


async def _handle_legacy_conversation_item_added(runtime: SessionRuntime, metadata: dict, event) -> None:
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


def _register_legacy_session_handlers(session, runtime: SessionRuntime, metadata: dict) -> None:
    def on_user_input_transcribed(event) -> None:
        asyncio.create_task(_handle_legacy_user_input_transcribed(runtime, metadata, event))

    def on_conversation_item_added(event) -> None:
        asyncio.create_task(_handle_legacy_conversation_item_added(runtime, metadata, event))

    session.on("user_input_transcribed", on_user_input_transcribed)
    session.on("conversation_item_added", on_conversation_item_added)


def _register_sts_session_handlers(session, runtime: SessionRuntime, metadata: dict) -> None:
    def on_conversation_item_added(event) -> None:
        asyncio.create_task(_handle_sts_conversation_item_added(runtime, metadata, event))

    session.on("conversation_item_added", on_conversation_item_added)


def _register_session_handlers(session, runtime: SessionRuntime, metadata: dict) -> None:
    if metadata.get("pipeline_mode", PipelineMode.STT_LLM_TTS.value) == PipelineMode.STS.value:
        _register_sts_session_handlers(session, runtime, metadata)
        return
    _register_legacy_session_handlers(session, runtime, metadata)


async def entrypoint(context: JobContext) -> None:
    metadata = json.loads(context.job.metadata or "{}")
    started_at = time.monotonic()
    await context.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)
    await context.wait_for_participant()

    prewarmed = getattr(context.proc, "userdata", {}) or {}
    agent, session = build_agent_runtime(
        metadata,
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
    await session.start(agent=agent, room=context.room)
    await session.say(f"Hello, I'm {metadata['agent_name']}, an AI assistant representing {metadata['owner_name']}. This call may be recorded. How can I help you?")


def prewarm_assets(proc) -> None:
    userdata = getattr(proc, "userdata", None)
    if userdata is None:
        userdata = {}
        proc.userdata = userdata

    if _env_bool("LIVEKIT_SILERO_VAD_ENABLED", True):
        try:
            from livekit.plugins import silero

            userdata["silero_vad"] = silero.VAD.load()
        except ModuleNotFoundError:
            logger.info("silero prewarm skipped: optional package unavailable")
        except Exception:
            logger.exception("silero prewarm failed")

    if _env_bool("LIVEKIT_TURN_DETECTOR_ENABLED", True):
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
    except Exception:
        logger.exception("speechmatics prewarm failed")


def build_worker_options() -> WorkerOptions:
    _register_inference_runners()
    return WorkerOptions(
        entrypoint_fnc=entrypoint,
        prewarm_fnc=prewarm_assets,
        agent_name=os.getenv("LIVEKIT_AGENT_NAME", "ai-call-agent"),
        ws_url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )


if __name__ == "__main__":
    cli.run_app(build_worker_options())
