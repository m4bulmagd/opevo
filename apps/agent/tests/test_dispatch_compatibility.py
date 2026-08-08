import json
import logging
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from opevo_contracts import ContractError, VersionedContract, dump_contract

import agent.main as agent_main
from agent.composition import (
    build_agent_process_runtime,
    publish_agent_process_runtime,
    require_agent_process_runtime,
)
from agent.config import AgentSettings


FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "libs/shared/tests/fixtures/v1"
TEST_SETTINGS = AgentSettings(
    speechmatics_api_key="speechmatics-test-key",
    gemini_api_key="gemini-test-key",
    livekit_silero_vad_enabled=False,
    livekit_turn_detector_enabled=False,
)


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / name).read_text())


def _fixture_text(name: str) -> str:
    return (FIXTURE_ROOT / name).read_text()


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
        self.proc = SimpleNamespace(userdata={})
        publish_agent_process_runtime(
            self.proc,
            build_agent_process_runtime(TEST_SETTINGS),
        )
        self.room = object()
        self.events: list[object] = []

    def add_shutdown_callback(self, callback: object) -> None:
        self.shutdown_callbacks.append(callback)

    async def connect(self, **kwargs: object) -> None:
        self.events.append(("connect", kwargs))

    async def wait_for_participant(self, **kwargs: object) -> object:
        self.events.append(("wait_for_participant", kwargs))
        return SimpleNamespace(identity="sip-caller")

    def shutdown(self, reason: str) -> None:
        self.events.append(("shutdown", reason))


class FakeSession:
    def __init__(self) -> None:
        self.input = SimpleNamespace(set_audio_enabled=lambda _enabled: None)
        self.handlers: dict[str, object] = {}

    def on(self, event_name: str, callback: object) -> None:
        self.handlers[event_name] = callback

    async def start(self, **_kwargs: object) -> None:
        return None

    async def say(self, _text: str, **_kwargs: object) -> None:
        return None


class FakeRuntime:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.call_limit_expired_on_start = False
        self.call_limit_task = None

    def create_handler_task(self, _factory: object) -> bool:
        return True

    def enforce_call_limit(self, _metadata: object, _disconnect: object) -> None:
        return None

    async def handle_caller_transcript(self, *_args: object) -> None:
        return None

    async def handle_agent_utterance(self, *_args: object) -> None:
        return None

    async def finalize(self, *_args: object, **_kwargs: object) -> None:
        return None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("fixture_name", "expected_name", "expected_identity"),
    [
        (
            "customer_call_dispatch.json",
            "Fixture Agent",
            "agent-call-11111111-1111-4111-8111-111111111111",
        ),
        (
            "forwarding_verification_dispatch.json",
            "Opevo forwarding verification",
            "agent-verification-44444444-4444-4444-8444-444444444444",
        ),
    ],
)
async def test_job_request_accepts_exact_shared_dispatch_artifacts(
    fixture_name: str, expected_name: str, expected_identity: str
) -> None:
    request = FakeJobRequest(_fixture_text(fixture_name))

    await agent_main.handle_job_request(request)

    assert request.rejected == []
    assert request.accepted == [
        {"name": expected_name, "identity": expected_identity}
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
@pytest.mark.parametrize(
    "fixture_name",
    ["customer_call_dispatch.json", "forwarding_verification_dispatch.json"],
)
async def test_entrypoint_parses_exact_shared_dispatch_artifacts(
    fixture_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_fixture = _fixture_text(fixture_name)
    expected = json.loads(raw_fixture)
    context = FakeJobContext(raw_fixture)
    parsed_payloads: list[dict[str, object]] = []
    session = FakeSession()

    async def capture_verification(
        resolved_context: object,
        metadata: object,
        *,
        settings: AgentSettings,
        api_client: object,
    ) -> None:
        assert resolved_context is context
        assert settings is TEST_SETTINGS
        assert api_client is require_agent_process_runtime(context.proc).api_client
        parsed_payloads.append(dump_contract(cast(VersionedContract, metadata)))

    def capture_customer(
        metadata: dict[str, object], **_kwargs: object
    ) -> tuple[object, FakeSession]:
        parsed_payloads.append(metadata)
        return object(), session

    monkeypatch.setattr(agent_main, "_initialize_observability_safely", lambda: None)
    monkeypatch.setattr(agent_main, "shutdown_observability", lambda: None)
    monkeypatch.setattr(agent_main, "run_forwarding_verification", capture_verification)
    monkeypatch.setattr(agent_main, "agent_lifecycle_span", lambda **_kwargs: nullcontext())
    monkeypatch.setattr(agent_main, "agent_provider_span", lambda **_kwargs: nullcontext())

    await agent_main.entrypoint(
        context,
        agent_runtime_factory=capture_customer,
        session_runtime_factory=FakeRuntime,
    )

    assert parsed_payloads == [expected]


def _invalid_dispatch_artifacts() -> list[tuple[str, str]]:
    unsupported_version = _customer_metadata(schema_version=2)
    bad_customer_uuid = _customer_metadata(call_id="BAD_CUSTOMER_UUID_SENTINEL")
    bad_verification_uuid = _verification_metadata(
        verification_session_id="BAD_VERIFICATION_UUID_SENTINEL"
    )
    missing_discriminator = _customer_metadata()
    missing_discriminator.pop("job_type")
    unknown_discriminator = _customer_metadata(job_type="UNKNOWN_JOB_SENTINEL")
    return [
        (json.dumps(unsupported_version), "unsupported_schema_version"),
        (json.dumps(bad_customer_uuid), "invalid_payload"),
        (json.dumps(bad_verification_uuid), "invalid_payload"),
        ("{MALFORMED_JSON_SENTINEL", "malformed_json"),
        (json.dumps(missing_discriminator), "invalid_payload"),
        (json.dumps(unknown_discriminator), "invalid_payload"),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(("raw_metadata", "expected_code"), _invalid_dispatch_artifacts())
async def test_job_request_rejects_invalid_artifacts_with_safe_logs(
    raw_metadata: str,
    expected_code: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = FakeJobRequest(raw_metadata)

    with caplog.at_level(logging.WARNING):
        await agent_main.handle_job_request(request)

    assert request.accepted == []
    assert request.rejected == [{"terminate": True}]
    assert caplog.messages == [
        "job_request_rejected contract_name=DispatchContract "
        f"code={expected_code} transport=livekit"
    ]
    for sentinel in (
        "fixture-dispatch-token",
        "fixture-completion-token",
        "Help the fixture caller clearly.",
        "BAD_CUSTOMER_UUID_SENTINEL",
        "BAD_VERIFICATION_UUID_SENTINEL",
        "MALFORMED_JSON_SENTINEL",
        "UNKNOWN_JOB_SENTINEL",
    ):
        assert sentinel not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(("raw_metadata", "expected_code"), _invalid_dispatch_artifacts())
async def test_entrypoint_rejects_invalid_artifacts_with_safe_contract_error(
    raw_metadata: str,
    expected_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = FakeJobContext(raw_metadata)
    monkeypatch.setattr(agent_main, "_initialize_observability_safely", lambda: None)
    monkeypatch.setattr(agent_main, "shutdown_observability", lambda: None)

    with pytest.raises(ContractError) as caught:
        await agent_main.entrypoint(context)

    assert caught.value.contract_name == "DispatchContract"
    assert caught.value.code == expected_code
    assert str(caught.value) == f"DispatchContract rejected: {expected_code}"
    for sentinel in (
        "fixture-dispatch-token",
        "fixture-completion-token",
        "Help the fixture caller clearly.",
        "BAD_CUSTOMER_UUID_SENTINEL",
        "BAD_VERIFICATION_UUID_SENTINEL",
        "MALFORMED_JSON_SENTINEL",
        "UNKNOWN_JOB_SENTINEL",
    ):
        assert sentinel not in str(caught.value)
