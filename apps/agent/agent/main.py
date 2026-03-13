import json
import os

from livekit.agents import AgentServer, JobContext, WorkerOptions, cli

from agent.event_publisher import EventPublisher
from agent.session_runtime import SessionRuntime


async def entrypoint(context: JobContext) -> None:
    metadata = json.loads(context.job.metadata or "{}")
    runtime = SessionRuntime(EventPublisher())
    await runtime.handle_agent_utterance(metadata, "Bonjour")
    await runtime.finalize(metadata, duration_seconds=0)


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
