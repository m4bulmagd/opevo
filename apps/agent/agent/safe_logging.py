import logging
import re
import threading
from typing import Any


_SAFE_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SENSITIVE_MARKERS = (
    "authorization",
    "credential",
    "knowledgebase",
    "password",
    "prompt",
    "secret",
    "token",
    "transcript",
)
_HTTP_CLIENT_LOGGER_PREFIXES = ("httpx", "httpcore")
_HTTP_CLIENT_LOGGING_LOCK = threading.Lock()


def _is_http_client_record(record: logging.LogRecord) -> bool:
    return any(
        record.name == prefix or record.name.startswith(f"{prefix}.")
        for prefix in _HTTP_CLIENT_LOGGER_PREFIXES
    )


def _sanitize_http_client_record(
    record: logging.LogRecord,
) -> logging.LogRecord:
    if not _is_http_client_record(record):
        return record
    record.msg = "HTTP client diagnostic suppressed"
    record.args = ()
    record.exc_info = None
    record.exc_text = None
    record.stack_info = None
    return record


class _SafeHttpClientDiagnosticFilter(logging.Filter):
    """Remove URLs and wire details from third-party HTTP diagnostics."""

    _presvo_http_client_diagnostic_filter = True

    def filter(self, record: logging.LogRecord) -> bool:
        _sanitize_http_client_record(record)
        return True


class _SafeHttpClientLogRecordFactory:
    """Sanitize future HTTPcore child loggers before handlers see them."""

    _presvo_http_client_diagnostic_factory = True

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate

    def __call__(self, *args: Any, **kwargs: Any) -> logging.LogRecord:
        return _sanitize_http_client_record(self.delegate(*args, **kwargs))


def install_safe_http_client_logging() -> None:
    """Sanitize verbose HTTPX/HTTPCore records without muting Presvo logs."""
    with _HTTP_CLIENT_LOGGING_LOCK:
        current_factory = logging.getLogRecordFactory()
        if not getattr(
            current_factory,
            "_presvo_http_client_diagnostic_factory",
            False,
        ):
            logging.setLogRecordFactory(
                _SafeHttpClientLogRecordFactory(current_factory)
            )

        logger_names = {
            *_HTTP_CLIENT_LOGGER_PREFIXES,
            *(
                name
                for name in logging.Logger.manager.loggerDict
                if isinstance(name, str)
                and any(
                    name == prefix or name.startswith(f"{prefix}.")
                    for prefix in _HTTP_CLIENT_LOGGER_PREFIXES
                )
            ),
        }
        for logger_name in logger_names:
            http_logger = logging.getLogger(logger_name)
            if any(
                getattr(
                    log_filter,
                    "_presvo_http_client_diagnostic_filter",
                    False,
                )
                for log_filter in http_logger.filters
            ):
                continue
            http_logger.addFilter(_SafeHttpClientDiagnosticFilter())


def _safe_label(value: object) -> str | None:
    if not isinstance(value, str) or _SAFE_LABEL.fullmatch(value) is None:
        return None
    normalized = "".join(character for character in value.casefold() if character.isalnum())
    if any(marker in normalized for marker in _SENSITIVE_MARKERS):
        return None
    return value


def report_safe_exception(
    logger: logging.Logger,
    *,
    event: str,
    operation: str,
    error: BaseException,
    level: int = logging.ERROR,
) -> None:
    fields: list[tuple[str, str]] = []
    for key, value in (
        ("event", event),
        ("operation", operation),
        ("error_type", type(error).__name__),
    ):
        safe_value = _safe_label(value)
        if safe_value is not None:
            fields.append((key, safe_value))
    logger.log(
        level,
        " ".join(f"{key}=%s" for key, _value in fields),
        *(value for _key, value in fields),
    )
