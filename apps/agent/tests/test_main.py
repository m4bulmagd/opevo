from agent.main import build_worker_options
from pathlib import Path


def test_build_worker_options_sets_prewarm_hook() -> None:
    options = build_worker_options()

    assert options.prewarm_fnc is not None
    assert options.prewarm_fnc.__name__ == "prewarm_assets"


def test_agent_env_example_documents_debug_stream_flag() -> None:
    env_example = (
        Path(__file__).resolve().parents[1] / ".env.example"
    ).read_text()

    assert "AGENT_DEBUG_STREAMS=false" in env_example
    assert "AGENT_MIN_ENDPOINTING_DELAY=0.25" in env_example
    assert "AGENT_MAX_ENDPOINTING_DELAY=1.5" in env_example
