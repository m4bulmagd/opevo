import logging
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from tests.fakes import CaptureMeter, CaptureTracer


@pytest.mark.parametrize(
    ("trace_endpoint", "metric_endpoint", "expected_exporters"),
    [
        (
            "https://collector.example/v1/traces",
            "https://collector.example/v1/metrics",
            {"trace", "metric"},
        ),
        ("https://collector.example/v1/traces", None, {"trace"}),
        (None, "https://collector.example/v1/metrics", {"metric"}),
        (None, None, set()),
    ],
)
def test_isolated_worker_components_preserve_identity_and_optional_exporters(
    monkeypatch: pytest.MonkeyPatch,
    trace_endpoint: str | None,
    metric_endpoint: str | None,
    expected_exporters: set[str],
) -> None:
    """A worker must export under its own identity and only configured signals."""
    from opentelemetry.exporter.otlp.proto.http import metric_exporter
    from opentelemetry.exporter.otlp.proto.http import trace_exporter
    from opentelemetry.sdk import metrics as sdk_metrics
    from opentelemetry.sdk import resources as sdk_resources
    from opentelemetry.sdk import trace as sdk_trace
    from opentelemetry.sdk.metrics import export as metric_export
    from opentelemetry.sdk.trace import export as trace_export

    from app.core.observability import (
        SafeMetricExporter,
        SafeSpanExporter,
        _build_components,
    )

    created: dict[str, list[object]] = {
        "metric_exporters": [],
        "metric_readers": [],
        "span_exporters": [],
        "span_processors": [],
    }

    class FakeResource:
        @classmethod
        def create(cls, attributes: dict[str, str]) -> dict[str, str]:
            return dict(attributes)

    class FakeSpanExporter:
        def __init__(self, *, endpoint: str) -> None:
            self.endpoint = endpoint
            created["span_exporters"].append(self)

    class FakeMetricExporter:
        def __init__(self, *, endpoint: str) -> None:
            self.endpoint = endpoint
            self._preferred_temporality = None
            self._preferred_aggregation = None
            created["metric_exporters"].append(self)

    class FakeSpanProcessor:
        def __init__(self, exporter: SafeSpanExporter) -> None:
            self.exporter = exporter
            created["span_processors"].append(self)

    class FakeMetricReader:
        def __init__(self, exporter: SafeMetricExporter) -> None:
            self.exporter = exporter
            created["metric_readers"].append(self)

    class FakeTracerProvider:
        def __init__(self, *, resource: dict[str, str]) -> None:
            self.resource = resource
            self.processors: list[FakeSpanProcessor] = []

        def add_span_processor(self, processor: FakeSpanProcessor) -> None:
            self.processors.append(processor)

        def get_tracer(self, name: str) -> tuple[str, str]:
            return ("tracer", name)

    class FakeMeterProvider:
        def __init__(
            self,
            *,
            resource: dict[str, str],
            metric_readers: list[FakeMetricReader],
        ) -> None:
            self.resource = resource
            self.metric_readers = metric_readers

        def get_meter(self, name: str) -> tuple[str, str]:
            return ("meter", name)

    monkeypatch.setattr(sdk_resources, "Resource", FakeResource)
    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", FakeSpanExporter)
    monkeypatch.setattr(metric_exporter, "OTLPMetricExporter", FakeMetricExporter)
    monkeypatch.setattr(trace_export, "BatchSpanProcessor", FakeSpanProcessor)
    monkeypatch.setattr(
        metric_export,
        "PeriodicExportingMetricReader",
        FakeMetricReader,
    )
    monkeypatch.setattr(sdk_trace, "TracerProvider", FakeTracerProvider)
    monkeypatch.setattr(sdk_metrics, "MeterProvider", FakeMeterProvider)

    meter, tracer, lifecycle = _build_components(
        "presvo-worker-call-lifecycle",
        trace_endpoint,
        metric_endpoint,
    )

    assert meter == ("meter", "presvo")
    assert tracer == ("tracer", "presvo")
    assert lifecycle.tracer_provider.resource == {
        "service.name": "presvo-worker-call-lifecycle"
    }
    assert lifecycle.meter_provider.resource == {
        "service.name": "presvo-worker-call-lifecycle"
    }
    actual_exporters = {
        *("trace" for _exporter in created["span_exporters"]),
        *("metric" for _exporter in created["metric_exporters"]),
    }
    assert actual_exporters == expected_exporters
    if trace_endpoint is not None:
        processor = lifecycle.tracer_provider.processors[0]
        assert isinstance(processor.exporter, SafeSpanExporter)
        assert processor.exporter.delegate.endpoint == trace_endpoint
    else:
        assert lifecycle.tracer_provider.processors == []
    if metric_endpoint is not None:
        reader = lifecycle.meter_provider.metric_readers[0]
        assert isinstance(reader.exporter, SafeMetricExporter)
        assert reader.exporter.delegate.endpoint == metric_endpoint
    else:
        assert lifecycle.meter_provider.metric_readers == []


def test_worker_signal_lifecycle_flushes_and_shuts_down_both_signals() -> None:
    """Neither signal provider may be omitted from worker cleanup."""
    from app.core.observability import _Lifecycle

    class Provider:
        def __init__(self, name: str) -> None:
            self.name = name
            self.calls: list[tuple[str, int | None]] = []

        def force_flush(self, *, timeout_millis: int) -> bool:
            self.calls.append(("flush", timeout_millis))
            return True

        def shutdown(self) -> None:
            self.calls.append(("shutdown", None))

    trace_provider = Provider("traces")
    metric_provider = Provider("metrics")
    lifecycle = _Lifecycle(trace_provider, metric_provider)

    assert lifecycle.force_flush() is True
    assert lifecycle.shutdown() is True

    assert trace_provider.calls == [("flush", 750), ("shutdown", None)]
    assert metric_provider.calls == [("flush", 750), ("shutdown", None)]


def test_worker_observability_shutdown_propagates_real_signal_failure(caplog) -> None:
    """Public worker shutdown must report a failure from either real signal path."""
    from app.core.observability import Observability, _Lifecycle

    calls: list[str] = []

    class FailingTraceProvider:
        def shutdown(self) -> None:
            calls.append("traces")
            raise RuntimeError("PRIVATE_TRACE_SHUTDOWN_DETAIL")

    class RejectingMetricProvider:
        def shutdown(self) -> bool:
            calls.append("metrics")
            return False

    telemetry = Observability(
        meter=CaptureMeter(),
        tracer=CaptureTracer(),
        lifecycle=_Lifecycle(FailingTraceProvider(), RejectingMetricProvider()),
    )

    with caplog.at_level(logging.WARNING):
        result = telemetry.shutdown()

    assert result is False
    assert set(calls) == {"traces", "metrics"}
    assert caplog.record_tuples == [
        (
            "app.core.observability",
            logging.WARNING,
            "event=observability_shutdown_failed operation=shutdown_traces "
            "error_type=RuntimeError status=failed",
        )
    ]
    assert "PRIVATE_TRACE_SHUTDOWN_DETAIL" not in caplog.text


def test_worker_exporters_preserve_success_and_isolate_cleanup_failures(caplog) -> None:
    """Exporter cleanup faults must not change successful worker exports."""
    from opentelemetry.sdk.metrics.export import MetricExportResult
    from opentelemetry.sdk.trace.export import SpanExportResult

    from app.core.observability import SafeMetricExporter, SafeSpanExporter

    class SpanDelegate:
        def export(self, _spans) -> SpanExportResult:
            return SpanExportResult.SUCCESS

        def force_flush(self, *, timeout_millis: int) -> bool:
            assert timeout_millis == 123
            raise RuntimeError("PRIVATE_TRACE_CLEANUP_DETAIL")

        def shutdown(self) -> None:
            raise RuntimeError("PRIVATE_TRACE_SHUTDOWN_DETAIL")

    class MetricDelegate:
        _preferred_temporality = None
        _preferred_aggregation = None

        def export(
            self,
            _metrics_data,
            *,
            timeout_millis: float,
            marker: str,
        ) -> MetricExportResult:
            assert timeout_millis == 456
            assert marker == "kept"
            return MetricExportResult.SUCCESS

        def force_flush(self, *, timeout_millis: float) -> bool:
            assert timeout_millis == 234
            raise RuntimeError("PRIVATE_METRIC_CLEANUP_DETAIL")

        def shutdown(self, *, timeout_millis: float, marker: str) -> None:
            assert timeout_millis == 567
            assert marker == "kept"
            raise RuntimeError("PRIVATE_METRIC_SHUTDOWN_DETAIL")

    span_exporter = SafeSpanExporter(SpanDelegate())
    metric_exporter = SafeMetricExporter(MetricDelegate())

    with caplog.at_level(logging.WARNING):
        assert span_exporter.export([]) is SpanExportResult.SUCCESS
        assert (
            metric_exporter.export(None, timeout_millis=456, marker="kept")
            is MetricExportResult.SUCCESS
        )
        assert span_exporter.force_flush(timeout_millis=123) is False
        assert metric_exporter.force_flush(timeout_millis=234) is False
        span_exporter.shutdown()
        metric_exporter.shutdown(timeout_millis=567, marker="kept")

    assert caplog.text.count("event=observability_flush_failed") == 2
    assert caplog.text.count("event=observability_shutdown_failed") == 2
    assert "PRIVATE_TRACE_CLEANUP_DETAIL" not in caplog.text
    assert "PRIVATE_TRACE_SHUTDOWN_DETAIL" not in caplog.text
    assert "PRIVATE_METRIC_CLEANUP_DETAIL" not in caplog.text
    assert "PRIVATE_METRIC_SHUTDOWN_DETAIL" not in caplog.text


def test_worker_signal_actions_do_not_wait_for_a_stalled_provider(caplog) -> None:
    """A stalled exporter cannot hold an isolated worker past its cleanup bound."""
    from app.core.observability import _run_independent_signal_actions

    release = threading.Event()
    stalled_signal_started = threading.Event()
    stalled_signal_finished = threading.Event()
    fast_signal_finished = threading.Event()
    helper_finished = threading.Event()
    results: list[bool] = []

    def stalled_trace() -> None:
        stalled_signal_started.set()
        try:
            release.wait()
        finally:
            stalled_signal_finished.set()

    def fast_metric() -> None:
        fast_signal_finished.set()

    def run_actions() -> None:
        try:
            results.append(
                _run_independent_signal_actions(
                    (("traces", stalled_trace), ("metrics", fast_metric)),
                    event="observability_shutdown_failed",
                    operation_prefix="shutdown",
                    timeout_seconds=0.02,
                )
            )
        finally:
            helper_finished.set()

    controller = threading.Thread(target=run_actions, daemon=True)
    try:
        with caplog.at_level(logging.WARNING):
            controller.start()
            assert stalled_signal_started.wait(timeout=1)
            assert fast_signal_finished.wait(timeout=1)
            # This is a condition wait with 25x headroom over the 20ms bound.
            assert helper_finished.wait(timeout=0.5)
    finally:
        release.set()

    assert stalled_signal_finished.wait(timeout=1)
    controller.join(timeout=1)
    assert not controller.is_alive()
    assert results == [False]
    assert "event=observability_shutdown_failed" in caplog.text


def test_lifecycle_worker_snapshot_exports_only_bounded_call_states() -> None:
    """Call reconciliation must not turn database values into metric labels."""
    from app.core.observability import Observability

    meter = CaptureMeter()
    telemetry = Observability(meter=meter, tracer=CaptureTracer())

    telemetry.record_call_snapshot(
        SimpleNamespace(
            current={"pending": 2, "PRIVATE_CURRENT_STATE": 99},
            stale={"finalizing": 1, "PRIVATE_STALE_STATE": 88},
        )
    )

    assert meter.instruments["presvo.calls.current"].measurements == [
        (2, {"state": "pending"})
    ]
    assert meter.instruments["presvo.calls.stale"].measurements == [
        (1, {"state": "finalizing"})
    ]
    assert "PRIVATE_CURRENT_STATE" not in repr(meter.instruments)
    assert "PRIVATE_STALE_STATE" not in repr(meter.instruments)


def test_background_worker_snapshot_exports_only_bounded_outbox_statuses() -> None:
    """Background reconciliation must not turn database values into metric labels."""
    from app.core.observability import Observability

    meter = CaptureMeter()
    telemetry = Observability(meter=meter, tracer=CaptureTracer())

    telemetry.record_outbox_snapshot(
        SimpleNamespace(
            counts={"pending": 3, "PRIVATE_OUTBOX_STATUS": 77},
            oldest_unfinished_age_seconds=12.5,
        )
    )

    assert meter.instruments["presvo.outbox.events"].measurements == [
        (3, {"status": "pending"})
    ]
    assert meter.instruments["presvo.outbox.oldest_unfinished.age"].measurements == [
        (12.5, {})
    ]
    assert "PRIVATE_OUTBOX_STATUS" not in repr(meter.instruments)


@pytest.mark.anyio
async def test_worker_job_reference_skips_untrusted_candidates_before_payload_keyword(
) -> None:
    """Only the first valid UUID may become a lifecycle worker span attribute."""
    from app.core.observability import Observability, instrument_job

    call_id = str(uuid4())
    tracer = CaptureTracer()
    telemetry = Observability(meter=CaptureMeter(), tracer=tracer)
    observed_contexts: list[dict] = []

    def get_observability(ctx: dict) -> Observability:
        observed_contexts.append(ctx)
        return telemetry

    @instrument_job(
        "call_finalization",
        queue_class="call_lifecycle",
        observability_getter=get_observability,
    )
    async def job(_ctx: dict, *_args, payload: dict) -> str:
        return payload["call_id"]

    ctx = {
        "enqueue_time": datetime.now(UTC),
        "job_try": 1,
    }
    result = await job(
        ctx,
        object(),
        {"not_call_id": "PRIVATE_FIELD"},
        {"call_id": "PRIVATE_INVALID_CALL_ID"},
        payload={"call_id": call_id},
    )

    assert result == call_id
    assert observed_contexts == [ctx]
    assert tracer.spans[0].attributes["presvo.call.id"] == call_id
    assert "PRIVATE_INVALID_CALL_ID" not in repr(tracer.spans[0].attributes)


@pytest.mark.anyio
async def test_worker_observability_shutdown_attempts_both_phases_after_failure(
    caplog,
) -> None:
    """Flush failure is fail-open and must not skip the worker shutdown phase."""
    from app.core.observability import shutdown_observability

    calls: list[str] = []

    class Telemetry:
        def force_flush(self) -> bool:
            calls.append("flush")
            raise RuntimeError("PRIVATE_FLUSH_DETAIL")

        def shutdown(self) -> bool:
            calls.append("shutdown")
            return True

    with caplog.at_level(logging.WARNING):
        await shutdown_observability(Telemetry(), timeout_seconds=0.2)

    assert calls == ["flush", "shutdown"]
    assert "event=observability_flush_failed" in caplog.text
    assert "PRIVATE_FLUSH_DETAIL" not in caplog.text


@pytest.mark.anyio
async def test_worker_lifecycle_action_skips_work_after_deadline() -> None:
    """An exhausted worker shutdown budget must not start another action."""
    from app.core.observability import _run_lifecycle_action

    called = False

    def action() -> None:
        nonlocal called
        called = True

    await _run_lifecycle_action(
        action,
        event="observability_shutdown_failed",
        operation="bounded_shutdown",
        deadline=time.monotonic() - 1,
    )

    assert called is False
