from __future__ import annotations

import asyncio
import json

import httpx

from app.core.config import get_settings
from app.core.observability import get_observability, instrument_provider
from app.core.provider_failures import ProviderFailure, provider_failure_from_http_status
from app.providers.summaries.base import StructuredSummary, SummaryProvider


class GeminiSummaryProvider(SummaryProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        client=None,
        observability=None,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key if api_key is not None else settings.gemini_api_key
        self.model = model or settings.summary_model
        self.client = client
        self.observability = observability or get_observability()

    def _get_client(self):
        if self.client is not None:
            return self.client

        if not self.api_key:
            raise ProviderFailure(
                provider="gemini",
                operation="generate_summary",
                disposition="terminal",
                error_class="authentication",
            )

        try:
            from google import genai
        except ImportError as exc:
            raise ProviderFailure(
                provider="gemini",
                operation="generate_summary",
                disposition="terminal",
                error_class="unknown",
            ) from exc

        self.client = genai.Client(api_key=self.api_key)
        return self.client

    @instrument_provider("gemini", "generate_summary")
    async def generate_summary(self, transcript: list[dict]) -> StructuredSummary:
        client = self._get_client()
        prompt = self._build_prompt(transcript)
        try:
            from google.genai import errors as genai_errors
        except ImportError as exc:
            raise ProviderFailure(
                provider="gemini",
                operation="generate_summary",
                disposition="terminal",
                error_class="unknown",
            ) from exc

        try:
            response = await client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
            )
        except genai_errors.APIError as exc:
            raise provider_failure_from_http_status(
                provider="gemini",
                operation="generate_summary",
                status=exc.code,
            ) from exc
        except genai_errors.UnknownApiResponseError as exc:
            raise ProviderFailure(
                provider="gemini",
                operation="generate_summary",
                disposition="terminal",
                error_class="validation",
            ) from exc
        except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
            raise ProviderFailure(
                provider="gemini",
                operation="generate_summary",
                disposition="retryable",
                error_class="timeout",
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderFailure(
                provider="gemini",
                operation="generate_summary",
                disposition="retryable",
                error_class="unavailable",
            ) from exc

        try:
            payload = self._extract_json(response)
            return StructuredSummary(
                summary_text=str(payload["summary_text"]),
                caller_intent=str(payload["caller_intent"]),
                action_items=[str(item) for item in payload["action_items"]],
                sentiment=str(payload["sentiment"]),
                follow_up_required=bool(payload["follow_up_required"]),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ProviderFailure(
                provider="gemini",
                operation="generate_summary",
                disposition="terminal",
                error_class="validation",
            ) from exc

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
