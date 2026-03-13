from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path


WATCH_ROOT = Path("/app/agent")
COMMAND = ["uv", "run", "python", "-m", "agent.main", "start"]
POLL_INTERVAL = float(os.getenv("AGENT_DEV_POLL_INTERVAL", "0.5"))
IGNORE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv"}


def _snapshot_python_files() -> dict[str, int]:
    snapshot: dict[str, int] = {}
    for path in WATCH_ROOT.rglob("*.py"):
        if any(part in IGNORE_DIR_NAMES for part in path.parts):
            continue
        try:
            snapshot[str(path)] = path.stat().st_mtime_ns
        except FileNotFoundError:
            continue
    return snapshot


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    current_snapshot = _snapshot_python_files()
    process = subprocess.Popen(COMMAND)

    try:
        while True:
            time.sleep(POLL_INTERVAL)
            next_snapshot = _snapshot_python_files()

            if next_snapshot != current_snapshot:
                print("agent source change detected, restarting worker", flush=True)
                current_snapshot = next_snapshot
                _terminate_process(process)
                process = subprocess.Popen(COMMAND)
                continue

            if process.poll() is not None:
                print(f"agent worker exited with code {process.returncode}, restarting", flush=True)
                process = subprocess.Popen(COMMAND)
    except KeyboardInterrupt:
        return 0
    finally:
        _terminate_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
