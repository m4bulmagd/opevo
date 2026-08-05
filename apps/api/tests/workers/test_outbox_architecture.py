from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.outbox_service import (
    REFERENCE_PAYLOAD_FIELDS,
    SUPPORTED_OUTBOX_TOPICS,
)
from app.workers.outbox import delivery
from app.workers.outbox.delivery import get_default_outbox_handlers
from app.workers.outbox.registry import DEFAULT_OUTBOX_HANDLERS


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
            "if loaded:",
            "    raise SystemExit(f'Eager topic providers: {sorted(loaded)}')",
        )
    )

    result = subprocess.run(
        [sys.executable, "-E", "-c", script],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_registry_exactly_matches_payload_schema_topics() -> None:
    assert SUPPORTED_OUTBOX_TOPICS == frozenset(REFERENCE_PAYLOAD_FIELDS)
    assert frozenset(DEFAULT_OUTBOX_HANDLERS) == SUPPORTED_OUTBOX_TOPICS
    assert all(callable(handler) for handler in DEFAULT_OUTBOX_HANDLERS.values())


def test_default_handler_lookup_returns_the_explicit_registry() -> None:
    assert get_default_outbox_handlers() is DEFAULT_OUTBOX_HANDLERS


def test_worker_settings_imports_with_the_complete_registry() -> None:
    script = """
from app.services.outbox_service import SUPPORTED_OUTBOX_TOPICS
from app.workers import arq_worker
from app.workers.outbox.registry import DEFAULT_OUTBOX_HANDLERS

if arq_worker.on_background_startup is None:
    raise SystemExit("Background worker startup hook is missing")
if frozenset(DEFAULT_OUTBOX_HANDLERS) != SUPPORTED_OUTBOX_TOPICS:
    raise SystemExit("Default outbox registry is incomplete")
"""

    completed = subprocess.run(
        [sys.executable, "-E", "-c", script],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    if completed.returncode != 0:
        pytest.fail(completed.stderr)


@pytest.mark.anyio
async def test_injected_handlers_bypass_default_registry(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_default_lookup():
        pytest.fail("default registry must not load when handlers are injected")

    monkeypatch.setattr(
        delivery,
        "get_default_outbox_handlers",
        fail_default_lookup,
    )
    session_factory = async_sessionmaker(
        db_session.bind,
        expire_on_commit=False,
    )

    result = await delivery.outbox_delivery_job(
        {
            "session_factory": session_factory,
            "outbox_handlers": {},
        }
    )

    assert result == {
        "claimed": 0,
        "delivered": 0,
        "retried": 0,
        "failed": 0,
    }
