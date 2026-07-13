import logging

import pytest

from app.services.summary_service import SummaryService


class FakeSummaryProvider:
    def __init__(self, result) -> None:
        self.result = result

    async def generate_summary(self, transcript: list[dict]):
        return self.result


class ExplodingSummaryProvider:
    async def generate_summary(self, transcript: list[dict]):
        raise RuntimeError(
            "TRANSCRIPT_SENTINEL_FROM_SUMMARY_PROVIDER "
            "AUTHORIZATION_SENTINEL_FROM_SUMMARY_PROVIDER"
        )


def test_structured_summary_validator_is_public() -> None:
    expected = {
        "summary_text": "Summary",
        "caller_intent": "Ask",
        "action_items": ["Reply"],
        "sentiment": "neutral",
        "follow_up_required": False,
    }

    assert SummaryService.validate_structured_summary(expected) == expected


@pytest.mark.anyio
async def test_summary_service_returns_structured_summary() -> None:
    service = SummaryService(
        provider=FakeSummaryProvider(
            {
                "summary_text": "Caller asked about opening hours.",
                "caller_intent": "Ask about opening hours",
                "action_items": ["Provide opening hours"],
                "sentiment": "neutral",
                "follow_up_required": False,
            }
        )
    )

    result = await service.create_summary(
        {
            "transcript": [
                {"speaker": "CALLER", "text": "What are your opening hours?"},
                {"speaker": "AGENT", "text": "We open from nine to five."},
            ]
        }
    )

    assert result.text == "Caller asked about opening hours."
    assert result.data["caller_intent"] == "Ask about opening hours"
    assert result.data["follow_up_required"] is False
    assert result.job_enqueued is True


@pytest.mark.anyio
async def test_summary_service_rejects_malformed_provider_output() -> None:
    service = SummaryService(provider=FakeSummaryProvider({"summary_text": "missing fields"}))

    result = await service.create_summary(
        {
            "transcript": [
                {"speaker": "CALLER", "text": "What are your opening hours?"},
            ]
        }
    )

    assert result.text is None
    assert result.data is None
    assert result.job_enqueued is False


@pytest.mark.anyio
async def test_summary_service_handles_provider_failure_non_blocking(caplog) -> None:
    service = SummaryService(provider=ExplodingSummaryProvider())

    with caplog.at_level(logging.ERROR):
        result = await service.create_summary(
            {
                "call_id": "call_summary_123",
                "transcript": [
                    {"speaker": "CALLER", "text": "What are your opening hours?"},
                ],
            }
        )

    assert result.text is None
    assert result.data is None
    assert result.job_enqueued is False
    assert "TRANSCRIPT_SENTINEL_FROM_SUMMARY_PROVIDER" not in caplog.text
    assert "AUTHORIZATION_SENTINEL_FROM_SUMMARY_PROVIDER" not in caplog.text
    assert "event=summary_generation_failed" in caplog.text
    assert "operation=generate_summary" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "call_id=call_summary_123" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
