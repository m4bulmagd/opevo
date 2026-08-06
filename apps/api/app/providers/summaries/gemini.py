from __future__ import annotations

import asyncio
import json

import httpx

from app.core.observability import Observability, instrument_provider
from app.core.provider_failures import ProviderFailure, provider_failure_from_http_status
from app.providers.summaries.base import StructuredSummary, SummaryProvider


class _MalformedGeminiResponse(Exception):
    pass


_SUMMARY_FIELDS = frozenset(
    {
        "summary_text",
        "caller_intent",
        "action_items",
        "sentiment",
        "follow_up_required",
    }
)


class GeminiSummaryProvider(SummaryProvider):
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        observability: Observability,
        client=None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.client = client
        self._owns_client = client is None
        self._close_task: asyncio.Task[None] | None = None
        self.observability = observability

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

    async def aclose(self) -> None:
        close_task = self._close_task
        if close_task is None:
            if not self._owns_client:
                return
            client = self.client
            if client is None:
                self._owns_client = False
                return
            close_task = asyncio.create_task(self._close_owned_client(client))
            self._close_task = close_task
        await asyncio.shield(close_task)

    async def _close_owned_client(self, client) -> None:
        async_client = getattr(client, "aio", None)
        close = getattr(async_client, "aclose", None)
        if callable(close):
            await close()
            self.client = None
            self._owns_client = False
            return
        close = getattr(client, "close", None)
        if callable(close):
            await asyncio.to_thread(close)
        self.client = None
        self._owns_client = False

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
            return self._structured_summary(payload)
        except _MalformedGeminiResponse as exc:
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
    def _extract_json(response: object) -> dict[str, object]:
        text = getattr(response, "text", None)
        if not isinstance(text, str):
            raise _MalformedGeminiResponse("Gemini response text is invalid")
        content = text.strip()
        if not content:
            raise _MalformedGeminiResponse("Gemini response text is invalid")

        try:
            payload = json.loads(content)
        except json.JSONDecodeError as error:
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
            raise _MalformedGeminiResponse("Gemini response JSON is invalid") from error

        if not isinstance(payload, dict):
            raise _MalformedGeminiResponse("Gemini response shape is invalid")
        return payload

    @staticmethod
    def _structured_summary(payload: dict[str, object]) -> StructuredSummary:
        if set(payload) != _SUMMARY_FIELDS:
            raise _MalformedGeminiResponse("Gemini response schema is invalid")

        summary_text = payload["summary_text"]
        caller_intent = payload["caller_intent"]
        action_items = payload["action_items"]
        sentiment = payload["sentiment"]
        follow_up_required = payload["follow_up_required"]

        if not isinstance(summary_text, str):
            raise _MalformedGeminiResponse("Gemini response schema is invalid")
        if not isinstance(caller_intent, str):
            raise _MalformedGeminiResponse("Gemini response schema is invalid")
        if not isinstance(sentiment, str):
            raise _MalformedGeminiResponse("Gemini response schema is invalid")
        if not isinstance(action_items, list) or not all(
            isinstance(item, str) for item in action_items
        ):
            raise _MalformedGeminiResponse("Gemini response schema is invalid")
        if not isinstance(follow_up_required, bool):
            raise _MalformedGeminiResponse("Gemini response schema is invalid")

        return StructuredSummary(
            summary_text=summary_text,
            caller_intent=caller_intent,
            action_items=action_items,
            sentiment=sentiment,
            follow_up_required=follow_up_required,
        )
