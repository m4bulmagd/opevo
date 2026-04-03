"""Verify that the realtime channel prefix stays in sync across all three locations."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent
EXPECTED_PREFIX = "realtime:user:"


def _extract_prefix(file_path: Path) -> str | None:
    for line in file_path.read_text().splitlines():
        if line.startswith("REALTIME_CHANNEL_PREFIX"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def test_realtime_channel_prefix_matches_across_apps():
    api_path = ROOT / "apps" / "api" / "app" / "core" / "redis.py"
    agent_path = ROOT / "apps" / "agent" / "agent" / "event_publisher.py"
    shared_path = ROOT / "libs" / "shared" / "constants.py"

    api_prefix = _extract_prefix(api_path)
    agent_prefix = _extract_prefix(agent_path)
    shared_prefix = _extract_prefix(shared_path)

    assert api_prefix == EXPECTED_PREFIX, f"API prefix mismatch: {api_prefix!r}"
    assert agent_prefix == EXPECTED_PREFIX, f"Agent prefix mismatch: {agent_prefix!r}"
    assert shared_prefix == EXPECTED_PREFIX, f"Shared prefix mismatch: {shared_prefix!r}"
    assert api_prefix == agent_prefix == shared_prefix
