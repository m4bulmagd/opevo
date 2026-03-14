import json
import os
import time
import asyncio
import logging

from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli

from agent.api_client import AgentApiClient
from agent.event_publisher import EventPublisher
from agent.pipeline_factory import build_agent_runtime
from agent.pipeline_factory import _env_bool
from agent.pipeline_factory import _resolve_speechmatics_turn_detection_mode
from agent.session_runtime import SessionRuntime


logger = logging.getLogger(__name__)


async def entrypoint(context: JobContext) -> None:
    metadata = json.loads(context.job.metadata or "{}")
    started_at = time.monotonic()
    await context.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)
    await context.wait_for_participant()

    prewarmed = getattr(context.proc, "userdata", {}) or {}
    agent, session = build_agent_runtime(
        metadata,
        vad=prewarmed.get("silero_vad"),
    )
    runtime = SessionRuntime(EventPublisher(), api_client=AgentApiClient())

    def on_user_input_transcribed(event) -> None:
        if not getattr(event, "is_final", False) or not getattr(event, "transcript", None):
            return
        asyncio.create_task(runtime.handle_caller_transcript(metadata, event.transcript))

    def on_conversation_item_added(event) -> None:
        item = getattr(event, "item", None)
        if item is None or getattr(item, "type", None) != "message":
            return
        if getattr(item, "role", None) != "assistant":
            return
        text = item.text_content
        if not text:
            return
        asyncio.create_task(runtime.handle_agent_utterance(metadata, text))

    session.on("user_input_transcribed", on_user_input_transcribed)
    session.on("conversation_item_added", on_conversation_item_added)
    context.add_shutdown_callback(
        lambda *_: runtime.finalize(
            metadata,
            duration_seconds=max(1, int(time.monotonic() - started_at)),
        )
    )
    await session.start(agent=agent, room=context.room)
    await session.say("Hello")


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
