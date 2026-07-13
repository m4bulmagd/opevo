import logging
import re

from app.core.redaction import SafeExtraFilter


_OPAQUE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


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
    error: BaseException,
    call_id: object = None,
    user_id: object = None,
    status: str | None = None,
    provider_request_id: object = None,
    level: int = logging.ERROR,
) -> None:
    fields: list[tuple[str, object]] = [
        ("event", event),
        ("operation", operation),
        ("error_type", type(error).__name__),
    ]
    if status is not None:
        fields.append(("status", status))
    for key, value in (
        ("call_id", call_id),
        ("user_id", user_id),
        ("provider_request_id", provider_request_id),
    ):
        rendered_value = str(value) if value is not None else ""
        if _OPAQUE_IDENTIFIER.fullmatch(rendered_value):
            fields.append((key, rendered_value))

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
