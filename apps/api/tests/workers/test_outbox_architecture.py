import os
from pathlib import Path
import subprocess
import sys


API_ROOT = Path(__file__).resolve().parents[2]


def test_delivery_import_does_not_eagerly_import_topic_providers() -> None:
    script = "\n".join(
        (
            "import sys",
            "import app.workers.outbox.delivery",
            "forbidden = {",
            "    'app.providers.livekit_dispatch.livekit',",
            "    'app.providers.summaries.gemini',",
            "    'app.providers.telephony.factory',",
            "}",
            "loaded = forbidden.intersection(sys.modules)",
            "assert not loaded, sorted(loaded)",
        )
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(API_ROOT)

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=API_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
