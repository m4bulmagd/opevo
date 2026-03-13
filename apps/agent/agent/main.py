import json
import os

from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli

from agent.event_publisher import EventPublisher
from agent.pipeline_factory import build_agent_runtime
from agent.session_runtime import SessionRuntime


async def entrypoint(context: JobContext) -> None:
    metadata = json.loads(context.job.metadata or "{}")
    await context.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)
    await context.wait_for_participant()

    agent, session = build_agent_runtime(metadata)
    runtime = SessionRuntime(EventPublisher())
    await runtime.handle_agent_utterance(metadata, "Bonjour")
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
