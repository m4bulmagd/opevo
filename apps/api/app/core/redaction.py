from __future__ import annotations

import logging
import re


_PHONE_MASK = "******"
_SUPPORTED_PHONE_CHARACTERS = re.compile(r"^\+?[0-9\s().-]+$")
_SAFE_OPERATIONAL_EXTRA_KEYS = frozenset(
    {
        "audioseconds",
        "attemptcount",
        "callid",
        "charactercount",
        "count",
        "duration",
        "durationms",
        "elapsedms",
        "errortype",
        "event",
        "eventid",
        "framecount",
        "latency",
        "latencyms",
        "operation",
        "operationid",
        "provider",
        "providerrequestid",
        "roomid",
        "status",
        "tokencount",
        "userid",
    }
)
_SAFE_OPERATIONAL_METRIC_SUFFIXES = (
    "count",
    "duration",
    "durationms",
    "durationseconds",
    "elapsedms",
    "elapsedseconds",
    "latency",
    "latencyms",
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
_SAFE_EXTRA_VALUE_TYPES = (str, int, float, bool, type(None))


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


def _is_safe_operational_extra_key(key: object) -> bool:
    normalized = _normalize_extra_key(key)
    if normalized is None:
        return False
    if normalized in _SAFE_OPERATIONAL_EXTRA_KEYS:
        return True
    if any(marker in normalized for marker in _SENSITIVE_EXTRA_KEY_MARKERS):
        return False
    return normalized.endswith(_SAFE_OPERATIONAL_METRIC_SUFFIXES)


class SafeExtraFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key in tuple(record.__dict__):
            if key in _STANDARD_LOG_RECORD_KEYS:
                continue
            value = record.__dict__[key]
            if not _is_safe_operational_extra_key(key) or not isinstance(
                value,
                _SAFE_EXTRA_VALUE_TYPES,
            ):
                del record.__dict__[key]
        return True
