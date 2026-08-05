import ast
from pathlib import Path
from uuid import uuid4

import pytest

from app.workers.call_finalization_queue import CallFinalizationQueue
from app.workers.queueing import enqueue_outbox_wakeup


class CapturePool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict]] = []

    async def enqueue_job(self, name: str, payload: dict, **kwargs) -> None:
        self.calls.append((name, payload, kwargs))


@pytest.mark.anyio
async def test_background_wakeup_uses_fixed_background_queue() -> None:
    pool = CapturePool()

    await enqueue_outbox_wakeup(pool)

    assert pool.calls == [
        ("outbox_delivery_job", {}, {"_queue_name": "arq:queue:background"})
    ]


@pytest.mark.anyio
async def test_call_finalization_keeps_id_and_uses_lifecycle_queue() -> None:
    pool = CapturePool()
    call_id = uuid4()

    job_id = await CallFinalizationQueue(pool).enqueue({"call_id": str(call_id)})

    assert job_id == f"call-finalization:{call_id}"
    assert pool.calls == [
        (
            "call_finalization_job",
            {"call_id": str(call_id)},
            {"_job_id": job_id, "_queue_name": "arq:queue"},
        )
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"call_id": str(uuid4()), "transcript": "PRIVATE_TRANSCRIPT"},
    ],
)
async def test_call_finalization_rejects_noncanonical_payloads_without_enqueue(
    payload: object,
) -> None:
    """Malformed or expanded payloads must not cross the lifecycle queue boundary."""
    pool = CapturePool()

    with pytest.raises(ValueError, match="must contain call_id only"):
        await CallFinalizationQueue(pool).enqueue(payload)  # type: ignore[arg-type]

    assert pool.calls == []


def _enqueue_job_literals(source: Path) -> list[str]:
    tree = ast.parse(source.read_text())
    literals: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "enqueue_job"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            continue
        literals.append(node.args[0].value)
    return literals


def test_enqueue_job_ownership_is_limited_to_queue_seams() -> None:
    app_directory = Path(__file__).resolve().parents[2] / "app"
    owners = {
        "outbox_delivery_job": Path("workers/queueing.py"),
        "call_finalization_job": Path("workers/call_finalization_queue.py"),
    }

    for job_name, expected_owner in owners.items():
        actual_owners = {
            source.relative_to(app_directory)
            for source in app_directory.rglob("*.py")
            if job_name in _enqueue_job_literals(source)
        }
        assert actual_owners == {expected_owner}
