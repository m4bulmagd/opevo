from dataclasses import dataclass
import logging

from app.core.config import get_settings
from app.core.logging import report_safe_exception
from app.providers.summaries.base import StructuredSummary, SummaryProvider
from app.providers.summaries.gemini import GeminiSummaryProvider


logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class SummaryResult:
    text: str | None
    data: dict | None
    job_enqueued: bool


class SummaryService:
    def __init__(self, provider: SummaryProvider) -> None:
        self.provider = provider

    async def create_summary(self, payload: dict) -> SummaryResult:
        transcript = self._normalize_transcript(payload.get("transcript") or [])
        if not transcript:
            return SummaryResult(text=None, data=None, job_enqueued=False)

        try:
            structured = await self.provider.generate_summary(transcript)
            data = self.validate_structured_summary(structured)
        except Exception as exc:
            report_safe_exception(
                logger,
                event="summary_generation_failed",
                operation="generate_summary",
                error=exc,
                call_id=payload.get("call_id"),
                status="failed",
            )
            return SummaryResult(text=None, data=None, job_enqueued=False)
        if data is None:
            return SummaryResult(text=None, data=None, job_enqueued=False)

        return SummaryResult(
            text=data["summary_text"],
            data=data,
            job_enqueued=True,
        )

    @staticmethod
    def _normalize_transcript(transcript: list[dict]) -> list[dict]:
        return [
            {
                "speaker": line.get("speaker", "UNKNOWN"),
                "text": line.get("text", "").strip(),
            }
            for line in transcript
            if line.get("text", "").strip()
        ]

    @staticmethod
    def validate_structured_summary(summary: StructuredSummary | dict) -> dict | None:
        if isinstance(summary, StructuredSummary):
            data = {
                "summary_text": summary.summary_text,
                "caller_intent": summary.caller_intent,
                "action_items": summary.action_items,
                "sentiment": summary.sentiment,
                "follow_up_required": summary.follow_up_required,
            }
        elif isinstance(summary, dict):
            data = summary
        else:
            return None

        required_keys = {
            "summary_text": str,
            "caller_intent": str,
            "action_items": list,
            "sentiment": str,
            "follow_up_required": bool,
        }
        for key, expected_type in required_keys.items():
            value = data.get(key)
            if value is None or not isinstance(value, expected_type):
                return None
        if not all(isinstance(item, str) for item in data["action_items"]):
            return None
        return data

    @staticmethod
    def _build_default_provider() -> SummaryProvider:
        settings = get_settings()
        if settings.summary_provider == "gemini":
            return GeminiSummaryProvider()
        raise ValueError(f"Unsupported summary provider: {settings.summary_provider}")
