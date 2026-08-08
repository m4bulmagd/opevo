import asyncio
import logging
import os
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic
from uuid import UUID

from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer

from agent.safe_logging import report_safe_exception


logger = logging.getLogger(__name__)

_DEFAULT_SERVICE_NAME = "opevo-agent"
_OTLP_HTTP_TRACE_EXPORTER_LOGGER = (
    "opentelemetry.exporter.otlp.proto.http.trace_exporter"
)
_OTLP_HTTP_TRACE_SENSITIVE_MESSAGES = frozenset(
    {
        "Failed to export span batch code: %s, reason: %s",
        "Transient error %s encountered while exporting span batch, "
        "retrying in %.2fs.",
    }
)
_PIPELINE_MODES = frozenset({"sts", "stt_llm_tts"})
_PROVIDERS = frozenset(
    {"deepgram", "elevenlabs", "gemini", "livekit", "speechmatics"}
)
_PROVIDER_OPERATIONS = frozenset({"connect", "session_start"})
_SENSITIVE_IDENTITY_MARKERS = (
    "authorization",
    "credential",
    "email",
    "phone",
    "prompt",
    "recording",
    "secret",
    "token",
    "transcript",
)


@dataclass(frozen=True)
class _ObservabilityAdapter:
    provider: TracerProvider
    tracer: Tracer


class _OtlpHttpTraceFailureFilter(logging.Filter):
    """Rewrite exact stock exporter events that include response content."""

    _opevo_otlp_response_body_filter = True

    def filter(self, record: logging.LogRecord) -> bool:
        if not (
            record.name == _OTLP_HTTP_TRACE_EXPORTER_LOGGER
            and record.msg in _OTLP_HTTP_TRACE_SENSITIVE_MESSAGES
        ):
            return True
        record.msg = "OTLP HTTP exporter diagnostic suppressed"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


class _ExporterRejectedBatch(Exception):
    """A content-free marker for an exporter-declared failure."""


class _SafeSpanExporter(SpanExporter):
    def __init__(self, exporter: SpanExporter) -> None:
        self._exporter = exporter
        if type(exporter).__module__ == _OTLP_HTTP_TRACE_EXPORTER_LOGGER:
            _install_otlp_response_body_filter()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self._exporter.export(spans)
        except Exception as exc:
            _report_failure(
                event="agent_observability_export_failed",
                operation="export_agent_spans",
                error=exc,
            )
            return SpanExportResult.FAILURE
        if result is SpanExportResult.FAILURE:
            _report_failure(
                event="agent_observability_export_failed",
                operation="export_agent_spans",
                error=_ExporterRejectedBatch(),
            )
        return result

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        try:
            return self._exporter.force_flush(timeout_millis)
        except Exception as exc:
            _report_failure(
                event="agent_observability_flush_failed",
                operation="flush_agent_exporter",
                error=exc,
            )
            return False

    def shutdown(self) -> None:
        try:
            self._exporter.shutdown()
        except Exception as exc:
            _report_failure(
                event="agent_observability_shutdown_failed",
                operation="shutdown_agent_exporter",
                error=exc,
            )


def _install_otlp_response_body_filter() -> None:
    exporter_logger = logging.getLogger(_OTLP_HTTP_TRACE_EXPORTER_LOGGER)
    if any(
        getattr(log_filter, "_opevo_otlp_response_body_filter", False)
        for log_filter in exporter_logger.filters
    ):
        return
    exporter_logger.addFilter(_OtlpHttpTraceFailureFilter())


_lock = threading.Lock()
_initialization_attempted = False
_shutdown_in_progress = False
_adapter: _ObservabilityAdapter | None = None


def initialize_observability() -> bool:
    """Initialize Opevo's private OTLP tracer at most once per process."""
    global _adapter, _initialization_attempted

    with _lock:
        if _initialization_attempted:
            return _adapter is not None
        _initialization_attempted = True

        if not _otlp_endpoint_configured():
            return False

        try:
            _adapter = _build_adapter()
        except Exception as exc:
            _report_failure(
                event="agent_observability_initialization_failed",
                operation="initialize_agent_observability",
                error=exc,
            )
            return False
        return True


def _otlp_endpoint_configured() -> bool:
    if os.getenv("OTEL_SDK_DISABLED", "").strip().casefold() == "true":
        return False
    return any(
        os.getenv(name, "").strip()
        for name in (
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
        )
    )


def _build_adapter() -> _ObservabilityAdapter:
    exporter = _SafeSpanExporter(_create_exporter())
    provider = TracerProvider(
        resource=Resource(
            attributes={"service.name": _service_name()},
        )
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    tracer = provider.get_tracer("opevo.agent")
    return _ObservabilityAdapter(provider=provider, tracer=tracer)


def _create_exporter() -> SpanExporter:
    # This import is intentionally lazy: importing the agent never constructs an
    # exporter or prepares an outbound telemetry connection.
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    return OTLPSpanExporter()


def _service_name() -> str:
    configured = os.getenv("OTEL_SERVICE_NAME", "").strip()
    if not configured or len(configured) > 64:
        return _DEFAULT_SERVICE_NAME
    if not all(character.isalnum() or character in "-_." for character in configured):
        return _DEFAULT_SERVICE_NAME
    normalized = "".join(
        character for character in configured.casefold() if character.isalnum()
    )
    if any(marker in normalized for marker in _SENSITIVE_IDENTITY_MARKERS):
        return _DEFAULT_SERVICE_NAME
    return configured


@contextmanager
def agent_lifecycle_span(
    *,
    call_id: str | None,
    pipeline_mode: str,
) -> Iterator[Span | None]:
    attributes: dict[str, str] = {
        "opevo.agent.pipeline_mode": (
            pipeline_mode if pipeline_mode in _PIPELINE_MODES else "unknown"
        )
    }
    normalized_call_id = _validated_call_id(call_id)
    if normalized_call_id is not None:
        attributes["opevo.call.id"] = normalized_call_id
    with _record_span(
        "opevo.agent.lifecycle",
        attributes,
        kind=SpanKind.INTERNAL,
    ) as span:
        yield span


@contextmanager
def agent_provider_span(
    *,
    provider: str,
    operation: str,
    call_id: str | None,
) -> Iterator[Span | None]:
    attributes = {
        "opevo.provider.name": provider if provider in _PROVIDERS else "unknown",
        "opevo.provider.operation": (
            operation if operation in _PROVIDER_OPERATIONS else "unknown"
        ),
    }
    normalized_call_id = _validated_call_id(call_id)
    if normalized_call_id is not None:
        attributes["opevo.call.id"] = normalized_call_id
    with _record_span(
        "opevo.agent.provider.request",
        attributes,
        kind=SpanKind.CLIENT,
    ) as span:
        yield span


@contextmanager
def _record_span(
    name: str,
    attributes: dict[str, str],
    *,
    kind: SpanKind,
) -> Iterator[Span | None]:
    adapter = _adapter
    if adapter is None:
        yield None
        return

    try:
        # Use a fresh context without attaching it. Opevo spans stay independent
        # of LiveKit's dynamic provider and correlate only through validated IDs.
        span = adapter.tracer.start_span(
            name,
            context=Context(),
            attributes=attributes,
            kind=kind,
        )
    except Exception as exc:
        _report_failure(
            event="agent_observability_span_failed",
            operation="start_agent_span",
            error=exc,
        )
        yield None
        return

    try:
        yield span
    except BaseException as exc:
        error_class = _normalize_error_class(exc)
        _safe_span_action(
            span,
            "record_agent_span_error",
            lambda: span.set_attribute("opevo.outcome", "error"),
        )
        _safe_span_action(
            span,
            "record_agent_error_class",
            lambda: span.set_attribute("opevo.error.class", error_class),
        )
        _safe_span_action(
            span,
            "set_agent_span_status",
            lambda: span.set_status(Status(StatusCode.ERROR)),
        )
        raise
    else:
        _safe_span_action(
            span,
            "record_agent_span_success",
            lambda: span.set_attribute("opevo.outcome", "success"),
        )
    finally:
        _safe_span_action(span, "end_agent_span", span.end)


def _safe_span_action(
    span: Span,
    operation: str,
    action: Callable[[], object],
) -> None:
    try:
        action()
    except Exception as exc:
        _report_failure(
            event="agent_observability_span_failed",
            operation=operation,
            error=exc,
        )


def _validated_call_id(value: str | None) -> str | None:
    if not isinstance(value, str) or len(value) > 36:
        return None
    try:
        return str(UUID(value))
    except (ValueError, AttributeError):
        return None


def _normalize_error_class(error: BaseException) -> str:
    name = type(error).__name__.casefold()
    if "timeout" in name:
        return "timeout"
    if "ratelimit" in name or "toomanyrequests" in name:
        return "rate_limited"
    if "auth" in name or "permission" in name:
        return "authentication"
    if "validation" in name or "value" in name or "type" in name:
        return "validation"
    if "conflict" in name or "alreadyexists" in name:
        return "conflict"
    if "unavailable" in name or "connection" in name or "network" in name:
        return "unavailable"
    return "unknown"


async def shutdown_observability(*, timeout_seconds: float = 1.0) -> None:
    """Flush and close the private provider within one best-effort deadline."""
    global _adapter, _initialization_attempted, _shutdown_in_progress

    with _lock:
        if _shutdown_in_progress:
            return
        adapter = _adapter
        _adapter = None
        if adapter is None:
            _initialization_attempted = False
            return
        _shutdown_in_progress = True

    timeout_seconds = max(0.01, min(timeout_seconds, 5.0))
    cleanup_task = asyncio.create_task(
        _close_adapter(adapter, timeout_seconds=timeout_seconds)
    )
    cancellation: asyncio.CancelledError | None = None
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError as exc:
            cancellation = exc

    cleanup_task.result()
    if cancellation is not None:
        raise cancellation


async def _close_adapter(
    adapter: _ObservabilityAdapter,
    *,
    timeout_seconds: float,
) -> None:
    started_at = monotonic()
    deadline = started_at + timeout_seconds
    completions: list[threading.Event] = []
    try:
        completions.append(
            await _run_provider_action(
                event="agent_observability_provider_flush_failed",
                operation="flush_agent_tracer_provider",
                action=lambda: adapter.provider.force_flush(
                    timeout_millis=max(1, int(timeout_seconds * 1000))
                ),
                deadline=started_at + (timeout_seconds / 2),
            )
        )
        completions.append(
            await _run_provider_action(
                event="agent_observability_provider_shutdown_failed",
                operation="shutdown_agent_tracer_provider",
                action=adapter.provider.shutdown,
                deadline=deadline,
            )
        )
    finally:
        _reset_after_provider_actions(completions)


def _reset_after_provider_actions(completions: Sequence[threading.Event]) -> None:
    """Allow a fresh adapter only after every old-provider action has returned."""

    def reset() -> None:
        global _initialization_attempted, _shutdown_in_progress

        for completion in completions:
            completion.wait()
        with _lock:
            _initialization_attempted = False
            _shutdown_in_progress = False

    if all(completion.is_set() for completion in completions):
        reset()
        return
    threading.Thread(
        target=reset,
        name="opevo-otel-reset",
        daemon=True,
    ).start()


async def _run_provider_action(
    *,
    event: str,
    operation: str,
    action: Callable[[], object],
    deadline: float,
) -> threading.Event:
    remaining = deadline - monotonic()
    loop = asyncio.get_running_loop()
    completed = loop.create_future()
    action_completed = threading.Event()

    def run_action() -> None:
        try:
            result = action()
            failure = None
        except Exception as exc:
            result = None
            failure = exc
        finally:
            action_completed.set()

        def resolve() -> None:
            if completed.done():
                return
            if failure is not None:
                completed.set_exception(failure)
            else:
                completed.set_result(result)

        try:
            loop.call_soon_threadsafe(resolve)
        except RuntimeError:
            return

    threading.Thread(
        target=run_action,
        name="opevo-otel-shutdown",
        daemon=True,
    ).start()
    if remaining <= 0:
        _report_failure(
            event=event,
            operation=operation,
            error=TimeoutError(),
        )
        return action_completed
    try:
        await asyncio.wait_for(completed, timeout=remaining)
    except Exception as exc:
        _report_failure(event=event, operation=operation, error=exc)
    return action_completed


def _report_failure(*, event: str, operation: str, error: BaseException) -> None:
    try:
        report_safe_exception(
            logger,
            event=event,
            operation=operation,
            error=error,
        )
    except Exception:
        return
