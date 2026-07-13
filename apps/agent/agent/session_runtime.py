import logging

from agent.api_client import AgentApiClient
from agent.event_publisher import EventPublisher
from agent.schemas import CallCompletionPayload, CallTranscriptItem, DispatchMetadata


logger = logging.getLogger(__name__)


class SessionRuntime:
    def __init__(
        self,
        event_publisher: EventPublisher,
        *,
        api_client: AgentApiClient | None = None,
    ) -> None:
        self.event_publisher = event_publisher
        self.api_client = api_client
        self.transcript: list[CallTranscriptItem] = []

    async def handle_caller_transcript(self, metadata: DispatchMetadata, text: str) -> None:
        line = CallTranscriptItem(speaker="CALLER", text=text)
        self.transcript.append(line)
        try:
            await self.event_publisher.publish(
                {
                    "type": "transcript",
                    "user_id": metadata.user_id,
                    "call_id": metadata.call_id,
                    "speaker": "CALLER",
                    "text": text,
                }
            )
        except Exception as exc:
            logger.error(
                "failed to publish caller transcript event call_id=%s error_type=%s",
                metadata.call_id,
                type(exc).__name__,
            )

    async def handle_agent_utterance(self, metadata: DispatchMetadata, text: str) -> None:
        line = CallTranscriptItem(speaker="AGENT", text=text)
        if not self.transcript or self.transcript[-1] != line:
            self.transcript.append(line)
        try:
            await self.event_publisher.publish(
                {
                    "type": "transcript",
                    "user_id": metadata.user_id,
                    "call_id": metadata.call_id,
                    "speaker": "AGENT",
                    "text": text,
                }
            )
        except Exception as exc:
            logger.error(
                "failed to publish agent utterance event call_id=%s error_type=%s",
                metadata.call_id,
                type(exc).__name__,
            )

    async def finalize(self, metadata: DispatchMetadata, *, duration_seconds: int) -> None:
        if self.api_client is not None:
            payload = CallCompletionPayload(
                call_id=metadata.call_id,
                user_id=metadata.user_id,
                duration_seconds=duration_seconds,
                minutes_remaining=metadata.minutes_remaining,
                caller_number=metadata.caller_number,
                transcript=self.transcript,
            )
            call_payload = payload.model_dump()
            if metadata.dispatch_token:
                call_payload["dispatch_token"] = metadata.dispatch_token
            try:
                await self.api_client.complete_call(call_payload)
            except Exception as exc:
                logger.error(
                    "failed to complete call %s after retries error_type=%s",
                    metadata.call_id,
                    type(exc).__name__,
                )

        try:
            await self.event_publisher.publish(
                {
                    "type": "call_ended",
                    "user_id": metadata.user_id,
                    "call_id": metadata.call_id,
                    "duration_seconds": duration_seconds,
                }
            )
        except Exception as exc:
            logger.error(
                "failed to publish call_ended event for %s error_type=%s",
                metadata.call_id,
                type(exc).__name__,
            )
