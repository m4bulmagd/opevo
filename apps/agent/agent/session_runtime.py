from agent.event_publisher import EventPublisher


class SessionRuntime:
    def __init__(self, event_publisher: EventPublisher) -> None:
        self.event_publisher = event_publisher

    async def handle_agent_utterance(self, dispatch_payload: dict, text: str) -> None:
        await self.event_publisher.publish(
            {
                "type": "transcript",
                "call_id": dispatch_payload["call_id"],
                "speaker": "AGENT",
                "text": text,
            }
        )

    async def finalize(self, dispatch_payload: dict, *, duration_seconds: int) -> None:
        await self.event_publisher.publish(
            {
                "type": "call_ended",
                "call_id": dispatch_payload["call_id"],
                "duration_seconds": duration_seconds,
            }
        )
