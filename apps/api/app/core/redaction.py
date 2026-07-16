from __future__ import annotations

import logging
import math
import re
from uuid import UUID


_PHONE_MASK = "******"
_SUPPORTED_PHONE_CHARACTERS = re.compile(r"^\+?[0-9\s().-]+$")
_SAFE_LABEL_EXTRA_KEYS = frozenset(
    {
        "errortype",
        "event",
        "operation",
        "provider",
        "status",
    }
)
_SAFE_IDENTIFIER_EXTRA_KEYS = frozenset(
    {
        "callid",
        "eventid",
        "operationid",
        "providerrequestid",
        "roomid",
        "userid",
    }
)
_SAFE_METRIC_EXTRA_KEYS = frozenset(
    {
        "audioseconds",
        "attemptcount",
        "charactercount",
        "count",
        "duration",
        "durationms",
        "elapsedms",
        "framecount",
        "latency",
        "latencyms",
        "tokencount",
    }
)
_SENSITIVE_EXTRA_KEY_MARKERS = (
    "accesskey",
    "apikey",
    "attributes",
    "authorization",
    "bearertoken",
    "body",
    "cookie",
    "credential",
    "databaseurl",
    "dispatchmetadata",
    "dispatchtoken",
    "header",
    "idtoken",
    "jwtkey",
    "knowledgebase",
    "payload",
    "password",
    "privatekey",
    "prompt",
    "redisurl",
    "refreshtoken",
    "secret",
    "settings",
    "signature",
    "token",
    "transcript",
)
_STANDARD_LOG_RECORD_KEYS = frozenset(logging.makeLogRecord({}).__dict__)
_SAFE_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_FIXED_AUTH_LABELS = frozenset(
    {
        "clerk_token_rejected",
        "verify_token",
    }
)


def redact_phone(value: str | None) -> str | None:
    if value is None:
        return None

    candidate = value.strip()
    if not candidate or _SUPPORTED_PHONE_CHARACTERS.fullmatch(candidate) is None:
        return _PHONE_MASK

    digits = "".join(character for character in value if character.isdigit())
    if candidate.startswith("+"):
        is_supported = digits.startswith("33") and len(digits) == 11
        subscriber_number = digits[2:]
    elif digits.startswith("0033"):
        is_supported = len(digits) == 13
        subscriber_number = digits[4:]
    elif digits.startswith("0"):
        is_supported = len(digits) == 10
        subscriber_number = digits[1:]
    else:
        is_supported = False
        subscriber_number = ""

    if not is_supported or len(subscriber_number) != 9 or subscriber_number.startswith("0"):
        return _PHONE_MASK
    return f"+33******{subscriber_number[-2:]}"


def _normalize_extra_key(key: object) -> str | None:
    if not isinstance(key, str):
        return None
    return "".join(character for character in key.casefold() if character.isalnum())


def _contains_sensitive_marker(value: str) -> bool:
    normalized = "".join(character for character in value.casefold() if character.isalnum())
    return any(marker in normalized for marker in _SENSITIVE_EXTRA_KEY_MARKERS)


def safe_log_label(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if _SAFE_LABEL.fullmatch(value) is None:
        return None
    if (
        value not in _SAFE_FIXED_AUTH_LABELS
        and _contains_sensitive_marker(value)
    ):
        return None
    return value


def _looks_like_french_phone_number(value: str) -> bool:
    digits = "".join(character for character in value if character.isdigit())
    return (
        len(digits) == 10
        and digits.startswith("0")
        and not digits.startswith("00")
    ) or (
        len(digits) == 11
        and digits.startswith("33")
        and digits[2] != "0"
    ) or (
        len(digits) == 13
        and digits.startswith("0033")
        and digits[4] != "0"
    )


def safe_log_identifier(value: object) -> str | None:
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str):
        return None
    if _SAFE_IDENTIFIER.fullmatch(value) is None:
        return None
    if _looks_like_french_phone_number(value):
        return None
    if not any(character.isalpha() for character in value):
        return None
    if _contains_sensitive_marker(value):
        return None
    return value


def _is_safe_metric(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value >= 0 and math.isfinite(value)


def _is_safe_operational_extra(key: object, value: object) -> bool:
    normalized = _normalize_extra_key(key)
    if normalized is None:
        return False
    if normalized in _SAFE_LABEL_EXTRA_KEYS:
        return safe_log_label(value) is not None
    if normalized in _SAFE_IDENTIFIER_EXTRA_KEYS:
        return safe_log_identifier(value) is not None
    if normalized in _SAFE_METRIC_EXTRA_KEYS:
        return _is_safe_metric(value)
    return False


class SafeExtraFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key in tuple(record.__dict__):
            if key in _STANDARD_LOG_RECORD_KEYS:
                continue
            value = record.__dict__[key]
            if not _is_safe_operational_extra(key, value):
                del record.__dict__[key]
        return True
