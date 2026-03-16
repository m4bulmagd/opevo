from agent.api_client import AgentApiClient
from agent.event_publisher import EventPublisher


class SessionRuntime:
    def __init__(
        self,
        event_publisher: EventPublisher,
        *,
        api_client: AgentApiClient | None = None,
    ) -> None:
        self.event_publisher = event_publisher
        self.api_client = api_client
        self.transcript: list[dict[str, str]] = []

    async def handle_caller_transcript(self, dispatch_payload: dict, text: str) -> None:
        line = {"speaker": "CALLER", "text": text}
        self.transcript.append(line)
        await self.event_publisher.publish(
            {
                "type": "transcript",
                "user_id": dispatch_payload["user_id"],
                "call_id": dispatch_payload["call_id"],
                "speaker": "CALLER",
                "text": text,
            }
        )

    async def handle_agent_utterance(self, dispatch_payload: dict, text: str) -> None:
        line = {"speaker": "AGENT", "text": text}
        if not self.transcript or self.transcript[-1] != line:
            self.transcript.append(line)
        await self.event_publisher.publish(
            {
                "type": "transcript",
                "user_id": dispatch_payload["user_id"],
                "call_id": dispatch_payload["call_id"],
                "speaker": "AGENT",
                "text": text,
            }
        )

    async def finalize(self, dispatch_payload: dict, *, duration_seconds: int) -> None:
        if self.api_client is not None:
            payload = {
                "call_id": dispatch_payload["call_id"],
                "user_id": dispatch_payload["user_id"],
                "duration_seconds": duration_seconds,
                "minutes_remaining": dispatch_payload["minutes_remaining"],
                "transcript": list(self.transcript),
            }
            caller_number = dispatch_payload.get("caller_number")
            if caller_number:
                payload["caller_number"] = caller_number
            await self.api_client.complete_call(payload)
        await self.event_publisher.publish(
            {
                "type": "call_ended",
                "user_id": dispatch_payload["user_id"],
                "call_id": dispatch_payload["call_id"],
                "duration_seconds": duration_seconds,
            }
        )
