import asyncio
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import partial, wraps
from types import TracebackType
from typing import Any, Callable
from uuid import UUID

from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from app.composition.runtime import get_api_runtime
from app.core.logging import report_safe_exception
from app.core.provider_failures import (
    SAFE_PROVIDER_NAMES,
    ProviderFailure,
    is_safe_provider_operation,
)


logger = logging.getLogger(__name__)

_instance: "Observability | None" = None
_instance_lock = threading.Lock()
_call_id_context: ContextVar[str | None] = ContextVar(
    "presvo_observability_call_id",
    default=None,
)

_OTLP_HTTP_EXPORTER_LOGGERS = frozenset(
    {
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        "opentelemetry.exporter.otlp.proto.http.metric_exporter",
    }
)
_UNSAFE_OTLP_HTTP_LOG_TEMPLATES = frozenset(
    {
        "Failed to export span batch code: %s, reason: %s",
        "Transient error %s encountered while exporting span batch, "
        "retrying in %.2fs.",
        "Failed to export metrics batch code: %s, reason: %s",
        "Transient error %s encountered while exporting metrics batch, "
        "retrying in %.2fs.",
    }
)

_WEBHOOK_PROVIDERS = frozenset({"clerk", "stripe", "livekit"})
_WEBHOOK_OUTCOMES = frozenset({"accepted", "duplicate", "rejected", "error"})
_JOB_NAMES = frozenset(
    {
        "call_finalization",
        "outbox_delivery",
        "outbox_reconciliation",
        "call_reconciliation",
        "verification_expiry",
    }
)
_WORKER_QUEUE_CLASSES = frozenset({"call_lifecycle", "background"})
_WORKER_JOB_OUTCOMES = frozenset(
    {"success", "error", "timeout", "cancelled"}
)
_WORKER_JOB_ATTEMPTS = frozenset({1, 2, 3})
_OUTBOX_STATUSES = frozenset({"pending", "processing", "delivered", "failed"})
_CALL_STATES = frozenset(
    {"pending", "connected", "ending", "finalizing", "completed", "failed"}
)
_RECORDING_START_STATES = frozenset(
    {"prepared", "starting", "started", "not_started", "uncertain"}
)
_RECORDING_RECONCILIATION_RESULTS = frozenset(
    {
        "complete",
        "recording_unresolved",
        "recording_provider_unavailable",
        "recording_storage_unavailable",
        "recording_identity_mismatch",
        "recording_identity_conflict",
        "recording_legacy_incomplete",
    }
)
_RECORDING_WEBHOOK_MISMATCH_CATEGORIES = frozenset(
    {"missing", "mismatch", "conflict"}
)
_OUTBOX_TOPICS = frozenset(
    {
        "account.deactivate",
        "phone.provision",
        "phone.enable",
        "phone.disable",
        "livekit.dispatch",
        "livekit.verification_dispatch",
        "recording.reconcile",
        "summary.generate",
    }
)
_ACCOUNT_DEACTIVATION_TRIGGER_VALUES = (
    "owner_request",
    "subscription_ended",
)
_ACCOUNT_DEACTIVATION_TRIGGERS = frozenset(
    _ACCOUNT_DEACTIVATION_TRIGGER_VALUES
)
_ACCOUNT_DEACTIVATION_STATUS_VALUES = (
    "pending",
    "processing",
    "attention_required",
    "completed",
)
_ACCOUNT_DEACTIVATION_STATUSES = frozenset(
    _ACCOUNT_DEACTIVATION_STATUS_VALUES
)
_ACCOUNT_DEACTIVATION_STEPS = frozenset(
    {
        "disable_routing",
        "cancel_subscription",
        "drain_call",
        "release_number",
        "reset_activation",
        "complete",
    }
)
_ACCOUNT_DEACTIVATION_OUTCOMES = frozenset(
    {"success", "retry", "attention"}
)
SAFE_ERROR_CLASSES = frozenset(
    {
        "timeout",
        "rate_limited",
        "unavailable",
        "authentication",
        "validation",
        "conflict",
        "not_found",
        "unknown",
    }
)
_SERVICE_NAMES = frozenset(
    {
        "presvo-api",
        "presvo-worker",
        "presvo-worker-background",
        "presvo-worker-call-lifecycle",
    }
)
_HTTP_METHODS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
)
_CONTRACT_NAMES = frozenset(
    {
        "TranscriptAppendRequest",
        "TranscriptAppendAcknowledgement",
        "CallCompletionRequest",
        "CallCompletionAcknowledgement",
        "VerificationCompletionRequest",
        "VerificationCompletionAcknowledgement",
        "RealtimeEvent",
        "TranscriptObservedEvent",
        "CallStartedEvent",
        "AgentSessionEndedEvent",
        "CallFinalizedEvent",
    }
)
_CONTRACT_CODES = frozenset(
    {
        "malformed_json",
        "missing_schema_version",
        "unsupported_schema_version",
        "invalid_payload",
        "channel_user_mismatch",
    }
)
_CONTRACT_TRANSPORTS = frozenset({"http", "livekit", "redis"})
_AUTH_OUTCOMES = frozenset({"accepted", "rejected", "unavailable"})
_AUTH_REASONS = frozenset(
    {
        "none",
        "malformed",
        "algorithm",
        "signature",
        "issuer",
        "audience",
        "claims",
        "authorized_party",
        "signing_key",
        "jwks_timeout",
        "jwks_http",
        "jwks_invalid",
        "jwks_closed",
    }
)
_JWKS_REFRESH_OUTCOMES = frozenset(
    {"success", "timeout", "http_error", "invalid", "cancelled"}
)
_JWKS_COOLDOWN_OUTCOMES = frozenset({"rejected", "unavailable"})


def _safe_failure(event: str, operation: str, error: BaseException) -> None:
    try:
        report_safe_exception(
            logger,
            event=event,
            operation=operation,
            error=error,
            status="failed",
            level=logging.WARNING,
        )
    except Exception:
        return


def normalize_error_class(error: BaseException) -> str:
    if isinstance(error, ProviderFailure):
        return error.error_class

    fields = getattr(error, "__dict__", None)
    structured = fields.get("error_class") if isinstance(fields, dict) else None
    if isinstance(structured, str) and structured in SAFE_ERROR_CLASSES:
        return structured
    name = type(error).__name__.lower()
    if isinstance(error, TimeoutError) or "timeout" in name:
        return "timeout"
    if "ratelimit" in name or "rate_limit" in name or "toomanyrequests" in name:
        return "rate_limited"
    if "auth" in name or "permission" in name or "forbidden" in name:
        return "authentication"
    if isinstance(error, ValueError) or "validation" in name or "invalid" in name:
        return "validation"
    if "conflict" in name or "alreadyexists" in name:
        return "conflict"
    if "unavailable" in name or "connection" in name or "network" in name:
        return "unavailable"
    return "unknown"


def validated_error_class(error_class: str) -> str:
    if error_class not in SAFE_ERROR_CLASSES:
        raise ValueError("Unsafe provider error class")
    return error_class


def _safe_call(operation: str, callback: Callable[[], Any]) -> bool:
    try:
        callback()
    except Exception as error:
        _safe_failure("observability_metric_record_failed", operation, error)
        return False
    return True


def _safe_trace_call(operation: str, callback: Callable[[], Any]) -> bool:
    try:
        callback()
    except Exception as error:
        _safe_failure("observability_trace_failed", operation, error)
        return False
    return True


def _safe_label(
    value: str,
    allowed: frozenset[str],
    *,
    fallback: str = "unknown",
) -> str:
    return value if value in allowed else fallback


def _safe_worker_attempt(value: object) -> int:
    return value if type(value) is int and value in _WORKER_JOB_ATTEMPTS else 0


def _provider_labels(provider: str, operation: str) -> tuple[str, str]:
    if provider not in SAFE_PROVIDER_NAMES:
        return "unknown", "unknown"
    safe_operation = (
        operation if is_safe_provider_operation(provider, operation) else "unknown"
    )
    return provider, safe_operation


def _validated_call_id(call_id: object) -> str | None:
    if call_id is None:
        return None
    try:
        return str(UUID(str(call_id)))
    except (TypeError, ValueError, AttributeError):
        return None


@contextmanager
def bind_call_id(call_id: object):
    parsed_call_id = _validated_call_id(call_id)
    token = _call_id_context.set(parsed_call_id)
    try:
        yield
    finally:
        _call_id_context.reset(token)


class _SafeOtlpExporterLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if (
            not isinstance(record.msg, str)
            or record.msg not in _UNSAFE_OTLP_HTTP_LOG_TEMPLATES
        ):
            return True
        # These exact upstream templates interpolate collector-controlled
        # response text and reason strings. Preserve severity, discard fields.
        record.msg = "OTLP HTTP exporter diagnostic suppressed"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def _install_safe_otlp_exporter_logging(delegate: object) -> None:
    logger_name = type(delegate).__module__
    if logger_name not in _OTLP_HTTP_EXPORTER_LOGGERS:
        return
    exporter_logger = logging.getLogger(logger_name)
    if not any(
        isinstance(existing, _SafeOtlpExporterLogFilter)
        for existing in exporter_logger.filters
    ):
        exporter_logger.addFilter(_SafeOtlpExporterLogFilter())


class SafeSpanExporter(SpanExporter):
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        _install_safe_otlp_exporter_logging(delegate)

    def export(self, spans) -> SpanExportResult:
        try:
            result = self.delegate.export(spans)
        except Exception as error:
            _safe_failure("observability_export_failed", "export_traces", error)
            return SpanExportResult.FAILURE
        if result is SpanExportResult.FAILURE:
            _safe_failure(
                "observability_export_failed",
                "export_traces",
                RuntimeError(),
            )
        return result

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        try:
            return self.delegate.force_flush(timeout_millis=timeout_millis)
        except Exception as error:
            _safe_failure("observability_flush_failed", "flush_trace_exporter", error)
            return False

    def shutdown(self) -> None:
        try:
            self.delegate.shutdown()
        except Exception as error:
            _safe_failure(
                "observability_shutdown_failed",
                "shutdown_trace_exporter",
                error,
            )


class SafeMetricExporter(MetricExporter):
    def __init__(self, delegate) -> None:
        super().__init__(
            preferred_temporality=getattr(
                delegate,
                "_preferred_temporality",
                None,
            ),
            preferred_aggregation=getattr(
                delegate,
                "_preferred_aggregation",
                None,
            ),
        )
        self.delegate = delegate
        _install_safe_otlp_exporter_logging(delegate)

    def export(
        self,
        metrics_data,
        timeout_millis: float = 10_000,
        **kwargs,
    ) -> MetricExportResult:
        try:
            result = self.delegate.export(
                metrics_data,
                timeout_millis=timeout_millis,
                **kwargs,
            )
        except Exception as error:
            _safe_failure("observability_export_failed", "export_metrics", error)
            return MetricExportResult.FAILURE
        if result is MetricExportResult.FAILURE:
            _safe_failure(
                "observability_export_failed",
                "export_metrics",
                RuntimeError(),
            )
        return result

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        try:
            return self.delegate.force_flush(timeout_millis=timeout_millis)
        except Exception as error:
            _safe_failure("observability_flush_failed", "flush_metric_exporter", error)
            return False

    def shutdown(self, timeout_millis: float = 30_000, **kwargs) -> None:
        try:
            self.delegate.shutdown(timeout_millis=timeout_millis, **kwargs)
        except Exception as error:
            _safe_failure(
                "observability_shutdown_failed",
                "shutdown_metric_exporter",
                error,
            )


class Observability:
    def __init__(self, *, meter, tracer, lifecycle=None) -> None:
        self.tracer = tracer
        self.lifecycle = lifecycle
        self.http_duration = meter.create_histogram(
            "presvo.http.server.request.duration",
            unit="s",
        )
        self.webhook_requests = meter.create_counter("presvo.webhook.requests")
        self.webhook_duration = meter.create_histogram(
            "presvo.webhook.duration",
            unit="s",
        )
        self.outbox_events = meter.create_gauge("presvo.outbox.events")
        self.outbox_oldest_age = meter.create_gauge(
            "presvo.outbox.oldest_unfinished.age",
            unit="s",
        )
        self.outbox_terminal_failures = meter.create_counter(
            "presvo.outbox.terminal_failures"
        )
        self.worker_queue_delay = meter.create_histogram(
            "presvo.worker.queue.delay",
            unit="s",
        )
        self.worker_queue_depth = meter.create_gauge("presvo.worker.queue.depth")
        self.worker_queue_oldest_due_age = meter.create_gauge(
            "presvo.worker.queue.oldest_due.age",
            unit="s",
        )
        self.worker_job_duration = meter.create_histogram(
            "presvo.worker.job.duration",
            unit="s",
        )
        self.calls_current = meter.create_gauge("presvo.calls.current")
        self.calls_stale = meter.create_gauge("presvo.calls.stale")
        self.reconciliation_outcomes = meter.create_counter(
            "presvo.call_reconciliation.outcomes"
        )
        self.recording_operations = meter.create_gauge(
            "presvo.recording.operations"
        )
        self.recording_oldest_unresolved_age = meter.create_gauge(
            "presvo.recording.oldest_unresolved.age",
            unit="s",
        )
        self.recording_pending_stop_operations = meter.create_gauge(
            "presvo.recording.pending_stop.operations"
        )
        self.recording_pending_stop_oldest_age = meter.create_gauge(
            "presvo.recording.pending_stop.oldest_age",
            unit="s",
        )
        self.recording_pending_deletion_operations = meter.create_gauge(
            "presvo.recording.pending_deletion.operations"
        )
        self.recording_pending_deletion_oldest_age = meter.create_gauge(
            "presvo.recording.pending_deletion.oldest_age",
            unit="s",
        )
        self.recording_reconciliation_results = meter.create_counter(
            "presvo.recording.reconciliation.results"
        )
        self.recording_webhook_mismatches = meter.create_counter(
            "presvo.recording.webhook_mismatches"
        )
        self.recording_multiple_exact_match_conflicts = meter.create_counter(
            "presvo.recording.multiple_exact_match_conflicts"
        )
        self.provider_duration = meter.create_histogram(
            "presvo.provider.request.duration",
            unit="s",
        )
        self.provider_errors = meter.create_counter("presvo.provider.errors")
        self.account_deactivation_operations = meter.create_gauge(
            "presvo.account_deactivation.operations"
        )
        self.account_deactivation_oldest_incomplete_age = meter.create_gauge(
            "presvo.account_deactivation.oldest_incomplete_age",
            unit="s",
        )
        self.account_deactivation_reconciliation_results = meter.create_counter(
            "presvo.account_deactivation.reconciliation_results"
        )
        self.account_deactivation_attention = meter.create_gauge(
            "presvo.account_deactivation.attention"
        )
        self.account_deactivation_completion_duration = meter.create_histogram(
            "presvo.account_deactivation.completion_duration",
            unit="s",
        )
        self.invalid_contract_messages = meter.create_counter(
            "presvo.contract.invalid_messages"
        )
        self.auth_verifications = meter.create_counter("presvo.auth.verifications")
        self.jwks_refreshes = meter.create_counter("presvo.auth.jwks.refreshes")
        self.jwks_refresh_duration = meter.create_histogram(
            "presvo.auth.jwks.refresh.duration",
            unit="s",
        )
        self.jwks_coalesced_waits = meter.create_counter(
            "presvo.auth.jwks.coalesced_waits"
        )
        self.jwks_stale_key_uses = meter.create_counter(
            "presvo.auth.jwks.stale_key_uses"
        )
        self.jwks_refresh_cooldowns = meter.create_counter(
            "presvo.auth.jwks.refresh_cooldowns"
        )

    def record_invalid_contract(
        self,
        *,
        contract_name: str,
        code: str,
        transport: str,
    ) -> None:
        attributes = {
            "contract_name": _safe_label(contract_name, _CONTRACT_NAMES),
            "code": _safe_label(code, _CONTRACT_CODES),
            "transport": _safe_label(transport, _CONTRACT_TRANSPORTS),
        }
        _safe_call(
            "record_invalid_contract",
            lambda: self.invalid_contract_messages.add(1, attributes),
        )

    def record_auth_verification(self, outcome: str, reason: str) -> None:
        attributes = {
            "outcome": _safe_label(
                outcome,
                _AUTH_OUTCOMES,
                fallback="other",
            ),
            "reason": _safe_label(
                reason,
                _AUTH_REASONS,
                fallback="other",
            ),
        }
        _safe_call(
            "record_auth_verification",
            lambda: self.auth_verifications.add(1, attributes),
        )

    def record_jwks_refresh(self, outcome: str, duration_seconds: float) -> None:
        attributes = {
            "outcome": _safe_label(
                outcome,
                _JWKS_REFRESH_OUTCOMES,
                fallback="other",
            )
        }
        _safe_call(
            "record_jwks_refresh",
            lambda: self.jwks_refreshes.add(1, attributes),
        )
        _safe_call(
            "record_jwks_refresh_duration",
            lambda: self.jwks_refresh_duration.record(duration_seconds, attributes),
        )

    def record_jwks_coalesced_wait(self) -> None:
        _safe_call(
            "record_jwks_coalesced_wait",
            lambda: self.jwks_coalesced_waits.add(1),
        )

    def record_jwks_stale_key_use(self) -> None:
        _safe_call(
            "record_jwks_stale_key_use",
            lambda: self.jwks_stale_key_uses.add(1),
        )

    def record_jwks_refresh_cooldown(self, outcome: str) -> None:
        attributes = {
            "outcome": _safe_label(
                outcome,
                _JWKS_COOLDOWN_OUTCOMES,
                fallback="other",
            )
        }
        _safe_call(
            "record_jwks_refresh_cooldown",
            lambda: self.jwks_refresh_cooldowns.add(1, attributes),
        )

    def record_http_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        attributes = {
            "method": method,
            "route": route,
            "status_class": f"{status_code // 100}xx",
        }
        _safe_call(
            "record_http_request",
            lambda: self.http_duration.record(duration_seconds, attributes),
        )

    def record_webhook(
        self,
        provider: str,
        outcome: str,
        duration_seconds: float,
    ) -> None:
        attributes = {
            "provider": _safe_label(provider, _WEBHOOK_PROVIDERS),
            "outcome": _safe_label(outcome, _WEBHOOK_OUTCOMES),
        }
        _safe_call(
            "record_webhook_request",
            lambda: self.webhook_requests.add(1, attributes),
        )
        _safe_call(
            "record_webhook_duration",
            lambda: self.webhook_duration.record(duration_seconds, attributes),
        )

    @asynccontextmanager
    async def trace_operation(
        self,
        name: str,
        attributes: dict[str, Any],
        *,
        parent_context: Context | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
    ):
        span_context = None
        span = None
        start_kwargs: dict[str, Any] = {
            "attributes": attributes,
            "record_exception": False,
            "set_status_on_exception": False,
            "kind": kind,
        }
        if parent_context is not None:
            start_kwargs["context"] = parent_context
        try:
            span_context = self.tracer.start_as_current_span(name, **start_kwargs)
            span = span_context.__enter__()
        except Exception as error:
            _safe_failure("observability_trace_failed", "start_span", error)
            span_context = None

        exception_info: tuple[
            type[BaseException] | None,
            BaseException | None,
            TracebackType | None,
        ] = (None, None, None)
        try:
            yield span
        except BaseException as error:
            outcome = "error"
            exception_info = sys.exc_info()
            if span is not None:
                error_class = normalize_error_class(error)
                _safe_trace_call(
                    "set_span_error_class",
                    lambda: span.set_attribute(
                        "presvo.error.class",
                        error_class,
                    ),
                )
                _safe_trace_call(
                    "set_span_error_status",
                    lambda: span.set_status(Status(StatusCode.ERROR)),
                )
            raise
        else:
            outcome = "success"
        finally:
            if span is not None:
                _safe_trace_call(
                    "set_span_outcome",
                    lambda: span.set_attribute("presvo.outcome", outcome),
                )
            if span_context is not None:
                try:
                    span_context.__exit__(*exception_info)
                except Exception as error:
                    _safe_failure("observability_trace_failed", "finish_span", error)

    @staticmethod
    def set_span_attributes(span, attributes: dict[str, Any]) -> None:
        if span is None:
            return
        for key, value in attributes.items():
            _safe_trace_call(
                "set_span_attribute",
                partial(span.set_attribute, key, value),
            )

    @asynccontextmanager
    async def provider_operation(
        self,
        provider: str,
        operation: str,
        *,
        call_id: object = None,
    ):
        provider, operation = _provider_labels(provider, operation)
        started = time.monotonic()
        attributes: dict[str, Any] = {
            "presvo.provider.name": provider,
            "presvo.provider.operation": operation,
        }
        parsed_call_id = _validated_call_id(call_id) or _call_id_context.get()
        if parsed_call_id is not None:
            attributes["presvo.call.id"] = parsed_call_id
        span_context = None
        span = None
        try:
            span_context = self.tracer.start_as_current_span(
                f"provider.{provider}.{operation}",
                attributes=attributes,
                record_exception=False,
                set_status_on_exception=False,
                kind=SpanKind.CLIENT,
            )
            span = span_context.__enter__()
        except Exception as error:
            _safe_failure("observability_trace_failed", "start_provider_span", error)
            span_context = None

        exception_info: tuple[
            type[BaseException] | None,
            BaseException | None,
            TracebackType | None,
        ] = (None, None, None)
        try:
            yield
        except asyncio.CancelledError:
            outcome = "cancelled"
            exception_info = sys.exc_info()
            raise
        except Exception as error:
            outcome = "error"
            exception_info = sys.exc_info()
            error_class = normalize_error_class(error)
            failure_kind = "provider" if isinstance(error, ProviderFailure) else "internal"
            if span is not None:
                _safe_trace_call(
                    "set_provider_span_error_class",
                    lambda: span.set_attribute(
                        "presvo.error.class",
                        error_class,
                    ),
                )
                _safe_trace_call(
                    "set_provider_span_failure_kind",
                    lambda: span.set_attribute(
                        "presvo.failure.kind",
                        failure_kind,
                    ),
                )
                _safe_trace_call(
                    "set_provider_span_error_status",
                    lambda: span.set_status(Status(StatusCode.ERROR)),
                )
            metric_attributes = {
                "provider": provider,
                "operation": operation,
                "error_class": error_class,
                "failure_kind": failure_kind,
            }
            _safe_call(
                "record_provider_error",
                lambda: self.provider_errors.add(1, metric_attributes),
            )
            raise
        except BaseException:
            outcome = "error"
            exception_info = sys.exc_info()
            raise
        else:
            outcome = "success"
        finally:
            if span is not None:
                _safe_trace_call(
                    "set_provider_span_outcome",
                    lambda: span.set_attribute("presvo.outcome", outcome),
                )
            if span_context is not None:
                try:
                    span_context.__exit__(*exception_info)
                except Exception as error:
                    _safe_failure(
                        "observability_trace_failed",
                        "finish_provider_span",
                        error,
                    )
            metric_attributes = {
                "provider": provider,
                "operation": operation,
                "outcome": outcome,
            }
            _safe_call(
                "record_provider_duration",
                lambda: self.provider_duration.record(
                    time.monotonic() - started,
                    metric_attributes,
                ),
            )

    def record_outbox_snapshot(self, snapshot) -> None:
        for status, count in snapshot.counts.items():
            if status not in _OUTBOX_STATUSES:
                continue
            _safe_call(
                "record_outbox_snapshot",
                partial(
                    self.outbox_events.set,
                    count,
                    {"status": status},
                ),
            )
        _safe_call(
            "record_outbox_oldest_age",
            lambda: self.outbox_oldest_age.set(
                snapshot.oldest_unfinished_age_seconds,
                {},
            ),
        )

    def record_outbox_terminal_failure(self, topic: str, error_class: str) -> None:
        topic = _safe_label(topic, _OUTBOX_TOPICS)
        error_class = _safe_label(error_class, SAFE_ERROR_CLASSES)
        _safe_call(
            "record_outbox_terminal_failure",
            lambda: self.outbox_terminal_failures.add(
                1,
                {"topic": topic, "error_class": error_class},
            ),
        )

    def record_account_deactivation_snapshot(self, snapshot) -> None:
        counts = {
            (trigger, status): 0
            for trigger in _ACCOUNT_DEACTIVATION_TRIGGER_VALUES
            for status in _ACCOUNT_DEACTIVATION_STATUS_VALUES
        }
        for (trigger, status), count in snapshot.counts.items():
            if (
                trigger not in _ACCOUNT_DEACTIVATION_TRIGGERS
                or status not in _ACCOUNT_DEACTIVATION_STATUSES
            ):
                continue
            counts[(trigger, status)] = count
        for (trigger, status), count in counts.items():
            _safe_call(
                "record_account_deactivation_operations",
                partial(
                    self.account_deactivation_operations.set,
                    count,
                    {
                        "trigger": trigger,
                        "operation_status": status,
                    },
                ),
            )
        _safe_call(
            "record_account_deactivation_oldest_incomplete_age",
            lambda: self.account_deactivation_oldest_incomplete_age.set(
                snapshot.oldest_incomplete_age_seconds,
                {},
            ),
        )
        for trigger in _ACCOUNT_DEACTIVATION_TRIGGER_VALUES:
            count = snapshot.attention_counts.get(trigger, 0)
            _safe_call(
                "record_account_deactivation_attention",
                partial(
                    self.account_deactivation_attention.set,
                    count,
                    {"trigger": trigger},
                ),
            )

    def record_account_deactivation_result(
        self,
        trigger: str,
        step: str,
        outcome: str,
        error_class: str,
    ) -> None:
        attributes = {
            "trigger": _safe_label(
                trigger,
                _ACCOUNT_DEACTIVATION_TRIGGERS,
            ),
            "step": _safe_label(step, _ACCOUNT_DEACTIVATION_STEPS),
            "outcome": _safe_label(outcome, _ACCOUNT_DEACTIVATION_OUTCOMES),
            "error_class": _safe_label(error_class, SAFE_ERROR_CLASSES),
        }
        _safe_call(
            "record_account_deactivation_result",
            lambda: self.account_deactivation_reconciliation_results.add(
                1,
                attributes,
            ),
        )

    def record_account_deactivation_attention(
        self,
        trigger: str,
        _step: str,
        _error_class: str,
    ) -> None:
        trigger = _safe_label(trigger, _ACCOUNT_DEACTIVATION_TRIGGERS)
        _safe_call(
            "record_account_deactivation_attention",
            lambda: self.account_deactivation_attention.set(
                1,
                {"trigger": trigger},
            ),
        )

    def record_account_deactivation_completion(
        self,
        trigger: str,
        duration_seconds: float,
    ) -> None:
        trigger = _safe_label(trigger, _ACCOUNT_DEACTIVATION_TRIGGERS)
        _safe_call(
            "record_account_deactivation_completion",
            lambda: self.account_deactivation_completion_duration.record(
                max(0.0, duration_seconds),
                {"trigger": trigger},
            ),
        )

    def record_call_snapshot(self, snapshot) -> None:
        for state, count in snapshot.current.items():
            if state not in _CALL_STATES:
                continue
            _safe_call(
                "record_current_calls",
                partial(
                    self.calls_current.set,
                    count,
                    {"state": state},
                ),
            )
        for state, count in snapshot.stale.items():
            if state not in _CALL_STATES:
                continue
            _safe_call(
                "record_stale_calls",
                partial(
                    self.calls_stale.set,
                    count,
                    {"state": state},
                ),
            )

    def record_reconciliation_outcomes(self, result: dict[str, int]) -> None:
        for outcome in ("scanned", "recovered", "failed", "deferred"):
            _safe_call(
                "record_reconciliation_outcomes",
                partial(
                    self.reconciliation_outcomes.add,
                    result[outcome],
                    {"outcome": outcome},
                ),
            )

    def record_recording_operation_snapshot(self, snapshot) -> None:
        for state, count in snapshot.counts.items():
            if state not in _RECORDING_START_STATES:
                continue
            _safe_call(
                "record_recording_operation_state",
                partial(
                    self.recording_operations.set,
                    count,
                    {"state": state},
                ),
            )
        _safe_call(
            "record_recording_oldest_unresolved_age",
            lambda: self.recording_oldest_unresolved_age.set(
                snapshot.oldest_unresolved_age_seconds,
                {},
            ),
        )
        _safe_call(
            "record_recording_pending_stop_count",
            lambda: self.recording_pending_stop_operations.set(
                snapshot.pending_stop_count,
                {},
            ),
        )
        _safe_call(
            "record_recording_pending_stop_oldest_age",
            lambda: self.recording_pending_stop_oldest_age.set(
                snapshot.oldest_pending_stop_age_seconds,
                {},
            ),
        )
        _safe_call(
            "record_recording_pending_deletion_count",
            lambda: self.recording_pending_deletion_operations.set(
                snapshot.pending_deletion_count,
                {},
            ),
        )
        _safe_call(
            "record_recording_pending_deletion_oldest_age",
            lambda: self.recording_pending_deletion_oldest_age.set(
                snapshot.oldest_pending_deletion_age_seconds,
                {},
            ),
        )

    def record_recording_reconciliation_result(self, result: str) -> None:
        if type(result) is not str or result not in _RECORDING_RECONCILIATION_RESULTS:
            result = "recording_unresolved"
        _safe_call(
            "record_recording_reconciliation_result",
            lambda: self.recording_reconciliation_results.add(
                1,
                {"result": result},
            ),
        )

    def record_recording_webhook_mismatch(self, category: str) -> None:
        if (
            type(category) is not str
            or category not in _RECORDING_WEBHOOK_MISMATCH_CATEGORIES
        ):
            return
        _safe_call(
            "record_recording_webhook_mismatch",
            lambda: self.recording_webhook_mismatches.add(
                1,
                {"category": category},
            ),
        )

    def record_multiple_exact_match_conflict(self) -> None:
        _safe_call(
            "record_multiple_exact_match_conflict",
            lambda: self.recording_multiple_exact_match_conflicts.add(1, {}),
        )

    def record_worker_queue_delay(
        self,
        queue_class: str,
        job: str,
        attempt: int,
        seconds: float,
    ) -> None:
        attributes = {
            "queue_class": _safe_label(queue_class, _WORKER_QUEUE_CLASSES),
            "job": _safe_label(job, _JOB_NAMES),
            "attempt": _safe_worker_attempt(attempt),
        }
        _safe_call(
            "record_worker_queue_delay",
            lambda: self.worker_queue_delay.record(seconds, attributes),
        )

    def record_worker_queue_snapshot(
        self,
        queue_class: str,
        *,
        depth: int,
        oldest_due_age_seconds: float,
    ) -> None:
        attributes = {
            "queue_class": _safe_label(queue_class, _WORKER_QUEUE_CLASSES),
        }
        _safe_call(
            "record_worker_queue_snapshot_depth",
            lambda: self.worker_queue_depth.set(depth, attributes),
        )
        _safe_call(
            "record_worker_queue_snapshot_oldest_due_age",
            lambda: self.worker_queue_oldest_due_age.set(
                oldest_due_age_seconds,
                attributes,
            ),
        )

    def record_worker_job_duration(
        self,
        queue_class: str,
        job: str,
        outcome: str,
        attempt: int,
        seconds: float,
    ) -> None:
        attributes = {
            "queue_class": _safe_label(queue_class, _WORKER_QUEUE_CLASSES),
            "job": _safe_label(job, _JOB_NAMES),
            "outcome": _safe_label(outcome, _WORKER_JOB_OUTCOMES),
            "attempt": _safe_worker_attempt(attempt),
        }
        _safe_call(
            "record_worker_job_duration",
            lambda: self.worker_job_duration.record(
                seconds,
                attributes,
            ),
        )

    def force_flush(self) -> bool:
        if self.lifecycle is None:
            return True
        try:
            result = self.lifecycle.force_flush()
            return result is not False
        except Exception as error:
            _safe_failure("observability_flush_failed", "force_flush", error)
            return False

    def shutdown(self) -> bool:
        if self.lifecycle is None:
            return True
        try:
            result = self.lifecycle.shutdown()
            return result is not False
        except Exception as error:
            _safe_failure("observability_shutdown_failed", "shutdown", error)
            return False


class _Lifecycle:
    def __init__(self, tracer_provider, meter_provider) -> None:
        self.tracer_provider = tracer_provider
        self.meter_provider = meter_provider

    def force_flush(self) -> bool:
        return _run_independent_signal_actions(
            (
                (
                    "traces",
                    lambda: self.tracer_provider.force_flush(
                        timeout_millis=750
                    ),
                ),
                (
                    "metrics",
                    lambda: self.meter_provider.force_flush(
                        timeout_millis=750
                    ),
                ),
            ),
            event="observability_flush_failed",
            operation_prefix="force_flush",
            timeout_seconds=0.8,
        )

    def shutdown(self) -> bool:
        return _run_independent_signal_actions(
            (
                ("traces", self.tracer_provider.shutdown),
                ("metrics", self.meter_provider.shutdown),
            ),
            event="observability_shutdown_failed",
            operation_prefix="shutdown",
            timeout_seconds=0.8,
        )


def _run_independent_signal_actions(
    actions: tuple[tuple[str, Callable[[], Any]], ...],
    *,
    event: str,
    operation_prefix: str,
    timeout_seconds: float,
) -> bool:
    results: dict[str, bool] = {}
    result_lock = threading.Lock()

    def run(signal: str, action: Callable[[], Any]) -> None:
        try:
            result = action()
        except Exception as error:
            _safe_failure(event, f"{operation_prefix}_{signal}", error)
            succeeded = False
        else:
            succeeded = result is not False
        with result_lock:
            results[signal] = succeeded

    threads = []
    for signal, action in actions:
        thread = threading.Thread(
            target=run,
            args=(signal, action),
            name=f"presvo-otel-{operation_prefix}-{signal}",
            daemon=True,
        )
        thread.start()
        threads.append((signal, thread))

    deadline = time.monotonic() + timeout_seconds
    for signal, thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
        if thread.is_alive():
            _safe_failure(
                event,
                f"{operation_prefix}_{signal}",
                TimeoutError(),
            )
            results[signal] = False
    return all(results.get(signal, False) for signal, _thread in threads)


def resolve_otlp_endpoints(
    base_endpoint: str | None,
) -> tuple[str | None, str | None]:
    base = base_endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    trace_endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    metric_endpoint = os.getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT")
    if base:
        normalized = base.rstrip("/")
        trace_endpoint = trace_endpoint or f"{normalized}/v1/traces"
        metric_endpoint = metric_endpoint or f"{normalized}/v1/metrics"
    return trace_endpoint, metric_endpoint


def _build_components(
    service_name: str,
    trace_endpoint: str | None,
    metric_endpoint: str | None,
):
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": service_name})
    tracer_provider = TracerProvider(resource=resource)
    if trace_endpoint:
        tracer_provider.add_span_processor(
            BatchSpanProcessor(
                SafeSpanExporter(OTLPSpanExporter(endpoint=trace_endpoint))
            )
        )
    readers = []
    if metric_endpoint:
        readers.append(
            PeriodicExportingMetricReader(
                SafeMetricExporter(OTLPMetricExporter(endpoint=metric_endpoint))
            )
        )
    meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    return (
        meter_provider.get_meter("presvo"),
        tracer_provider.get_tracer("presvo"),
        _Lifecycle(tracer_provider, meter_provider),
    )


def initialize_observability(
    *,
    service_name: str,
    endpoint: str | None = None,
    components_factory: Callable[..., tuple[Any, Any, Any]] | None = None,
) -> Observability:
    global _instance
    if _instance is not None:
        return _instance
    with _instance_lock:
        if _instance is not None:
            return _instance
        service_name = _safe_label(service_name, _SERVICE_NAMES)
        if service_name == "unknown":
            service_name = "presvo-api"
        disabled = os.getenv("OTEL_SDK_DISABLED", "").strip().lower() in {
            "true",
            "1",
            "yes",
        }
        trace_endpoint, metric_endpoint = resolve_otlp_endpoints(endpoint)
        try:
            if not disabled and (trace_endpoint or metric_endpoint):
                factory = components_factory or _build_components
                meter, tracer, lifecycle = factory(
                    service_name,
                    trace_endpoint,
                    metric_endpoint,
                )
            else:
                meter = metrics.get_meter("presvo")
                tracer = trace.get_tracer("presvo")
                lifecycle = None
            _instance = Observability(
                meter=meter,
                tracer=tracer,
                lifecycle=lifecycle,
            )
        except Exception as error:
            _safe_failure("observability_initialize_failed", "initialize", error)
            _instance = Observability(
                meter=metrics.get_meter("presvo"),
                tracer=trace.get_tracer("presvo"),
            )
        return _instance


def get_request_observability(request) -> Observability:
    return get_api_runtime(request.app).observability


def reset_observability_for_tests() -> None:
    global _instance
    _instance = None


def install_http_observability(app) -> None:
    if getattr(app.state, "http_observability_installed", False):
        return
    app.state.http_observability_installed = True

    @app.middleware("http")
    async def observe_request(request, call_next):
        if request.scope.get("path") == "/healthz":
            return await call_next(request)
        started = time.monotonic()
        status_code = 500
        method = _safe_label(request.method, _HTTP_METHODS)
        telemetry = get_request_observability(request)
        carrier: dict[str, str] = {}
        traceparent = request.headers.get("traceparent")
        if traceparent is not None:
            carrier["traceparent"] = traceparent
        parent_context = TraceContextTextMapPropagator().extract(carrier=carrier)
        async with telemetry.trace_operation(
            "presvo.http.server",
            {"http.request.method": method},
            parent_context=parent_context,
            kind=SpanKind.SERVER,
        ) as span:
            try:
                response = await call_next(request)
                status_code = response.status_code
                return response
            finally:
                route = request.scope.get("route")
                route_template = getattr(route, "path", None) or "unmatched"
                telemetry.set_span_attributes(
                    span,
                    {
                        "http.route": route_template,
                        "http.response.status_code": status_code,
                    },
                )
                telemetry.record_http_request(
                    method=method,
                    route=route_template,
                    status_code=status_code,
                    duration_seconds=time.monotonic() - started,
                )


def instrument_job(
    job_name: str,
    *,
    queue_class: str,
    observability_getter: Callable[[dict[str, Any]], Observability],
):
    job_name = _safe_label(job_name, _JOB_NAMES)
    queue_class = _safe_label(queue_class, _WORKER_QUEUE_CLASSES)

    def decorator(function):
        @wraps(function)
        async def wrapped(ctx: dict[str, Any], *args, **kwargs):
            telemetry = observability_getter(ctx)
            attempt = _safe_worker_attempt(ctx.get("job_try"))
            enqueue_time = ctx.get("enqueue_time")
            if isinstance(enqueue_time, datetime):
                if enqueue_time.tzinfo is None:
                    enqueue_time = enqueue_time.replace(tzinfo=UTC)
                telemetry.record_worker_queue_delay(
                    queue_class,
                    job_name,
                    attempt,
                    max(0.0, (datetime.now(UTC) - enqueue_time).total_seconds()),
                )
            span_attributes = {
                "presvo.queue.class": queue_class,
                "presvo.job.name": job_name,
                "presvo.job.attempt": attempt,
            }
            call_id = _reference_call_id(args, kwargs)
            if call_id is not None:
                span_attributes["presvo.call.id"] = call_id
            with bind_call_id(call_id):
                async with telemetry.trace_operation(
                    "presvo.worker.job",
                    span_attributes,
                    kind=SpanKind.CONSUMER,
                ):
                    started = time.monotonic()
                    try:
                        result = await function(ctx, *args, **kwargs)
                    except asyncio.CancelledError:
                        outcome = "cancelled"
                        raise
                    except TimeoutError:
                        outcome = "timeout"
                        raise
                    except BaseException:
                        outcome = "error"
                        raise
                    else:
                        outcome = "success"
                        return result
                    finally:
                        telemetry.record_worker_job_duration(
                            queue_class,
                            job_name,
                            outcome,
                            attempt,
                            time.monotonic() - started,
                        )

        return wrapped

    return decorator


def instrument_provider(provider: str, operation: str):
    def decorator(function):
        @wraps(function)
        async def wrapped(self, *args, **kwargs):
            async with self.observability.provider_operation(
                provider,
                operation,
                call_id=kwargs.get("call_id"),
            ):
                return await function(self, *args, **kwargs)

        return wrapped

    return decorator


def _reference_call_id(args: tuple, kwargs: dict) -> str | None:
    candidates = list(args)
    if "payload" in kwargs:
        candidates.append(kwargs["payload"])
    for candidate in candidates:
        if not isinstance(candidate, dict) or "call_id" not in candidate:
            continue
        parsed_call_id = _validated_call_id(candidate["call_id"])
        if parsed_call_id is not None:
            return parsed_call_id
    return None


async def shutdown_observability(
    telemetry: Observability,
    *,
    timeout_seconds: float = 2.0,
) -> None:
    timeout_seconds = max(0.02, min(timeout_seconds, 5.0))
    started = time.monotonic()
    await _run_lifecycle_action(
        telemetry.force_flush,
        event="observability_flush_failed",
        operation="bounded_force_flush",
        deadline=started + (timeout_seconds / 2),
    )
    await _run_lifecycle_action(
        telemetry.shutdown,
        event="observability_shutdown_failed",
        operation="bounded_shutdown",
        deadline=started + timeout_seconds,
    )


async def _run_lifecycle_action(
    action: Callable[[], Any],
    *,
    event: str,
    operation: str,
    deadline: float,
) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return
    loop = asyncio.get_running_loop()
    completed = loop.create_future()

    def run_action() -> None:
        try:
            result = action()
            failure = None
        except Exception as error:
            result = None
            failure = error

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
        name="presvo-otel-shutdown",
        daemon=True,
    ).start()
    try:
        await asyncio.wait_for(completed, timeout=remaining)
    except Exception as error:
        _safe_failure(event, operation, error)
