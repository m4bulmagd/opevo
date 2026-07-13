from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any


_FORBIDDEN_EXTRA_KEY_SUFFIXES = (
    "apikey",
    "apisecret",
    "attributes",
    "authorization",
    "dispatchmetadata",
    "dispatchtoken",
    "knowledgebase",
    "payload",
    "prompt",
    "refreshtoken",
    "signature",
    "transcript",
)
_STANDARD_LOG_RECORD_KEYS = frozenset(logging.makeLogRecord({}).__dict__)


def redact_phone(value: str | None) -> str | None:
    if value is None:
        return None

    digits = "".join(character for character in value if character.isdigit())
    if not digits:
        return "******"

    country_prefix = "+33" if digits.startswith("33") else "+" + digits[:1]
    visible_suffix = digits[-2:] if len(digits) >= 2 else digits
    return f"{country_prefix}******{visible_suffix}"


def _is_forbidden_extra_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = "".join(character for character in key.casefold() if character.isalnum())
    return normalized.endswith(_FORBIDDEN_EXTRA_KEY_SUFFIXES)


def _sanitize_extra_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _sanitize_extra_value(nested_value)
            for key, nested_value in value.items()
            if not _is_forbidden_extra_key(key)
        }
    if isinstance(value, list):
        return [_sanitize_extra_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_extra_value(item) for item in value)
    return value


class SafeExtraFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key in tuple(record.__dict__):
            if key in _STANDARD_LOG_RECORD_KEYS:
                continue
            if _is_forbidden_extra_key(key):
                del record.__dict__[key]
                continue
            record.__dict__[key] = _sanitize_extra_value(record.__dict__[key])
        return True
