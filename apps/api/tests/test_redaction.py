import io
import logging

import pytest

from app.core.logging import setup_logging
from app.core.redaction import SafeExtraFilter, redact_phone


class RecordStateFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return f"{record.getMessage()} {record.__dict__!r}"


class SecretBearingObject:
    def __repr__(self) -> str:
        return "CUSTOM_OBJECT_AUTHORIZATION_SENTINEL"

    def __str__(self) -> str:
        return "CUSTOM_OBJECT_AUTHORIZATION_SENTINEL"


def render_log_record(
    logger: logging.Logger,
    *,
    extra: dict,
    add_filter: bool,
) -> str:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RecordStateFormatter())
    if add_filter:
        handler.addFilter(SafeExtraFilter())

    previous_handlers = logger.handlers[:]
    previous_level = logger.level
    previous_propagate = logger.propagate
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        logger.info("provider.operation.completed", extra=extra)
        return stream.getvalue()
    finally:
        logger.handlers = previous_handlers
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
        handler.close()


def test_redact_phone_keeps_country_and_last_two_digits() -> None:
    assert redact_phone("+33612345678") == "+33******78"


def test_redact_phone_handles_missing_value() -> None:
    assert redact_phone(None) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0033612345678", "+33******78"),
        ("06 12 34 56 78", "+33******78"),
        ("+33 6 12 34 56 78", "+33******78"),
    ],
)
def test_redact_phone_normalizes_supported_french_formats(value: str, expected: str) -> None:
    assert redact_phone(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "12",
        "+33",
        "not-a-phone",
        "call-me-at-0612345678",
        "+14155552671",
    ],
)
def test_redact_phone_fails_closed_for_unsupported_or_malformed_values(value: str) -> None:
    assert redact_phone(value) == "******"


def test_safe_extra_filter_removes_sensitive_structured_values() -> None:
    sentinels = {
        "system_prompt": "SYSTEM_PROMPT_SENTINEL_SECRET",
        "knowledge-base": "KNOWLEDGE_BASE_SENTINEL_SECRET",
        "transcript": "TRANSCRIPT_SENTINEL_SECRET",
        "Authorization": "AUTHORIZATION_SENTINEL_SECRET",
        "svix-signature": "SIGNATURE_SENTINEL_SECRET",
        "sip_attributes": {"sip.phoneNumber": "SIP_ATTRIBUTES_SENTINEL_SECRET"},
        "webhook_payload": {"private": "WEBHOOK_PAYLOAD_SENTINEL_SECRET"},
        "context": {"authorization": "NESTED_AUTHORIZATION_SENTINEL_SECRET"},
    }
    record = logging.makeLogRecord(
        {
            "msg": "livekit.webhook.received",
            "call_id": "call_123",
            "status": "accepted",
            **sentinels,
        }
    )

    assert SafeExtraFilter().filter(record) is True

    rendered_record = f"{record.getMessage()} {record.__dict__!r}"
    for value in sentinels.values():
        if isinstance(value, dict):
            value = next(iter(value.values()))
        assert str(value) not in rendered_record
    assert "call_123" in rendered_record
    assert "accepted" in rendered_record


def test_safe_extra_filter_removes_aliases_through_handler_and_formatter() -> None:
    sentinels = {
        "authorization_header": "AUTHORIZATION_HEADER_SENTINEL",
        "signature_value": "SIGNATURE_VALUE_SENTINEL",
        "transcript_text": "TRANSCRIPT_TEXT_SENTINEL",
        "stripe_secret_key": "STRIPE_SECRET_KEY_SENTINEL",
        "stripe_webhook_secret": "STRIPE_WEBHOOK_SECRET_SENTINEL",
        "s3_secret_key": "S3_SECRET_KEY_SENTINEL",
        "agent_internal_api_token": "AGENT_INTERNAL_TOKEN_SENTINEL",
        "firebase_credentials_json": "FIREBASE_CREDENTIALS_SENTINEL",
        "database_url": "DATABASE_URL_CREDENTIAL_SENTINEL",
        "redis_url": "REDIS_URL_CREDENTIAL_SENTINEL",
        "system_prompt": "SYSTEM_PROMPT_ALIAS_SENTINEL",
        "knowledge_base": "KNOWLEDGE_BASE_ALIAS_SENTINEL",
        "dispatch_token": "DISPATCH_TOKEN_ALIAS_SENTINEL",
        "dispatch_metadata": {"refresh_token": "REFRESH_TOKEN_NESTED_SENTINEL"},
        "webhook_payload": [
            {
                "sip_attributes": {
                    "signature_header": "NESTED_SIGNATURE_HEADER_SENTINEL",
                }
            }
        ],
        "context": {
            "authorization_header": "NESTED_AUTHORIZATION_HEADER_SENTINEL",
            "transcript_text": "NESTED_TRANSCRIPT_TEXT_SENTINEL",
            "token_value": "NESTED_TOKEN_VALUE_SENTINEL",
        },
    }
    safe_extras = {
        "call_id": "call_123",
        "user_id": "user_123",
        "status": "accepted",
        "event": "provider.completed",
        "operation": "dispatch",
        "provider_request_id": "request_123",
        "latency_ms": 42,
        "duration_ms": 84,
        "frame_count": 4,
        "token_count": 12,
    }

    rendered = render_log_record(
        logging.getLogger("test.safe.aliases"),
        extra={**sentinels, **safe_extras},
        add_filter=True,
    )

    for sentinel in (
        "AUTHORIZATION_HEADER_SENTINEL",
        "SIGNATURE_VALUE_SENTINEL",
        "TRANSCRIPT_TEXT_SENTINEL",
        "STRIPE_SECRET_KEY_SENTINEL",
        "STRIPE_WEBHOOK_SECRET_SENTINEL",
        "S3_SECRET_KEY_SENTINEL",
        "AGENT_INTERNAL_TOKEN_SENTINEL",
        "FIREBASE_CREDENTIALS_SENTINEL",
        "DATABASE_URL_CREDENTIAL_SENTINEL",
        "REDIS_URL_CREDENTIAL_SENTINEL",
        "SYSTEM_PROMPT_ALIAS_SENTINEL",
        "KNOWLEDGE_BASE_ALIAS_SENTINEL",
        "DISPATCH_TOKEN_ALIAS_SENTINEL",
        "REFRESH_TOKEN_NESTED_SENTINEL",
        "NESTED_SIGNATURE_HEADER_SENTINEL",
        "NESTED_AUTHORIZATION_HEADER_SENTINEL",
        "NESTED_TRANSCRIPT_TEXT_SENTINEL",
        "NESTED_TOKEN_VALUE_SENTINEL",
    ):
        assert sentinel not in rendered
    for safe_value in safe_extras.values():
        assert str(safe_value) in rendered


def test_safe_extra_filter_drops_unknown_and_unsupported_extra_values() -> None:
    rendered = render_log_record(
        logging.getLogger("test.safe.unknown_extras"),
        extra={
            "context": "UNKNOWN_CONTEXT_AUTHORIZATION_SENTINEL",
            "arbitrary_object": SecretBearingObject(),
            "call_id": SecretBearingObject(),
            "event": ["SAFE_FIELD_CONTAINER_SENTINEL"],
            "status": "accepted",
            "operation": "dispatch",
        },
        add_filter=True,
    )

    assert "UNKNOWN_CONTEXT_AUTHORIZATION_SENTINEL" not in rendered
    assert "CUSTOM_OBJECT_AUTHORIZATION_SENTINEL" not in rendered
    assert "SAFE_FIELD_CONTAINER_SENTINEL" not in rendered
    assert "accepted" in rendered
    assert "dispatch" in rendered


def test_setup_logging_protects_existing_named_non_propagating_handler() -> None:
    logger = logging.getLogger("test.safe.existing_non_propagating")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RecordStateFormatter())
    previous_handlers = logger.handlers[:]
    previous_level = logger.level
    previous_propagate = logger.propagate
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        setup_logging()
        logger.info(
            "provider.operation.completed",
            extra={
                "call_id": "call_existing_123",
                "authorization_header": "EXISTING_HANDLER_AUTH_SENTINEL",
            },
        )
        rendered = stream.getvalue()
    finally:
        logger.handlers = previous_handlers
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
        handler.close()

    assert "EXISTING_HANDLER_AUTH_SENTINEL" not in rendered
    assert "call_existing_123" in rendered


def test_setup_logging_protects_future_logger_and_later_handler() -> None:
    setup_logging()
    logger = logging.getLogger("test.safe.future_logger_created_after_setup")

    rendered = render_log_record(
        logger,
        extra={
            "provider_request_id": "future_request_123",
            "signature_header": "FUTURE_HANDLER_SIGNATURE_SENTINEL",
        },
        add_filter=False,
    )

    assert "FUTURE_HANDLER_SIGNATURE_SENTINEL" not in rendered
    assert "future_request_123" in rendered


def test_setup_logging_installs_safe_extra_filter_on_existing_handlers(caplog) -> None:
    sentinel = "AUTHORIZATION_SENTINEL_FROM_CAPLOG"

    with caplog.at_level(logging.INFO):
        setup_logging()
        logging.getLogger("test.safe.logging").info(
            "provider.request.completed",
            extra={
                "provider_request_id": "request_123",
                "authorization": sentinel,
            },
        )

    assert any(isinstance(item, SafeExtraFilter) for item in caplog.handler.filters)
    rendered_records = "\n".join(
        f"{record.getMessage()} {record.__dict__!r}" for record in caplog.records
    )
    assert sentinel not in rendered_records
    assert "request_123" in rendered_records
