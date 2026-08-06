from dataclasses import dataclass

from agent.config import AgentSettings


class AgentRuntimeConfigurationError(RuntimeError):
    """The LiveKit process does not contain a complete agent runtime."""


@dataclass(slots=True)
class AgentProcessRuntime:
    settings: AgentSettings
    silero_vad: object | None = None


def require_agent_process_runtime(proc: object) -> AgentProcessRuntime:
    runtime = getattr(proc, "userdata", None)
    if not isinstance(runtime, AgentProcessRuntime):
        raise AgentRuntimeConfigurationError(
            "agent process runtime is not initialized"
        )
    return runtime
