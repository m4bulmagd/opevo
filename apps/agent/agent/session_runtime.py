import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from agent.api_client import (
    AgentApiClient,
    TranscriptAppendPermanentError,
    TranscriptAppendRetryableError,
    is_completion_acknowledgement,
)
from agent.event_publisher import EventPublisher
from agent.schemas import CallCompletionPayload, CallTranscriptItem, DispatchMetadata


logger = logging.getLogger(__name__)

MAX_PENDING_TRANSCRIPT_ITEMS = 200
MAX_TRANSCRIPT_ITEMS = 2000
DEFAULT_FINALIZE_TIMEOUT_SECONDS = 5.0
MAX_RETRY_DELAY_SECONDS = 10


class TranscriptBufferOverflow(RuntimeError):
    """The bounded recovery buffer cannot accept another segment."""


class SessionRuntime:
    def __init__(
        self,
        event_publisher: EventPublisher,
        *,
        api_client: AgentApiClient | None = None,
        fatal_shutdown: Callable[[str], object] | None = None,
        retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        finalize_timeout_seconds: float = DEFAULT_FINALIZE_TIMEOUT_SECONDS,
    ) -> None:
        self.event_publisher = event_publisher
        self.api_client = api_client
        self.transcript: list[CallTranscriptItem] = []

        self._pending: deque[CallTranscriptItem] = deque(
            maxlen=MAX_PENDING_TRANSCRIPT_ITEMS
        )
        self._next_sequence_number = 1
        self._wake_flusher = asyncio.Event()
        self._drained = asyncio.Event()
        self._drained.set()
        self._flusher_task: asyncio.Task[None] | None = None
        self._flusher_stopped_permanently = False
        self._handler_tasks: set[asyncio.Task[Any]] = set()
        self._metadata: DispatchMetadata | None = None

        self._fatal_shutdown = fatal_shutdown
        self._fatal_shutdown_requested = False
        self._retry_sleep = retry_sleep
        self._finalize_timeout_seconds = finalize_timeout_seconds
        self._finalize_lock = asyncio.Lock()
        self._closing = False
        self._finalized = False
        self._call_ended_publish_attempted = False

    @property
    def pending_transcript(self) -> tuple[CallTranscriptItem, ...]:
        return tuple(self._pending)

    @property
    def flusher_task(self) -> asyncio.Task[None] | None:
        return self._flusher_task

    @property
    def handler_tasks(self) -> tuple[asyncio.Task[Any], ...]:
        return tuple(self._handler_tasks)

    @property
    def is_closing(self) -> bool:
        return self._closing

    def create_handler_task(
        self,
        factory: Callable[[], Coroutine[Any, Any, Any]],
    ) -> bool:
        """Register callback work synchronously so finalization owns its lifetime."""
        if self._closing:
            return False

        task = asyncio.create_task(factory())
        self._handler_tasks.add(task)
        task.add_done_callback(self._handler_tasks.discard)
        return True

    async def handle_caller_transcript(
        self,
        metadata: DispatchMetadata,
        text: str,
    ) -> bool:
        self._bind_metadata(metadata)
        line = self._accept_segment("CALLER", text)
        if line is None:
            return False
        await self._publish_transcript_event(metadata, line)
        return True

    async def handle_agent_utterance(
        self,
        metadata: DispatchMetadata,
        text: str,
    ) -> bool:
        self._bind_metadata(metadata)
        if not self._accepting_current_task():
            return False
        normalized_text = text.strip() if isinstance(text, str) else text
        if (
            self.transcript
            and self.transcript[-1].speaker == "AGENT"
            and self.transcript[-1].text == normalized_text
        ):
            duplicate = CallTranscriptItem(
                sequence_number=self.transcript[-1].sequence_number,
                speaker="AGENT",
                text=text,
            )
            await self._publish_transcript_event(metadata, duplicate)
            return False

        line = self._accept_segment("AGENT", text)
        if line is None:
            return False
        await self._publish_transcript_event(metadata, line)
        return True

    def _accept_segment(
        self,
        speaker: str,
        text: str,
    ) -> CallTranscriptItem | None:
        if not self._accepting_current_task():
            return None

        if len(self.transcript) >= MAX_TRANSCRIPT_ITEMS:
            self._request_fatal_shutdown("transcript_history_overflow")
            raise TranscriptBufferOverflow(
                "transcript compatibility history is full"
            )

        if len(self._pending) >= MAX_PENDING_TRANSCRIPT_ITEMS:
            self._request_fatal_shutdown("transcript_buffer_overflow")
            raise TranscriptBufferOverflow("transcript recovery buffer is full")

        line = CallTranscriptItem(
            sequence_number=self._next_sequence_number,
            speaker=speaker,
            text=text,
        )
        self._next_sequence_number += 1
        self.transcript.append(line)
        self._pending.append(line)
        self._drained.clear()
        self._wake_flusher.set()
        self._ensure_flusher()
        return line

    def _accepting_current_task(self) -> bool:
        return (
            not self._closing
            or asyncio.current_task() in self._handler_tasks
        )

    def _ensure_flusher(self) -> None:
        if self.api_client is None or not hasattr(self.api_client, "append_transcript"):
            return
        if self._flusher_stopped_permanently:
            return
        if self._flusher_task is None:
            self._flusher_task = asyncio.create_task(self._flush_transcripts())

    async def _flush_transcripts(self) -> None:
        while True:
            await self._wake_flusher.wait()
            retry_delay = 1

            while self._pending:
                item = self._pending[0]
                try:
                    acknowledgement = await self.api_client.append_transcript(
                        self._active_call_id,
                        self._active_dispatch_token,
                        item,
                    )
                except TranscriptAppendRetryableError:
                    await self._retry_sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY_SECONDS)
                    continue
                except TranscriptAppendPermanentError:
                    logger.error(
                        "transcript append permanently rejected call_id=%s sequence_number=%d",
                        self._active_call_id,
                        item.sequence_number,
                    )
                    self._flusher_stopped_permanently = True
                    self._request_fatal_shutdown(
                        "transcript_append_permanent_failure"
                    )
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.error(
                        "transcript append failed unexpectedly call_id=%s sequence_number=%d",
                        self._active_call_id,
                        item.sequence_number,
                    )
                    self._flusher_stopped_permanently = True
                    self._request_fatal_shutdown(
                        "transcript_append_permanent_failure"
                    )
                    return

                if not self._acknowledges(acknowledgement, item):
                    logger.error(
                        "transcript append acknowledgement invalid call_id=%s sequence_number=%d",
                        self._active_call_id,
                        item.sequence_number,
                    )
                    self._flusher_stopped_permanently = True
                    self._request_fatal_shutdown(
                        "transcript_append_permanent_failure"
                    )
                    return

                if self._pending and self._pending[0] == item:
                    self._pending.popleft()
                retry_delay = 1

            self._wake_flusher.clear()
            self._drained.set()

    @staticmethod
    def _acknowledges(
        acknowledgement: object,
        item: CallTranscriptItem,
    ) -> bool:
        return (
            isinstance(acknowledgement, dict)
            and acknowledgement.get("status") in {"stored", "duplicate"}
            and type(acknowledgement.get("sequence_number")) is int
            and acknowledgement["sequence_number"] == item.sequence_number
        )

    @property
    def _active_call_id(self) -> str:
        if self._metadata is None:
            raise RuntimeError("transcript metadata is unavailable")
        return self._metadata.call_id

    @property
    def _active_dispatch_token(self) -> str:
        if self._metadata is None:
            raise RuntimeError("transcript metadata is unavailable")
        return self._metadata.dispatch_token

    def _bind_metadata(self, metadata: DispatchMetadata) -> None:
        if self._metadata is not None and (
            self._metadata.call_id != metadata.call_id
            or self._metadata.dispatch_token != metadata.dispatch_token
        ):
            raise RuntimeError("session runtime cannot change call ownership")
        self._metadata = metadata

    async def _publish_transcript_event(
        self,
        metadata: DispatchMetadata,
        line: CallTranscriptItem,
    ) -> None:
        try:
            await self.event_publisher.publish(
                {
                    "type": "transcript",
                    "user_id": metadata.user_id,
                    "call_id": metadata.call_id,
                    "speaker": line.speaker,
                    "text": line.text,
                }
            )
        except Exception as exc:
            logger.error(
                "failed to publish %s transcript event call_id=%s error_type=%s",
                line.speaker.lower(),
                metadata.call_id,
                type(exc).__name__,
            )

    def _request_fatal_shutdown(self, reason: str) -> None:
        if self._fatal_shutdown_requested:
            return
        self._fatal_shutdown_requested = True
        if self._fatal_shutdown is None:
            return
        try:
            self._fatal_shutdown(reason)
        except Exception as exc:
            logger.error(
                "failed to request fatal session shutdown error_type=%s",
                type(exc).__name__,
            )

    async def finalize(
        self,
        metadata: DispatchMetadata,
        *,
        duration_seconds: int,
    ) -> None:
        async with self._finalize_lock:
            if self._finalized:
                return

            self._closing = True
            self._bind_metadata(metadata)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._finalize_timeout_seconds

            await self._drain_handler_tasks(deadline)
            await self._wait_for_acknowledged_flush(deadline)
            await self._stop_flusher()

            recovery_items = tuple(self._pending)
            completion = self._complete_call(
                metadata,
                duration_seconds=duration_seconds,
                recovery_items=recovery_items,
            )
            if self._call_ended_publish_attempted:
                completion_acknowledged = await completion
            else:
                self._call_ended_publish_attempted = True
                completion_acknowledged, _ = await asyncio.gather(
                    completion,
                    self._publish_call_ended(
                        metadata,
                        duration_seconds=duration_seconds,
                    ),
                )

            await self._close_api_client()

            if completion_acknowledged:
                recovery_sequences = {
                    item.sequence_number for item in recovery_items
                }
                self._pending = deque(
                    (
                        item
                        for item in self._pending
                        if item.sequence_number not in recovery_sequences
                    ),
                    maxlen=MAX_PENDING_TRANSCRIPT_ITEMS,
                )
                if not self._pending:
                    self._drained.set()
                self._finalized = True

    async def _complete_call(
        self,
        metadata: DispatchMetadata,
        *,
        duration_seconds: int,
        recovery_items: tuple[CallTranscriptItem, ...],
    ) -> bool:
        if self.api_client is None:
            return False

        payload = CallCompletionPayload(
            call_id=metadata.call_id,
            duration_seconds=duration_seconds,
            transcript=list(recovery_items),
        )
        call_payload = payload.model_dump()
        call_payload["dispatch_token"] = metadata.dispatch_token
        try:
            acknowledgement = await self.api_client.complete_call(call_payload)
        except Exception as exc:
            logger.error(
                "failed to complete call %s after retries error_type=%s",
                metadata.call_id,
                type(exc).__name__,
            )
            return False

        if not is_completion_acknowledgement(
            acknowledgement,
            metadata.call_id,
        ):
            logger.error(
                "call completion acknowledgement invalid call_id=%s",
                metadata.call_id,
            )
            return False
        return True

    async def _close_api_client(self) -> None:
        if self.api_client is None:
            return
        close = getattr(self.api_client, "aclose", None)
        if close is None:
            return
        try:
            await close()
        except Exception as exc:
            logger.error(
                "failed to close agent API client error_type=%s",
                type(exc).__name__,
            )

    async def _publish_call_ended(
        self,
        metadata: DispatchMetadata,
        *,
        duration_seconds: int,
    ) -> None:
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

    async def _drain_handler_tasks(self, deadline: float) -> None:
        current_task = asyncio.current_task()
        handlers = {
            task
            for task in self._handler_tasks
            if task is not current_task and not task.done()
        }
        if not handlers:
            return

        timeout = max(0.0, deadline - asyncio.get_running_loop().time())
        _done, pending = await asyncio.wait(handlers, timeout=timeout)
        if not pending:
            return

        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    async def _wait_for_acknowledged_flush(self, deadline: float) -> None:
        if not self._pending:
            return
        if self._flusher_task is None or self._flusher_task.done():
            return

        timeout = max(0.0, deadline - asyncio.get_running_loop().time())
        if timeout == 0:
            return
        try:
            await asyncio.wait_for(self._drained.wait(), timeout=timeout)
        except TimeoutError:
            return

    async def _stop_flusher(self) -> None:
        if self._flusher_task is None:
            return
        if not self._flusher_task.done():
            self._flusher_task.cancel()
        await asyncio.gather(self._flusher_task, return_exceptions=True)
