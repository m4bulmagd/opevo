from dataclasses import dataclass


@dataclass(frozen=True)
class StructuredSummary:
    summary_text: str
    caller_intent: str
    action_items: list[str]
    sentiment: str
    follow_up_required: bool


class SummaryProvider:
    async def generate_summary(self, transcript: list[dict]) -> StructuredSummary:
        raise NotImplementedError
