import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _Instrument:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.measurements: list[tuple[float, dict]] = []

    def _write(self, value, attributes=None) -> None:
        if self.failure is not None:
            raise self.failure
        self.measurements.append((value, dict(attributes or {})))

    add = _write
    record = _write
    set = _write


class _Meter:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.instruments: dict[str, _Instrument] = {}

    def _create(self, name: str, **_kwargs) -> _Instrument:
        instrument = _Instrument(failure=self.failure)
        self.instruments[name] = instrument
        return instrument

    create_counter = _create
    create_histogram = _create
    create_gauge = _create


class _Span:
    def __init__(self, name: str, attributes: dict | None, *, kind=None) -> None:
        self.name = name
        self.attributes = dict(attributes or {})
        self.status = None
        self.kind = kind

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def set_attribute(self, key: str, value) -> None:
        self.attributes[key] = value

    def set_status(self, status) -> None:
        self.status = status


class _Tracer:
    def __init__(self) -> None:
        self.spans: list[_Span] = []

    def start_as_current_span(self, name: str, *, attributes=None, **_kwargs):
        span = _Span(name, attributes, kind=_kwargs.get("kind"))
        self.spans.append(span)
        return span


def _observability(*, meter=None, tracer=None, lifecycle=None):
    try:
        from app.core.observability import Observability
    except ModuleNotFoundError as error:
        pytest.fail(f"observability module is required: {error}")
    return Observability(
        meter=meter or _Meter(),
        tracer=tracer or _Tracer(),
        lifecycle=lifecycle,
    )


def test_no_endpoint_constructs_no_exporter_and_initialization_is_idempotent() -> None:
    try:
        from app.core.observability import (
            initialize_observability,
            reset_observability_for_tests,
        )
    except ModuleNotFoundError as error:
        pytest.fail(f"observability initializer is required: {error}")

    calls: list[str] = []

    def forbidden_factory(*_args, **_kwargs):
        calls.append("network")
        raise AssertionError("exporter must not be constructed")

    reset_observability_for_tests()
    first = initialize_observability(
        service_name="presvo-api",
        endpoint=None,
        components_factory=forbidden_factory,
    )
    second = initialize_observability(
        service_name="presvo-api",
        endpoint=None,
        components_factory=forbidden_factory,
    )

    assert first is second
    assert calls == []


def test_otlp_base_and_signal_specific_endpoints_are_resolved_without_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.observability import resolve_otlp_endpoints

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", raising=False)
    assert resolve_otlp_endpoints("https://collector.example/otel") == (
        "https://collector.example/otel/v1/traces",
        "https://collector.example/otel/v1/metrics",
    )

    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "https://traces.example/custom",
    )
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "https://metrics.example/custom",
    )
    assert resolve_otlp_endpoints("https://collector.example/otel") == (
        "https://traces.example/custom",
        "https://metrics.example/custom",
    )


@pytest.mark.parametrize("failure_point", ["start", "set", "exit"])
@pytest.mark.anyio
async def test_tracer_failures_do_not_change_provider_result_or_execution_count(
    failure_point: str,
) -> None:
    sentinel = RuntimeError("TRACE_EXPORT_SECRET")
    executions = 0

    class FailingSpan(_Span):
        def set_attribute(self, key: str, value) -> None:
            if failure_point == "set":
                raise sentinel
            super().set_attribute(key, value)

        def __exit__(self, *_args) -> None:
            if failure_point == "exit":
                raise sentinel

    class FailingTracer:
        def start_as_current_span(self, name: str, *, attributes=None, **_kwargs):
            if failure_point == "start":
                raise sentinel
            return FailingSpan(name, attributes)

    telemetry = _observability(tracer=FailingTracer())
    async with telemetry.provider_operation("gemini", "generate_summary"):
        executions += 1

    assert executions == 1


@pytest.mark.anyio
async def test_trace_exit_failure_does_not_replace_provider_exception() -> None:
    provider_error = ValueError("PROVIDER_PRIVATE_CONTENT")

    class ExitFailingSpan(_Span):
        def __exit__(self, *_args) -> None:
            raise RuntimeError("TRACE_EXPORT_SECRET")

    class ExitFailingTracer:
        def start_as_current_span(self, name: str, *, attributes=None, **_kwargs):
            return ExitFailingSpan(name, attributes)

    telemetry = _observability(tracer=ExitFailingTracer())
    with pytest.raises(ValueError) as captured:
        async with telemetry.provider_operation("gemini", "generate_summary"):
            raise provider_error

    assert captured.value is provider_error


def test_exporter_failures_return_otel_failure_and_redact_content(caplog) -> None:
    from opentelemetry.sdk.metrics.export import MetricExportResult
    from opentelemetry.sdk.trace.export import SpanExportResult

    from app.core.observability import SafeMetricExporter, SafeSpanExporter

    sentinel = "COLLECTOR_CREDENTIAL_SECRET private transcript"

    class Delegate:
        def export(self, *_args, **_kwargs):
            raise RuntimeError(sentinel)

        def force_flush(self, *_args, **_kwargs):
            return True

        def shutdown(self, *_args, **_kwargs):
            return None

    with caplog.at_level(logging.WARNING):
        span_result = SafeSpanExporter(Delegate()).export([])
        metric_result = SafeMetricExporter(Delegate()).export(None)

    assert span_result is SpanExportResult.FAILURE
    assert metric_result is MetricExportResult.FAILURE
    assert "event=observability_export_failed" in caplog.text
    assert sentinel not in caplog.text
    assert "private transcript" not in caplog.text


def test_real_otlp_http_non_2xx_response_body_is_suppressed_and_reported_safely(
    caplog,
) -> None:
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.metrics.export import MetricsData, MetricExportResult
    from opentelemetry.sdk.trace.export import SpanExportResult

    from app.core.observability import SafeMetricExporter, SafeSpanExporter

    response_sentinel = "COLLECTOR_RESPONSE_SECRET private transcript"
    reason_sentinel = "COLLECTOR_REASON_SECRET private prompt"
    final_response = SimpleNamespace(
        ok=False,
        status_code=400,
        text=response_sentinel,
        reason="Bad Request",
    )
    retryable_response = SimpleNamespace(
        ok=False,
        status_code=503,
        text="unavailable",
        reason=reason_sentinel,
    )

    class ImmediateShutdownEvent:
        def wait(self, _timeout: float) -> bool:
            return True

        def set(self) -> None:
            return None

    span_delegate = OTLPSpanExporter(endpoint="http://collector.invalid/v1/traces")
    metric_delegate = OTLPMetricExporter(
        endpoint="http://collector.invalid/v1/metrics"
    )
    span_delegate._export = lambda *_args, **_kwargs: final_response
    metric_delegate._export = lambda *_args, **_kwargs: final_response

    try:
        with caplog.at_level(logging.WARNING):
            safe_span_exporter = SafeSpanExporter(span_delegate)
            safe_metric_exporter = SafeMetricExporter(metric_delegate)
            span_result = safe_span_exporter.export([])
            metric_result = safe_metric_exporter.export(
                MetricsData(resource_metrics=[])
            )
            span_delegate._export = lambda *_args, **_kwargs: retryable_response
            metric_delegate._export = lambda *_args, **_kwargs: retryable_response
            span_delegate._shutdown_in_progress = ImmediateShutdownEvent()
            metric_delegate._shutdown_in_progress = ImmediateShutdownEvent()
            assert safe_span_exporter.export([]) is SpanExportResult.FAILURE
            assert (
                safe_metric_exporter.export(MetricsData(resource_metrics=[]))
                is MetricExportResult.FAILURE
            )
            logging.getLogger("opentelemetry.unrelated").warning(
                "UNRELATED_OTEL_WARNING"
            )
            logging.getLogger(
                "opentelemetry.exporter.otlp.proto.http.trace_exporter"
            ).warning("UNRELATED_EXPORTER_WARNING")
    finally:
        span_delegate.shutdown()
        metric_delegate.shutdown()

    assert span_result is SpanExportResult.FAILURE
    assert metric_result is MetricExportResult.FAILURE
    assert caplog.text.count("event=observability_export_failed") == 4
    assert response_sentinel not in caplog.text
    assert reason_sentinel not in caplog.text
    assert "private transcript" not in caplog.text
    assert "private prompt" not in caplog.text
    assert "UNRELATED_OTEL_WARNING" in caplog.text
    assert "UNRELATED_EXPORTER_WARNING" in caplog.text


def test_service_name_is_normalized_before_resource_construction() -> None:
    from app.core.observability import (
        initialize_observability,
        reset_observability_for_tests,
    )

    observed: list[str] = []

    def factory(service_name, _trace_endpoint, _metric_endpoint):
        observed.append(service_name)
        return _Meter(), _Tracer(), None

    reset_observability_for_tests()
    initialize_observability(
        service_name="customer-private-service-name",
        endpoint="https://collector.example",
        components_factory=factory,
    )

    assert observed == ["presvo-api"]


def test_initialization_flush_shutdown_and_record_failures_are_fail_open_and_redacted(
    caplog,
) -> None:
    try:
        from app.core.observability import (
            initialize_observability,
            reset_observability_for_tests,
        )
    except ModuleNotFoundError as error:
        pytest.fail(f"observability initializer is required: {error}")

    sentinel = "OTLP_CREDENTIAL_SECRET customer transcript"

    def failing_factory(*_args, **_kwargs):
        raise RuntimeError(sentinel)

    with caplog.at_level(logging.WARNING):
        reset_observability_for_tests()
        telemetry = initialize_observability(
            service_name="presvo-api-failing",
            endpoint="https://otel.example/v1/traces",
            components_factory=failing_factory,
        )
        failing = _observability(
            meter=_Meter(failure=RuntimeError(sentinel)),
            lifecycle=SimpleNamespace(
                force_flush=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError(sentinel)
                ),
                shutdown=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError(sentinel)
                ),
            ),
        )

        telemetry.record_webhook("stripe", "accepted", 0.1)
        failing.record_webhook("stripe", "accepted", 0.1)
        assert failing.force_flush() is False
        assert failing.shutdown() is False
    assert "event=observability_initialize_failed" in caplog.text
    assert "event=observability_metric_record_failed" in caplog.text
    assert "event=observability_flush_failed" in caplog.text
    assert "event=observability_shutdown_failed" in caplog.text
    assert sentinel not in caplog.text
    assert "customer transcript" not in caplog.text


def test_http_metric_uses_route_template_not_raw_path_or_query() -> None:
    try:
        from app.core.observability import install_http_observability
    except ModuleNotFoundError as error:
        pytest.fail(f"HTTP observability middleware is required: {error}")

    meter = _Meter()
    telemetry = _observability(meter=meter)
    app = FastAPI()
    app.state.observability = telemetry
    install_http_observability(app)

    @app.get("/items/{item_id}")
    async def item(item_id: str):
        return {"item_id": item_id}

    raw_id = str(uuid4())
    with TestClient(app) as client:
        assert client.get(f"/items/{raw_id}?credential=QUERY_SECRET").status_code == 200

    measurements = meter.instruments[
        "presvo.http.server.request.duration"
    ].measurements
    assert len(measurements) == 1
    assert measurements[0][1] == {
        "method": "GET",
        "route": "/items/{item_id}",
        "status_class": "2xx",
    }
    assert raw_id not in repr(measurements)
    assert "QUERY_SECRET" not in repr(measurements)


def test_http_span_extracts_only_w3c_parent_and_uses_route_template() -> None:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from app.core.observability import install_http_observability

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry = _observability(tracer=provider.get_tracer("test"))
    app = FastAPI()
    app.state.observability = telemetry
    install_http_observability(app)

    @app.get("/calls/{call_id}")
    async def call(call_id: str):
        return {"call_id": call_id}

    raw_id = str(uuid4())
    parent_span_id = int("1234567890abcdef", 16)
    headers = {
        "traceparent": (
            "00-0123456789abcdef0123456789abcdef-1234567890abcdef-01"
        ),
        "tracestate": "vendor=OPAQUE_TRACE_STATE_SECRET",
        "baggage": "transcript=PRIVATE_BAGGAGE",
        "authorization": "Bearer PRIVATE_TOKEN",
    }
    with TestClient(app) as client:
        response = client.get(
            f"/calls/{raw_id}?prompt=PRIVATE_QUERY",
            headers=headers,
        )

    assert response.status_code == 200
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    from opentelemetry.trace import SpanKind

    assert span.kind is SpanKind.SERVER
    assert span.parent is not None
    assert span.parent.span_id == parent_span_id
    assert span.parent.trace_state.get("vendor") is None
    assert span.attributes == {
        "http.request.method": "GET",
        "http.route": "/calls/{call_id}",
        "http.response.status_code": 200,
        "presvo.outcome": "success",
    }
    rendered = repr(span.attributes)
    assert raw_id not in rendered
    assert "PRIVATE_QUERY" not in rendered
    assert "PRIVATE_BAGGAGE" not in rendered
    assert "PRIVATE_TOKEN" not in rendered
    assert "OPAQUE_TRACE_STATE_SECRET" not in repr(span.parent.trace_state)


@pytest.mark.anyio
async def test_http_and_job_tracer_start_failures_do_not_change_outcomes() -> None:
    from app.core.observability import install_http_observability, instrument_job

    class FailingTracer:
        def __init__(self) -> None:
            self.calls = 0

        def start_as_current_span(self, *_args, **_kwargs):
            self.calls += 1
            raise RuntimeError("TRACE_CREDENTIAL_SECRET")

    tracer = FailingTracer()
    telemetry = _observability(tracer=tracer)
    app = FastAPI()
    app.state.observability = telemetry
    install_http_observability(app)

    @app.get("/ok")
    async def ok():
        return {"status": "ok"}

    with TestClient(app) as client:
        assert client.get("/ok").json() == {"status": "ok"}

    @instrument_job("call_finalization")
    async def job(_ctx: dict) -> str:
        return "ok"

    assert await job({"observability": telemetry}) == "ok"
    assert tracer.calls == 2


@pytest.mark.anyio
async def test_provider_metrics_count_once_and_normalize_unknown_without_content() -> None:
    meter = _Meter()
    tracer = _Tracer()
    telemetry = _observability(meter=meter, tracer=tracer)

    async with telemetry.provider_operation("gemini", "generate_summary"):
        await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="PROMPT_SENTINEL"):
        async with telemetry.provider_operation("gemini", "generate_summary"):
            raise RuntimeError(
                "PROMPT_SENTINEL TRANSCRIPT_SENTINEL https://recording.example/private"
            )

    durations = meter.instruments["presvo.provider.request.duration"].measurements
    errors = meter.instruments["presvo.provider.errors"].measurements
    assert len(durations) == 2
    assert durations[0][1] == {
        "provider": "gemini",
        "operation": "generate_summary",
        "outcome": "success",
    }
    assert durations[1][1] == {
        "provider": "gemini",
        "operation": "generate_summary",
        "outcome": "error",
    }
    assert errors == [
        (
            1,
            {
                "provider": "gemini",
                "operation": "generate_summary",
                "error_class": "unknown",
            },
        )
    ]
    assert tracer.spans[0].attributes["presvo.provider.name"] == "gemini"
    assert tracer.spans[0].attributes["presvo.provider.operation"] == (
        "generate_summary"
    )
    assert "presvo.error.class" not in tracer.spans[0].attributes
    assert tracer.spans[1].attributes["presvo.error.class"] == "unknown"
    assert tracer.spans[1].status.status_code.name == "ERROR"
    rendered = repr(durations) + repr(errors) + repr(
        [(span.name, span.attributes) for span in tracer.spans]
    )
    for sentinel in (
        "PROMPT_SENTINEL",
        "TRANSCRIPT_SENTINEL",
        "recording.example",
    ):
        assert sentinel not in rendered


@pytest.mark.anyio
async def test_recording_not_running_provider_operation_is_allowlisted() -> None:
    tracer = _Tracer()
    telemetry = _observability(tracer=tracer)

    async with telemetry.provider_operation(
        "livekit",
        "ensure_recording_not_running",
    ):
        await asyncio.sleep(0)

    assert tracer.spans[0].attributes["presvo.provider.name"] == "livekit"
    assert tracer.spans[0].attributes["presvo.provider.operation"] == (
        "ensure_recording_not_running"
    )


def test_recording_reconcile_outbox_topic_is_allowlisted_without_identity_labels() -> None:
    meter = _Meter()
    telemetry = _observability(meter=meter)

    telemetry.record_outbox_terminal_failure(
        "recording.reconcile",
        "conflict",
    )

    assert meter.instruments[
        "presvo.outbox.terminal_failures"
    ].measurements == [
        (
            1,
            {"topic": "recording.reconcile", "error_class": "conflict"},
        )
    ]


@pytest.mark.anyio
async def test_recording_operation_uuid_is_never_bound_as_call_context() -> None:
    from app.core.observability import bind_call_id
    from app.models.outbox_event import OutboxEvent
    from app.workers.jobs.outbox_delivery import _validated_event_call_id

    operation_id = uuid4()
    event = OutboxEvent(
        topic="recording.reconcile",
        aggregate_type="recording-egress-operation",
        aggregate_id=operation_id,
        idempotency_key=f"recording.reconcile:{operation_id}:start",
        payload={"operation_id": str(operation_id)},
        status="processing",
        next_attempt_at=datetime.now(UTC),
    )
    tracer = _Tracer()
    telemetry = _observability(tracer=tracer)

    with bind_call_id(_validated_event_call_id(event)):
        async with telemetry.provider_operation(
            "livekit",
            "ensure_recording_not_running",
        ):
            pass

    assert "presvo.call.id" not in tracer.spans[0].attributes
    assert str(operation_id) not in repr(tracer.spans[0].attributes)


@pytest.mark.anyio
async def test_provider_telemetry_prefers_allowlisted_structured_error_class() -> None:
    class StructuredProviderError(RuntimeError):
        def __init__(self) -> None:
            super().__init__("timeout rate limit SECRET_MESSAGE_MUST_NOT_BE_READ")
            self.error_class = "authentication"

    meter = _Meter()
    tracer = _Tracer()
    telemetry = _observability(meter=meter, tracer=tracer)

    with pytest.raises(StructuredProviderError):
        async with telemetry.provider_operation("stripe", "create_portal_session"):
            raise StructuredProviderError()

    assert meter.instruments["presvo.provider.errors"].measurements == [
        (
            1,
            {
                "provider": "stripe",
                "operation": "create_portal_session",
                "error_class": "authentication",
            },
        )
    ]
    assert tracer.spans[0].attributes["presvo.error.class"] == "authentication"
    assert "SECRET_MESSAGE_MUST_NOT_BE_READ" not in repr(
        meter.instruments["presvo.provider.errors"].measurements
    )


@pytest.mark.anyio
async def test_internal_provider_and_worker_spans_use_semantic_kinds() -> None:
    from opentelemetry.trace import SpanKind

    from app.core.observability import instrument_job

    tracer = _Tracer()
    telemetry = _observability(tracer=tracer)

    async with telemetry.trace_operation("presvo.internal", {}):
        pass
    async with telemetry.provider_operation("gemini", "generate_summary"):
        pass

    @instrument_job("call_finalization")
    async def job(_ctx: dict) -> None:
        return None

    await job({"observability": telemetry})

    assert [span.kind for span in tracer.spans] == [
        SpanKind.INTERNAL,
        SpanKind.CLIENT,
        SpanKind.CONSUMER,
    ]


def test_webhook_semantic_outcomes_are_recorded_once_with_fixed_labels() -> None:
    meter = _Meter()
    telemetry = _observability(meter=meter)

    telemetry.record_webhook("clerk", "accepted", 0.01)
    telemetry.record_webhook("stripe", "rejected", 0.02)
    telemetry.record_webhook("livekit", "duplicate", 0.03)

    requests = meter.instruments["presvo.webhook.requests"].measurements
    durations = meter.instruments["presvo.webhook.duration"].measurements
    assert requests == [
        (1, {"provider": "clerk", "outcome": "accepted"}),
        (1, {"provider": "stripe", "outcome": "rejected"}),
        (1, {"provider": "livekit", "outcome": "duplicate"}),
    ]
    assert len(durations) == 3


class _WebhookTelemetry:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def record_webhook(self, provider: str, outcome: str, _duration: float) -> None:
        self.events.append((provider, outcome))


class _WebhookRequest:
    def __init__(self, payload: dict, telemetry: _WebhookTelemetry) -> None:
        self._body = json.dumps(payload).encode()
        self.headers = {
            "stripe-signature": "signed",
            "authorization": "Bearer signed",
        }
        self.app = SimpleNamespace(
            state=SimpleNamespace(observability=telemetry, arq_pool=None)
        )

    async def body(self) -> bytes:
        return self._body


@pytest.mark.anyio
async def test_actual_webhook_handlers_emit_semantic_outcome_once(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.webhooks import clerk as clerk_module
    from app.webhooks import livekit as livekit_module
    from app.webhooks import stripe as stripe_module

    telemetry = _WebhookTelemetry()

    class AuthProvider:
        def verify_webhook(self, _payload, _headers) -> str:
            return "evt-clerk"

    async def sync_user(self, **_kwargs) -> None:
        return None

    monkeypatch.setattr(clerk_module.AuthService, "sync_clerk_user", sync_user)
    clerk_payload = {
        "type": "user.created",
        "data": {
            "id": "clerk-user",
            "email_addresses": [{"email_address": "private@example.com"}],
        },
    }
    response = await clerk_module.handle_clerk_webhook(
        _WebhookRequest(clerk_payload, telemetry),
        session=db_session,
        auth_provider=AuthProvider(),
    )
    assert response.status_code == 202

    async def fail_sync_user(self, **_kwargs) -> None:
        raise RuntimeError("PRIVATE_WEBHOOK_FAILURE")

    monkeypatch.setattr(clerk_module.AuthService, "sync_clerk_user", fail_sync_user)
    with pytest.raises(RuntimeError, match="PRIVATE_WEBHOOK_FAILURE"):
        await clerk_module.handle_clerk_webhook(
            _WebhookRequest(clerk_payload, telemetry),
            session=db_session,
            auth_provider=AuthProvider(),
        )

    class RejectingBillingService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def verify_signature(self, *_args) -> None:
            return None

        async def handle_event(self, _event) -> None:
            raise stripe_module.UnsupportedStripeLifecycleError("rejected")

    monkeypatch.setattr(stripe_module, "BillingService", RejectingBillingService)
    stripe_payload = {"id": "evt-stripe", "type": "unsupported", "data": {}}
    with pytest.raises(Exception):
        await stripe_module.handle_stripe_webhook(
            _WebhookRequest(stripe_payload, telemetry),
            session=db_session,
        )

    livekit_event = {
        "id": "evt-livekit-duplicate",
        "event": "room_finished",
        "room": {"name": "private-room"},
        "participant": {"kind": "STANDARD", "attributes": {}},
    }
    request = _WebhookRequest({}, telemetry)
    receiver = SimpleNamespace(receive=lambda *_args: livekit_event)
    first = await livekit_module.handle_livekit_webhook(
        request,
        session=db_session,
        webhook_receiver=receiver,
        realtime_service=None,
    )
    second = await livekit_module.handle_livekit_webhook(
        request,
        session=db_session,
        webhook_receiver=receiver,
        realtime_service=None,
    )
    assert first.status_code == second.status_code == 202
    assert telemetry.events == [
        ("clerk", "accepted"),
        ("clerk", "error"),
        ("stripe", "rejected"),
        ("livekit", "accepted"),
        ("livekit", "duplicate"),
    ]


@pytest.mark.anyio
async def test_repository_snapshots_aggregate_outbox_and_call_state(
    db_session,
    settings,
) -> None:
    from app.models.call import Call
    from app.models.outbox_event import OutboxEvent
    from app.models.user import User
    from app.repositories.call_repository import CallRepository
    from app.repositories.outbox_repository import OutboxRepository

    now = datetime.now(UTC)
    users = [
        User(clerk_user_id=f"metrics-{index}", email=f"metrics-{index}@example.com")
        for index in range(4)
    ]
    db_session.add_all(users)
    await db_session.flush()
    calls = [
        Call(
            user_id=users[0].id,
            livekit_room_id="metrics-pending",
            status="pending",
            state_changed_at=now - timedelta(seconds=20),
        ),
        Call(
            user_id=users[1].id,
            livekit_room_id="metrics-connected",
            status="connected",
            state_changed_at=now - timedelta(seconds=130),
        ),
        Call(
            user_id=users[2].id,
            livekit_room_id="metrics-ending",
            status="ending",
            state_changed_at=now - timedelta(seconds=10),
        ),
        Call(
            user_id=users[3].id,
            livekit_room_id="metrics-finalizing",
            status="finalizing",
            state_changed_at=now - timedelta(seconds=50),
        ),
    ]
    db_session.add_all(calls)
    outbox = [
        OutboxEvent(
            topic="phone.disable",
            aggregate_type="user",
            aggregate_id=users[0].id,
            idempotency_key=f"metrics-{status}",
            payload={"user_id": str(users[0].id)},
            status=status,
            next_attempt_at=now,
            delivered_at=now if status == "delivered" else None,
            last_error_code="provider_terminal" if status == "failed" else None,
        )
        for status in ("pending", "processing", "delivered", "failed")
    ]
    db_session.add_all(outbox)
    await db_session.flush()
    outbox[0].created_at = now - timedelta(seconds=180)
    outbox[1].created_at = now - timedelta(seconds=30)
    await db_session.commit()

    outbox_snapshot = await OutboxRepository(db_session).observability_snapshot(now)
    call_snapshot = await CallRepository(db_session).observability_snapshot(
        now,
        settings.model_copy(
            update={
                "max_call_duration_seconds": 1,
                "call_reconciliation_pending_stale_seconds": 10,
                "call_reconciliation_connected_stale_seconds": 121,
                "call_reconciliation_ending_grace_seconds": 30,
                "call_reconciliation_finalizing_lease_seconds": 40,
            }
        ),
    )

    assert outbox_snapshot.counts == {
        "pending": 1,
        "processing": 1,
        "delivered": 1,
        "failed": 1,
    }
    assert outbox_snapshot.oldest_unfinished_age_seconds == pytest.approx(180)
    assert call_snapshot.current == {
        "pending": 1,
        "connected": 1,
        "ending": 1,
        "finalizing": 1,
        "completed": 0,
        "failed": 0,
    }
    assert call_snapshot.stale == {
        "pending": 1,
        "connected": 1,
        "ending": 0,
        "finalizing": 1,
        "completed": 0,
        "failed": 0,
    }


@pytest.mark.anyio
async def test_instrumented_job_records_queue_delay_duration_and_no_job_id() -> None:
    try:
        from app.core.observability import instrument_job
    except ModuleNotFoundError as error:
        pytest.fail(f"worker observability wrapper is required: {error}")

    meter = _Meter()
    telemetry = _observability(meter=meter)

    @instrument_job("call_finalization")
    async def job(ctx: dict) -> str:
        return "ok"

    result = await job(
        {
            "observability": telemetry,
            "enqueue_time": datetime.now(UTC) - timedelta(seconds=3),
            "job_id": "JOB_ID_SECRET",
        }
    )

    assert result == "ok"
    delay = meter.instruments["presvo.worker.queue.delay"].measurements
    duration = meter.instruments["presvo.worker.job.duration"].measurements
    assert delay[0][0] == pytest.approx(3, abs=0.2)
    assert delay[0][1] == {}
    assert duration[0][1] == {"job": "call_finalization", "outcome": "success"}
    assert "JOB_ID_SECRET" not in repr(delay) + repr(duration)


@pytest.mark.anyio
async def test_transcript_job_extracts_only_valid_call_reference_for_trace() -> None:
    from app.core.observability import instrument_job

    tracer = _Tracer()
    telemetry = _observability(tracer=tracer)
    call_id = str(uuid4())

    @instrument_job("transcript_flush")
    async def job(_ctx: dict, payload: dict) -> str:
        return payload["call_id"]

    result = await job(
        {"observability": telemetry},
        {
            "call_id": call_id,
            "transcript": [
                {"speaker": "CALLER", "text": "PRIVATE_TRANSCRIPT_SENTINEL"}
            ],
        },
    )

    assert result == call_id
    assert tracer.spans[0].attributes["presvo.call.id"] == call_id
    assert "PRIVATE_TRANSCRIPT_SENTINEL" not in repr(tracer.spans[0].attributes)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("topic", "aggregate_type", "provider_name", "operation"),
    [
        ("livekit.dispatch", "call", "livekit", "create_dispatch"),
        ("summary.generate", "call-summary", "gemini", "generate_summary"),
        (
            "recording.stop",
            "call-recording",
            "livekit",
            "ensure_recording_stopped",
        ),
    ],
)
async def test_validated_outbox_call_reference_correlates_nested_provider_span(
    tmp_path,
    topic: str,
    aggregate_type: str,
    provider_name: str,
    operation: str,
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.observability import instrument_provider
    from app.models import Base
    from app.models.outbox_event import OutboxEvent
    from app.workers.jobs.outbox_delivery import outbox_delivery_job

    database_path = tmp_path / f"outbox-correlation-{operation}.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    call_id = uuid4()
    tracer = _Tracer()
    telemetry = _observability(tracer=tracer)

    class Provider:
        def __init__(self) -> None:
            self.observability = telemetry

        async def invoke(self) -> None:
            return None

    Provider.invoke = instrument_provider(provider_name, operation)(Provider.invoke)
    provider = Provider()

    async def handler(_ctx, _event) -> None:
        await provider.invoke()

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(
                OutboxEvent(
                    topic=topic,
                    aggregate_type=aggregate_type,
                    aggregate_id=call_id,
                    idempotency_key=f"correlation-{operation}",
                    payload={"call_id": str(call_id)},
                    status="pending",
                    next_attempt_at=datetime.now(UTC),
                )
            )
            await session.commit()

        result = await outbox_delivery_job(
            {
                "session_factory": factory,
                "outbox_handlers": {topic: handler},
                "observability": telemetry,
            }
        )
        await provider.invoke()

        assert result == {
            "claimed": 1,
            "delivered": 1,
            "retried": 0,
            "failed": 0,
        }
        assert tracer.spans[0].attributes["presvo.call.id"] == str(call_id)
        assert "presvo.call.id" not in tracer.spans[1].attributes
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_mismatched_outbox_aggregate_cannot_seed_provider_call_context() -> None:
    from app.core.observability import bind_call_id, instrument_provider
    from app.workers.jobs.outbox_delivery import _validated_event_call_id

    call_id = uuid4()
    tracer = _Tracer()
    telemetry = _observability(tracer=tracer)

    class Provider:
        def __init__(self) -> None:
            self.observability = telemetry

        @instrument_provider("livekit", "create_dispatch")
        async def invoke(self) -> None:
            return None

    mismatches = [
        SimpleNamespace(
            topic="livekit.dispatch",
            aggregate_type="user",
            aggregate_id=call_id,
            payload={"call_id": str(call_id)},
        ),
        SimpleNamespace(
            topic="livekit.dispatch",
            aggregate_type="call",
            aggregate_id=uuid4(),
            payload={"call_id": str(call_id)},
        ),
    ]
    provider = Provider()

    for event in mismatches:
        with bind_call_id(_validated_event_call_id(event)):
            await provider.invoke()

    assert len(tracer.spans) == 2
    assert all("presvo.call.id" not in span.attributes for span in tracer.spans)


@pytest.mark.anyio
async def test_invalid_nested_call_reference_clears_then_restores_parent_context() -> None:
    from app.core.observability import bind_call_id, instrument_provider

    outer_call_id = uuid4()
    tracer = _Tracer()
    telemetry = _observability(tracer=tracer)

    class Provider:
        def __init__(self) -> None:
            self.observability = telemetry

        @instrument_provider("livekit", "create_dispatch")
        async def invoke(self) -> None:
            return None

    provider = Provider()
    with bind_call_id(outer_call_id):
        with bind_call_id(None):
            await provider.invoke()
        await provider.invoke()

    assert "presvo.call.id" not in tracer.spans[0].attributes
    assert tracer.spans[1].attributes["presvo.call.id"] == str(outer_call_id)


def test_reconciliation_metric_records_every_exact_fixed_result() -> None:
    meter = _Meter()
    telemetry = _observability(meter=meter)

    telemetry.record_reconciliation_outcomes(
        {"scanned": 7, "recovered": 4, "failed": 2, "deferred": 1}
    )

    assert meter.instruments[
        "presvo.call_reconciliation.outcomes"
    ].measurements == [
        (7, {"outcome": "scanned"}),
        (4, {"outcome": "recovered"}),
        (2, {"outcome": "failed"}),
        (1, {"outcome": "deferred"}),
    ]


@pytest.mark.anyio
async def test_terminal_outbox_metric_runs_once_after_durable_failed_commit(
    tmp_path,
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base
    from app.models.outbox_event import OutboxEvent
    from app.workers.jobs.outbox_delivery import (
        OutboxDeliveryError,
        outbox_delivery_job,
    )

    database_path = tmp_path / "terminal-observability.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    aggregate_id = uuid4()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            event = OutboxEvent(
                topic="phone.disable",
                aggregate_type="user",
                aggregate_id=aggregate_id,
                idempotency_key="terminal-observability",
                payload={"user_id": str(aggregate_id)},
                status="pending",
                next_attempt_at=now,
            )
            session.add(event)
            await session.commit()
            event_id = event.id

        async def fail_terminal(_ctx, _event) -> None:
            raise OutboxDeliveryError("provider_terminal", retryable=False)

        observed: list[tuple[str, str]] = []

        async def metric(topic: str, error_code: str) -> None:
            async with factory() as session:
                durable = await session.get(OutboxEvent, event_id)
                assert durable is not None
                assert durable.status == "failed"
            observed.append((topic, error_code))

        result = await outbox_delivery_job(
            {
                "session_factory": factory,
                "outbox_handlers": {"phone.disable": fail_terminal},
                "outbox_now": lambda: now,
                "outbox_terminal_failure_metric": metric,
            }
        )

        assert result == {"claimed": 1, "delivered": 0, "retried": 0, "failed": 1}
        assert observed == [("phone.disable", "provider_terminal")]
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_call_reconciliation_job_emits_exact_result_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.call_reconciliation_service import ReconciliationResult
    from app.workers.jobs import call_reconciliation as job_module

    observed: list[dict[str, int]] = []

    class Telemetry:
        def record_reconciliation_outcomes(self, result: dict[str, int]) -> None:
            observed.append(result)

    class Service:
        def __init__(self, _factory) -> None:
            pass

        async def reconcile(self, _now, *, limit: int):
            assert limit == 100
            return ReconciliationResult(
                scanned=7,
                recovered=4,
                failed=2,
                deferred=1,
            )

    monkeypatch.setattr(job_module, "CallReconciliationService", Service)
    result = await job_module.call_reconciliation_job(
        {
            "session_factory": object(),
            "observability": Telemetry(),
        }
    )

    assert result == {"scanned": 7, "recovered": 4, "failed": 2, "deferred": 1}
    assert observed == [result]
