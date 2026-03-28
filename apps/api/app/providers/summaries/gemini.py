from __future__ import annotations

import json

from app.core.config import get_settings
from app.providers.summaries.base import StructuredSummary, SummaryProvider


class GeminiSummaryProvider(SummaryProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        client=None,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.gemini_api_key
        self.model = model or settings.summary_model
        self.client = client

    def _get_client(self):
        if self.client is not None:
            return self.client

        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("google-genai is required for Gemini summaries") from exc

        if not self.api_key:
            raise RuntimeError("Google API key is required for Gemini summaries")

        self.client = genai.Client(api_key=self.api_key)
        return self.client

    async def generate_summary(self, transcript: list[dict]) -> StructuredSummary:
        client = self._get_client()
        prompt = self._build_prompt(transcript)
        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
        )
        payload = self._extract_json(response)
        return StructuredSummary(
            summary_text=str(payload["summary_text"]),
            caller_intent=str(payload["caller_intent"]),
            action_items=[str(item) for item in payload["action_items"]],
            sentiment=str(payload["sentiment"]),
            follow_up_required=bool(payload["follow_up_required"]),
        )

    @staticmethod
    def _build_prompt(transcript: list[dict]) -> str:
        transcript_text = "\n".join(
            f"{line.get('speaker', 'UNKNOWN')}: {line.get('text', '').strip()}"
            for line in transcript
            if line.get("text", "").strip()
        )
        return (
            "Return only valid JSON with keys summary_text, caller_intent, "
            "action_items, sentiment, follow_up_required.\n\n"
            f"Transcript:\n{transcript_text}"
        )

    @staticmethod
    def _extract_json(response) -> dict:
        text = getattr(response, "text", None)
        if not text:
            raise ValueError("Gemini returned no text")
        content = text.strip()
        if not content:
            raise ValueError("Gemini returned no text")

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            for start in range(len(content)):
                if content[start] not in "{[":
                    continue
                try:
                    payload, _ = decoder.raw_decode(content[start:])
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    return payload
            raise
