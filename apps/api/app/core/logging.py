import logging

from app.core.provider_failures import SAFE_PROVIDER_NAMES
from app.core.redaction import (
    STANDARD_LOG_RECORD_KEYS,
    SafeExtraFilter,
    safe_log_identifier,
    safe_log_label,
)


_SAFE_DIAGNOSTIC_PROVIDERS = SAFE_PROVIDER_NAMES | frozenset({"internal"})
_ARQ_WORKER_LOGGER_NAME = "arq.worker"
_ARQ_WORKER_FALLBACK_EVENT = "arq worker event"
_ARQ_WORKER_EVENTS = {
    "job %s already running elsewhere": "arq worker job skipped",
    "multi-exec error, job %s already started elsewhere": "arq worker job skipped",
    "job %s expired": "arq worker job expired",
    "deserializing job %s failed": "arq worker job deserialization failed",
    "%6.2fs ⊘ %s:%s aborted before start": "arq worker job aborted before start",
    "job %s, function %r not found": "arq worker function not registered",
    "%6.2fs ! %s max retries %d exceeded": "arq worker job retries exceeded",
    "%6.2fs → %s(%s)%s": "arq worker job started",
    "%6.2fs ↻ %s retrying job in %0.2fs": "arq worker job retry scheduled",
    "%6.2fs ⊘ %s aborted": "arq worker job aborted",
    "%6.2fs ↻ %s cancelled, will be run again": (
        "arq worker job cancelled for retry"
    ),
    "%6.2fs ! %s failed, %s: %s": "arq worker job failed",
    "%6.2fs ← %s ● %s": "arq worker job completed",
    "recording health: %s": "arq worker health updated",
    "Setting allow_pick_jobs to `False`": "arq worker draining",
    (
        "shutdown on %s, wait complete ◆ %d jobs complete ◆ %d failed ◆ "
        "%d retries ◆ %d ongoing to cancel"
    ): "arq worker shutdown",
    (
        "shutdown on %s ◆ %d jobs complete ◆ %d failed ◆ %d retries ◆ "
        "%d to be completed"
    ): "arq worker draining",
    (
        "shutdown on %s ◆ %d jobs complete ◆ %d failed ◆ %d retries ◆ "
        "%d ongoing to cancel"
    ): "arq worker shutdown",
}


class ArqWorkerLogSanitizer(logging.Filter):
    """Replace ARQ worker records with fixed, payload-blind events."""

    def filter(self, record: logging.LogRecord) -> bool:
        template = record.msg if isinstance(record.msg, str) else None
        fixed_event = (
            _ARQ_WORKER_EVENTS.get(template, _ARQ_WORKER_FALLBACK_EVENT)
            if template is not None
            else _ARQ_WORKER_FALLBACK_EVENT
        )

        for key in tuple(record.__dict__):
            if key not in STANDARD_LOG_RECORD_KEYS:
                del record.__dict__[key]
        record.msg = fixed_event
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def install_arq_worker_log_sanitizer() -> None:
    logger = logging.getLogger(_ARQ_WORKER_LOGGER_NAME)
    if not any(isinstance(item, ArqWorkerLogSanitizer) for item in logger.filters):
        logger.addFilter(ArqWorkerLogSanitizer())


def _install_safe_filter(target: logging.Filterer) -> None:
    if not any(isinstance(item, SafeExtraFilter) for item in target.filters):
        target.addFilter(SafeExtraFilter())


class SafeLogger(logging.Logger):
    def __init__(self, name: str, level: int = logging.NOTSET) -> None:
        super().__init__(name, level)
        _install_safe_filter(self)


def _protect_logger(logger: logging.Logger) -> None:
    _install_safe_filter(logger)
    for handler in logger.handlers:
        _install_safe_filter(handler)


def report_safe_exception(
    logger: logging.Logger,
    *,
    event: str,
    operation: str,
    error: BaseException | None = None,
    error_type: str | None = None,
    call_id: object = None,
    user_id: object = None,
    status: str | None = None,
    provider_request_id: object = None,
    provider: str | None = None,
    level: int = logging.ERROR,
) -> None:
    fields: list[tuple[str, object]] = []
    for key, label_value in (
        ("event", event),
        ("operation", operation),
        ("error_type", type(error).__name__ if error is not None else error_type),
        ("status", status),
    ):
        safe_value = safe_log_label(label_value)
        if safe_value is not None:
            fields.append((key, safe_value))
    if provider in _SAFE_DIAGNOSTIC_PROVIDERS:
        fields.append(("provider", provider))
    for key, identifier_value in (
        ("call_id", call_id),
        ("user_id", user_id),
        ("provider_request_id", provider_request_id),
    ):
        safe_value = safe_log_identifier(identifier_value)
        if safe_value is not None:
            fields.append((key, safe_value))

    logger.log(
        level,
        " ".join(f"{key}=%s" for key, _value in fields),
        *(value for _key, value in fields),
    )


def setup_logging() -> None:
    logging.setLoggerClass(SafeLogger)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _protect_logger(logging.getLogger())
    for candidate in logging.Logger.manager.loggerDict.values():
        if isinstance(candidate, logging.Logger):
            _protect_logger(candidate)
