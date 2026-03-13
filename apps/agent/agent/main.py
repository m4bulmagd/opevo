import json
import os
import time
import asyncio

from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli

from agent.api_client import AgentApiClient
from agent.event_publisher import EventPublisher
from agent.pipeline_factory import build_agent_runtime
from agent.session_runtime import SessionRuntime


async def entrypoint(context: JobContext) -> None:
    metadata = json.loads(context.job.metadata or "{}")
    started_at = time.monotonic()
    await context.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)
    await context.wait_for_participant()

    agent, session = build_agent_runtime(metadata)
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
    await session.say("Bonjour, je suis votre assistant IA. Cet appel peut etre enregistre.")


def build_worker_options() -> WorkerOptions:
    return WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name=os.getenv("LIVEKIT_AGENT_NAME", "ai-call-agent"),
        ws_url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )


if __name__ == "__main__":
    cli.run_app(build_worker_options())
