from types import SimpleNamespace

import pytest

from app.providers.summaries.gemini import GeminiSummaryProvider


def test_extract_json_accepts_markdown_fenced_payload() -> None:
    response = SimpleNamespace(
        text="""```json
{
  "summary_text": "Caller asked about opening hours.",
  "caller_intent": "Ask about opening hours",
  "action_items": ["Provide opening hours"],
  "sentiment": "neutral",
  "follow_up_required": false
}
```"""
    )

    payload = GeminiSummaryProvider._extract_json(response)

    assert payload["summary_text"] == "Caller asked about opening hours."
    assert payload["action_items"] == ["Provide opening hours"]


def test_extract_json_accepts_prose_wrapped_payload() -> None:
    response = SimpleNamespace(
        text=(
            "Here is the structured summary:\n"
            '{'
            '"summary_text":"Caller asked about pricing.",'
            '"caller_intent":"Ask about pricing",'
            '"action_items":["Share current pricing"],'
            '"sentiment":"neutral",'
            '"follow_up_required":false'
            '}'
        )
    )

    payload = GeminiSummaryProvider._extract_json(response)

    assert payload["caller_intent"] == "Ask about pricing"
    assert payload["follow_up_required"] is False


def test_extract_json_rejects_missing_text() -> None:
    with pytest.raises(ValueError, match="Gemini returned no text"):
        GeminiSummaryProvider._extract_json(SimpleNamespace(text=""))


@pytest.mark.anyio
async def test_generate_summary_uses_async_client_and_parses_result() -> None:
    class SyncModels:
        def generate_content(self, **_kwargs):
            raise AssertionError("synchronous Gemini client must not run on event loop")

    class AsyncModels:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def generate_content(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                text=(
                    '{"summary_text":"Async summary",'
                    '"caller_intent":"Ask",'
                    '"action_items":["Reply"],'
                    '"sentiment":"neutral",'
                    '"follow_up_required":false}'
                )
            )

    async_models = AsyncModels()
    client = SimpleNamespace(
        models=SyncModels(),
        aio=SimpleNamespace(models=async_models),
    )

    result = await GeminiSummaryProvider(
        client=client,
        model="gemini-test",
    ).generate_summary([{"speaker": "CALLER", "text": "Hello"}])

    assert result.summary_text == "Async summary"
    assert len(async_models.calls) == 1
    assert async_models.calls[0]["model"] == "gemini-test"
    assert "CALLER: Hello" in async_models.calls[0]["contents"]
