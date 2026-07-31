import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent.main as agent_main


FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "libs/shared/tests/fixtures/v1"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / name).read_text())


def _customer_metadata(**overrides: object) -> dict[str, object]:
    metadata = _fixture("customer_call_dispatch.json")
    metadata.update(overrides)
    return metadata


def _verification_metadata(**overrides: object) -> dict[str, object]:
    metadata = _fixture("forwarding_verification_dispatch.json")
    metadata.update(overrides)
    return metadata


class FakeJobRequest:
    def __init__(self, metadata: object) -> None:
        self.job = SimpleNamespace(metadata=metadata)
        self.accepted: list[dict[str, object]] = []
        self.rejected: list[dict[str, object]] = []

    async def accept(self, **kwargs: object) -> None:
        self.accepted.append(kwargs)

    async def reject(self, **kwargs: object) -> None:
        self.rejected.append(kwargs)


class FakeJobContext:
    def __init__(self, metadata: str) -> None:
        self.job = SimpleNamespace(metadata=metadata)
        self.shutdown_callbacks: list[object] = []

    def add_shutdown_callback(self, callback: object) -> None:
        self.shutdown_callbacks.append(callback)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "expected_name"),
    [
        (_customer_metadata(), "Fixture Agent"),
        (_verification_metadata(), "Presvo forwarding verification"),
    ],
)
async def test_job_request_accepts_versioned_shared_dispatch_fixtures(
    payload: dict[str, object], expected_name: str
) -> None:
    request = FakeJobRequest(json.dumps(payload))

    await agent_main.handle_job_request(request)

    assert request.rejected == []
    assert request.accepted == [
        {"name": expected_name, "identity": payload["agent_identity"]}
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        _customer_metadata(schema_version=None),
        _customer_metadata(job_type=None),
        _customer_metadata(job_type="unknown"),
        "{not-json",
    ],
)
async def test_job_request_rejects_invalid_versioned_dispatches(
    payload: dict[str, object] | str,
) -> None:
    if isinstance(payload, dict):
        if payload.get("schema_version") is None:
            payload.pop("schema_version")
        if payload.get("job_type") is None:
            payload.pop("job_type")
        payload = json.dumps(payload)
    request = FakeJobRequest(payload)

    await agent_main.handle_job_request(request)

    assert request.accepted == []
    assert request.rejected == [{"terminate": True}]


@pytest.mark.anyio
async def test_job_request_tolerates_additive_shared_dispatch_fields() -> None:
    payload = _customer_metadata(future_field="accepted")
    request = FakeJobRequest(json.dumps(payload))

    await agent_main.handle_job_request(request)

    assert request.rejected == []
    assert request.accepted == [
        {"name": "Fixture Agent", "identity": payload["agent_identity"]}
    ]


@pytest.mark.anyio
async def test_job_request_redacts_malformed_dispatch_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "TOKEN_SENTINEL"
    prompt = "PROMPT_SENTINEL"
    request = FakeJobRequest(
        json.dumps(_customer_metadata(dispatch_token=token, system_prompt=prompt))[:-1]
    )

    with caplog.at_level(logging.WARNING):
        await agent_main.handle_job_request(request)

    assert request.rejected == [{"terminate": True}]
    assert token not in caplog.text
    assert prompt not in caplog.text


@pytest.mark.anyio
async def test_entrypoint_parses_versioned_verification_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = FakeJobContext(json.dumps(_verification_metadata()))
    calls: list[object] = []

    async def capture_verification(resolved_context: object, metadata: object) -> None:
        calls.append((resolved_context, metadata))

    monkeypatch.setattr(agent_main, "_initialize_observability_safely", lambda: None)
    monkeypatch.setattr(agent_main, "shutdown_observability", lambda: None)
    monkeypatch.setattr(agent_main, "run_forwarding_verification", capture_verification)

    await agent_main.entrypoint(context)

    assert calls and calls[0][0] is context
