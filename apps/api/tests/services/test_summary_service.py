import pytest

from app.services.summary_service import SummaryService


class FakeSummaryProvider:
    def __init__(self, result) -> None:
        self.result = result

    async def generate_summary(self, transcript: list[dict]):
        return self.result


class ExplodingSummaryProvider:
    async def generate_summary(self, transcript: list[dict]):
        raise RuntimeError("provider unavailable")


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
async def test_summary_service_handles_provider_failure_non_blocking() -> None:
    service = SummaryService(provider=ExplodingSummaryProvider())

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
