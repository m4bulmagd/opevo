import logging

from app.core.provider_failures import SAFE_PROVIDER_NAMES
from app.core.redaction import SafeExtraFilter, safe_log_identifier, safe_log_label


_SAFE_DIAGNOSTIC_PROVIDERS = SAFE_PROVIDER_NAMES | frozenset({"internal"})


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
