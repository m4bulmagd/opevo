import logging
import re


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
