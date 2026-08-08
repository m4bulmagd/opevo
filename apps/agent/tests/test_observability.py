import asyncio
import importlib
import logging
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind


def _load_observability():
    module = importlib.import_module("agent.observability")
    return importlib.reload(module)


def test_import_does_not_construct_an_otlp_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentelemetry.exporter.otlp.proto.http import trace_exporter

    constructed: list[bool] = []
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel.example.test")
    monkeypatch.setattr(
        trace_exporter,
        "OTLPSpanExporter",
        lambda: constructed.append(True),
    )

    _load_observability()

    assert constructed == []


def test_no_endpoint_skips_exporter_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observability = _load_observability()
    constructed: list[bool] = []
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.setattr(
        observability,
        "_build_adapter",
        lambda: constructed.append(True),
    )

    assert observability.initialize_observability() is False
    assert constructed == []


@pytest.mark.anyio
async def test_shutdown_resets_a_no_adapter_initialization_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observability = _load_observability()
    adapter = SimpleNamespace(tracer=object(), provider=object())
    constructed: list[bool] = []
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.setattr(
        observability,
        "_build_adapter",
        lambda: constructed.append(True) or adapter,
    )

    assert observability.initialize_observability() is False
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel.example.test")
    assert observability.initialize_observability() is False

    await observability.shutdown_observability(timeout_seconds=0.1)

    assert observability.initialize_observability() is True
    assert constructed == [True]


def test_repeated_initialization_builds_one_private_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observability = _load_observability()
    adapter = SimpleNamespace(tracer=object(), provider=object())
    constructed: list[bool] = []
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel.example.test")
    monkeypatch.setattr(
        observability,
        "_build_adapter",
        lambda: constructed.append(True) or adapter,
    )

    assert observability.initialize_observability() is True
    assert observability.initialize_observability() is True
    assert constructed == [True]


@pytest.mark.anyio
async def test_shutdown_allows_a_later_fresh_private_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observability = _load_observability()
    providers: list[object] = []

    class Provider:
        def __init__(self) -> None:
            self.flush_calls = 0
            self.shutdown_calls = 0

        def force_flush(self, *, timeout_millis: int) -> None:
            self.flush_calls += 1

        def shutdown(self) -> None:
            self.shutdown_calls += 1

    def build_adapter():
        provider = Provider()
        providers.append(provider)
        return SimpleNamespace(tracer=object(), provider=provider)

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel.example.test")
    monkeypatch.setattr(observability, "_build_adapter", build_adapter)

    assert observability.initialize_observability() is True
    await observability.shutdown_observability(timeout_seconds=0.1)
    await observability.shutdown_observability(timeout_seconds=0.1)
    assert observability.initialize_observability() is True

    assert len(providers) == 2
    assert providers[0].flush_calls == 1
    assert providers[0].shutdown_calls == 1
    assert providers[1].flush_calls == 0
    assert providers[1].shutdown_calls == 0


@pytest.mark.anyio
async def test_reinitialization_waits_for_timed_out_old_provider_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observability = _load_observability()
    release_flush = threading.Event()
    flush_started = threading.Event()
    adapters: list[object] = []

    class FirstProvider:
        def force_flush(self, *, timeout_millis: int) -> None:
            flush_started.set()
            release_flush.wait(1.0)

        def shutdown(self) -> None:
            return None

    class LaterProvider:
        def force_flush(self, *, timeout_millis: int) -> None:
            return None

        def shutdown(self) -> None:
            return None

    def build_adapter():
        provider = FirstProvider() if not adapters else LaterProvider()
        adapter = SimpleNamespace(tracer=object(), provider=provider)
        adapters.append(adapter)
        return adapter

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel.example.test")
    monkeypatch.setattr(observability, "_build_adapter", build_adapter)

    assert observability.initialize_observability() is True
    try:
        await observability.shutdown_observability(timeout_seconds=0.05)
        assert flush_started.is_set()
        assert observability.initialize_observability() is False
        assert len(adapters) == 1
    finally:
        release_flush.set()

    for _ in range(100):
        if observability.initialize_observability():
            break
        await asyncio.sleep(0.01)

    assert observability.initialize_observability() is True
    assert len(adapters) == 2


@pytest.mark.timeout(2)
@pytest.mark.anyio
async def test_cancelled_shutdown_finishes_cleanup_before_allowing_reinitialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observability = _load_observability()
    release_flush = threading.Event()
    flush_started = threading.Event()
    shutdown_calls: list[bool] = []
    adapters: list[object] = []

    class FirstProvider:
        def force_flush(self, *, timeout_millis: int) -> None:
            flush_started.set()
            release_flush.wait(1.0)

        def shutdown(self) -> None:
            shutdown_calls.append(True)

    def build_adapter():
        provider = FirstProvider() if not adapters else SimpleNamespace()
        adapter = SimpleNamespace(tracer=object(), provider=provider)
        adapters.append(adapter)
        return adapter

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel.example.test")
    monkeypatch.setattr(observability, "_build_adapter", build_adapter)

    assert observability.initialize_observability() is True
    shutdown_task = asyncio.create_task(
        observability.shutdown_observability(timeout_seconds=0.2)
    )
    for _ in range(50):
        if flush_started.is_set():
            break
        await asyncio.sleep(0.01)
    assert flush_started.is_set()
    shutdown_task.cancel()
    await asyncio.sleep(0)
    release_flush.set()

    with pytest.raises(asyncio.CancelledError):
        await shutdown_task

    assert shutdown_calls == [True]
    assert observability.initialize_observability() is True
    assert len(adapters) == 2


def test_initialization_never_replaces_global_or_livekit_tracer_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livekit.agents import telemetry as livekit_telemetry

    observability = _load_observability()
    original_provider = trace.get_tracer_provider()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel.example.test")
    monkeypatch.setattr(
        observability,
        "_create_exporter",
        lambda: InMemorySpanExporter(),
    )
    monkeypatch.setattr(
        trace,
        "set_tracer_provider",
        lambda *_args, **_kwargs: pytest.fail("global provider was replaced"),
    )
    monkeypatch.setattr(
        livekit_telemetry,
        "set_tracer_provider",
        lambda *_args, **_kwargs: pytest.fail("LiveKit provider was replaced"),
    )

    assert observability.initialize_observability() is True
    assert trace.get_tracer_provider() is original_provider


def test_initialization_failure_is_fail_open_idempotent_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    observability = _load_observability()
    attempts: list[bool] = []
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel.example.test")

    def fail_build():
        attempts.append(True)
        raise RuntimeError("OTLP_CREDENTIAL_TRANSCRIPT_SENTINEL")

    monkeypatch.setattr(observability, "_build_adapter", fail_build)

    with caplog.at_level(logging.ERROR):
        assert observability.initialize_observability() is False
        assert observability.initialize_observability() is False

    assert attempts == [True]
    assert "OTLP_CREDENTIAL_TRANSCRIPT_SENTINEL" not in caplog.text
    assert "event=agent_observability_initialization_failed" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_service_identity_rejects_content_bearing_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observability = _load_observability()
    monkeypatch.setenv(
        "OTEL_SERVICE_NAME",
        "opevo-agent-transcript-secret-sentinel",
    )

    assert observability._service_name() == "opevo-agent"


class _FailingExporter:
    def export(self, _spans):
        raise RuntimeError("EXPORT_PROMPT_SENTINEL")

    def force_flush(self, _timeout_millis=30000):
        raise RuntimeError("FLUSH_AUTHORIZATION_SENTINEL")

    def shutdown(self):
        raise RuntimeError("SHUTDOWN_TRANSCRIPT_SENTINEL")


def test_exporter_failures_return_safe_results_without_rendering_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    observability = _load_observability()
    exporter = observability._SafeSpanExporter(_FailingExporter())

    with caplog.at_level(logging.ERROR):
        assert exporter.export([]) is SpanExportResult.FAILURE
        assert exporter.force_flush(25) is False
        exporter.shutdown()

    for sentinel in (
        "EXPORT_PROMPT_SENTINEL",
        "FLUSH_AUTHORIZATION_SENTINEL",
        "SHUTDOWN_TRANSCRIPT_SENTINEL",
    ):
        assert sentinel not in caplog.text
    assert "event=agent_observability_export_failed" in caplog.text
    assert "event=agent_observability_flush_failed" in caplog.text
    assert "event=agent_observability_shutdown_failed" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_stock_otlp_http_failure_cannot_log_collector_response_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    observability = _load_observability()
    sentinel = "COLLECTOR_PROXY_TRANSCRIPT_SENTINEL"
    delegate = OTLPSpanExporter(endpoint="https://otel.example.test/v1/traces")
    delegate._session.post = lambda **_kwargs: SimpleNamespace(
        ok=False,
        status_code=400,
        text=sentinel,
        reason="Bad Request",
    )
    exporter = observability._SafeSpanExporter(delegate)

    with caplog.at_level(logging.ERROR):
        assert exporter.export([]) is SpanExportResult.FAILURE

    assert sentinel not in caplog.text
    assert "Failed to export span batch" not in caplog.text
    assert "OTLP HTTP exporter diagnostic suppressed" in caplog.text
    assert "event=agent_observability_export_failed" in caplog.text
    assert "operation=export_agent_spans" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_stock_otlp_http_retry_cannot_log_collector_reason(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from opentelemetry.exporter.otlp.proto.http import trace_exporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    observability = _load_observability()
    sentinel = "COLLECTOR_PROXY_REASON_TRANSCRIPT_SENTINEL"
    responses = iter(
        (
            SimpleNamespace(
                ok=False,
                status_code=503,
                text="",
                reason=sentinel,
            ),
            SimpleNamespace(ok=True),
        )
    )
    delegate = OTLPSpanExporter(endpoint="https://otel.example.test/v1/traces")
    delegate._session.post = lambda **_kwargs: next(responses)
    delegate._shutdown_in_progress = SimpleNamespace(wait=lambda _seconds: False)
    monkeypatch.setattr(trace_exporter.random, "uniform", lambda *_args: 0.0)
    exporter = observability._SafeSpanExporter(delegate)

    with caplog.at_level(logging.WARNING):
        assert exporter.export([]) is SpanExportResult.SUCCESS
        trace_exporter._logger.warning("unrelated OpenTelemetry warning")

    assert sentinel not in caplog.text
    assert "Transient error" not in caplog.text
    assert "OTLP HTTP exporter diagnostic suppressed" in caplog.text
    assert "unrelated OpenTelemetry warning" in caplog.text
    assert "event=agent_observability_export_failed" not in caplog.text


class _CapturingSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}
        self.ended = False

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def set_status(self, _status) -> None:
        return None

    def end(self) -> None:
        self.ended = True


class _CapturingTracer:
    def __init__(self) -> None:
        self.started: list[tuple[str, dict, _CapturingSpan]] = []

    def start_span(self, name: str, **kwargs):
        span = _CapturingSpan()
        self.started.append((name, kwargs, span))
        return span


def test_agent_spans_correlate_only_valid_call_ids_without_customer_content() -> None:
    observability = _load_observability()
    tracer = _CapturingTracer()
    observability._adapter = SimpleNamespace(tracer=tracer, provider=object())
    call_id = str(uuid4())

    with observability.agent_lifecycle_span(call_id=call_id, pipeline_mode="sts"):
        pass
    with observability.agent_provider_span(
        provider="livekit",
        operation="session_start",
        call_id=call_id,
    ):
        pass
    with observability.agent_lifecycle_span(
        call_id="CALL_TRANSCRIPT_SENTINEL",
        pipeline_mode="PROMPT_SENTINEL",
    ):
        pass

    assert [item[0] for item in tracer.started] == [
        "opevo.agent.lifecycle",
        "opevo.agent.provider.request",
        "opevo.agent.lifecycle",
    ]
    first_attributes = tracer.started[0][1]["attributes"]
    assert first_attributes == {
        "opevo.call.id": call_id,
        "opevo.agent.pipeline_mode": "sts",
    }
    provider_attributes = tracer.started[1][1]["attributes"]
    assert provider_attributes == {
        "opevo.call.id": call_id,
        "opevo.provider.name": "livekit",
        "opevo.provider.operation": "session_start",
    }
    assert tracer.started[0][1]["kind"] is SpanKind.INTERNAL
    assert tracer.started[1][1]["kind"] is SpanKind.CLIENT
    unsafe_attributes = tracer.started[2][1]["attributes"]
    assert unsafe_attributes == {"opevo.agent.pipeline_mode": "unknown"}
    rendered = repr(tracer.started)
    assert "CALL_TRANSCRIPT_SENTINEL" not in rendered
    assert "PROMPT_SENTINEL" not in rendered
    assert all(item[2].ended for item in tracer.started)
    assert all(item[2].attributes["opevo.outcome"] == "success" for item in tracer.started)


def test_provider_span_error_class_does_not_capture_cleared_exception() -> None:
    observability = _load_observability()
    tracer = _CapturingTracer()
    observability._adapter = SimpleNamespace(tracer=tracer, provider=object())
    sentinel = TimeoutError("PROVIDER_ACTION_SENTINEL")

    with pytest.raises(TimeoutError) as captured:
        with observability.agent_provider_span(
            provider="livekit",
            operation="session_start",
            call_id=str(uuid4()),
        ):
            raise sentinel

    assert captured.value is sentinel
    assert tracer.started[0][2].attributes["opevo.outcome"] == "error"
    assert tracer.started[0][2].attributes["opevo.error.class"] == "timeout"


def test_span_recording_failure_does_not_change_application_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    observability = _load_observability()

    class FailingTracer:
        def start_span(self, *_args, **_kwargs):
            raise RuntimeError("SPAN_RECORDING_BODY_SENTINEL")

    observability._adapter = SimpleNamespace(tracer=FailingTracer(), provider=object())

    with caplog.at_level(logging.ERROR):
        with observability.agent_lifecycle_span(
            call_id=str(uuid4()),
            pipeline_mode="stt_llm_tts",
        ):
            result = 42

    assert result == 42
    assert "SPAN_RECORDING_BODY_SENTINEL" not in caplog.text
    assert "event=agent_observability_span_failed" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_safe_reporting_failure_cannot_change_span_or_export_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observability = _load_observability()

    class FailingTracer:
        def start_span(self, *_args, **_kwargs):
            raise RuntimeError("span failed")

    monkeypatch.setattr(
        observability,
        "report_safe_exception",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("logger failed")),
    )
    observability._adapter = SimpleNamespace(tracer=FailingTracer(), provider=object())
    exporter = observability._SafeSpanExporter(_FailingExporter())

    with observability.agent_lifecycle_span(
        call_id=str(uuid4()),
        pipeline_mode="sts",
    ):
        result = 42

    assert result == 42
    assert exporter.export([]) is SpanExportResult.FAILURE


@pytest.mark.anyio
async def test_provider_action_reports_worker_exception_after_except_scope_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observability = _load_observability()
    sentinel = RuntimeError("PROVIDER_ACTION_SENTINEL")
    reported: list[BaseException] = []
    monkeypatch.setattr(
        observability,
        "_report_failure",
        lambda **kwargs: reported.append(kwargs["error"]),
    )

    def fail_after_thread_handoff() -> None:
        raise sentinel

    completion = await observability._run_provider_action(
        event="provider_action_failed",
        operation="run_provider_action",
        action=fail_after_thread_handoff,
        deadline=observability.monotonic() + 1,
    )

    assert completion.is_set()
    assert reported == [sentinel]


@pytest.mark.anyio
async def test_flush_and_shutdown_failures_are_bounded_and_redacted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    observability = _load_observability()

    class FailingProvider:
        def force_flush(self, *, timeout_millis: int):
            raise RuntimeError("PROVIDER_FLUSH_CREDENTIAL_SENTINEL")

        def shutdown(self):
            raise RuntimeError("PROVIDER_SHUTDOWN_PROMPT_SENTINEL")

    observability._adapter = SimpleNamespace(
        tracer=object(),
        provider=FailingProvider(),
    )

    with caplog.at_level(logging.ERROR):
        await observability.shutdown_observability(timeout_seconds=0.1)

    assert "PROVIDER_FLUSH_CREDENTIAL_SENTINEL" not in caplog.text
    assert "PROVIDER_SHUTDOWN_PROMPT_SENTINEL" not in caplog.text
    assert "event=agent_observability_provider_flush_failed" in caplog.text
    assert "event=agent_observability_provider_shutdown_failed" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.anyio
async def test_blocking_flush_uses_a_daemon_worker_and_still_attempts_shutdown() -> None:
    observability = _load_observability()
    release_flush = threading.Event()
    flush_threads: list[threading.Thread] = []
    shutdown_calls: list[bool] = []

    class BlockingProvider:
        def force_flush(self, *, timeout_millis: int):
            flush_threads.append(threading.current_thread())
            release_flush.wait(1.0)

        def shutdown(self):
            shutdown_calls.append(True)

    observability._adapter = SimpleNamespace(
        tracer=object(),
        provider=BlockingProvider(),
    )

    try:
        await observability.shutdown_observability(timeout_seconds=0.05)

        assert len(flush_threads) == 1
        assert flush_threads[0].daemon is True
        assert shutdown_calls == [True]
    finally:
        release_flush.set()
