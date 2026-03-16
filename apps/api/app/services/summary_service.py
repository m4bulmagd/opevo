from dataclasses import dataclass


@dataclass(frozen=True)
class SummaryResult:
    text: str | None
    job_enqueued: bool


class SummaryService:
    def create_summary(self, payload: dict) -> SummaryResult:
        transcript = payload.get("transcript") or []
        caller_lines = [
            line["text"].strip()
            for line in transcript
            if line.get("speaker") == "CALLER" and line.get("text", "").strip()
        ]
        if not caller_lines:
            return SummaryResult(text=None, job_enqueued=False)

        return SummaryResult(
            text=f"Caller request: {caller_lines[0]}",
            job_enqueued=True,
        )
