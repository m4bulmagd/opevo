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
