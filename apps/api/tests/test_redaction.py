import logging

from app.core.logging import setup_logging
from app.core.redaction import SafeExtraFilter, redact_phone


def test_redact_phone_keeps_country_and_last_two_digits() -> None:
    assert redact_phone("+33612345678") == "+33******78"


def test_redact_phone_handles_missing_value() -> None:
    assert redact_phone(None) is None


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
