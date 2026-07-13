from __future__ import annotations

import inspect
import logging
import os
import time
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from livekit import rtc
from livekit.agents import Agent


logger = logging.getLogger(__name__)


def debug_streams_enabled() -> bool:
    return os.getenv("AGENT_DEBUG_STREAMS", "false").strip().lower() in {"1", "true", "yes", "on"}


async def _iterate_node_output(value: Any) -> AsyncIterator[Any]:
    if inspect.isawaitable(value):
        value = await value

    if hasattr(value, "__aiter__"):
        async for item in value:
            yield item
        return

    yield value


class StreamDebugLogger:
    def __init__(self, *, enabled: bool, call_id: str | None = None, user_id: str | None = None) -> None:
        self.enabled = enabled
        self.call_id = call_id or "unknown"
        self.user_id = user_id or "unknown"

    @classmethod
    def from_dispatch_metadata(cls, metadata: dict[str, Any]) -> "StreamDebugLogger":
        return cls(
            enabled=debug_streams_enabled(),
            call_id=metadata.get("call_id"),
            user_id=metadata.get("user_id"),
        )

    def _log(self, stage: str, message: str, *args: Any) -> None:
        if not self.enabled:
            return
        logger.info(
            "agent.debug %s call_id=%s user_id=%s " + message,
            stage,
            self.call_id,
            self.user_id,
            *args,
        )

    def log_stt_event(self, event: Any) -> None:
        event_type = str(getattr(event, "type", "unknown"))
        self._log(f"stt.{event_type}", "received=true")

    def log_llm_start(self) -> None:
        self._log("llm.start", "started=true")

    def log_llm_delta(self, text: str) -> None:
        if not text:
            return
        self._log("llm.delta", "characters=%s", len(text))

    def log_llm_complete(self, text: str, *, elapsed_ms: int) -> None:
        self._log("llm.complete", "elapsed_ms=%s characters=%s", elapsed_ms, len(text))

    def log_tts_start(self, text: str) -> None:
        self._log("tts.start", "characters=%s", len(text))

    def log_tts_first_frame(self, *, elapsed_ms: int) -> None:
        self._log("tts.first_frame", "elapsed_ms=%s", elapsed_ms)

    def log_tts_complete(self, text: str, *, frame_count: int, elapsed_ms: int, audio_seconds: float) -> None:
        self._log(
            "tts.complete",
            "elapsed_ms=%s frame_count=%s audio_seconds=%.3f characters=%s",
            elapsed_ms,
            frame_count,
            audio_seconds,
            len(text),
        )

    def log_tts_error(self, text: str, *, error: Exception) -> None:
        self._log(
            "tts.error",
            "characters=%s error_type=%s",
            len(text),
            type(error).__name__,
        )


class InstrumentedAgent(Agent):
    def __init__(self, *, debug_logger: StreamDebugLogger, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._debug_logger = debug_logger

    async def stt_node(self, audio: AsyncIterable[rtc.AudioFrame], model_settings):  # type: ignore[override]
        async for event in _iterate_node_output(super().stt_node(audio, model_settings)):
            self._debug_logger.log_stt_event(event)
            yield event

    async def llm_node(self, chat_ctx, tools, model_settings):  # type: ignore[override]
        started_at = time.perf_counter()
        text_parts: list[str] = []
        self._debug_logger.log_llm_start()

        async for chunk in _iterate_node_output(super().llm_node(chat_ctx, tools, model_settings)):
            if isinstance(chunk, str):
                text_parts.append(chunk)
                self._debug_logger.log_llm_delta(chunk)
            else:
                delta = getattr(chunk, "delta", None)
                content = getattr(delta, "content", None) if delta is not None else None
                if content:
                    text_parts.append(content)
                    self._debug_logger.log_llm_delta(content)
            yield chunk

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        self._debug_logger.log_llm_complete("".join(text_parts), elapsed_ms=elapsed_ms)

    async def tts_node(self, text: AsyncIterable[str], model_settings):  # type: ignore[override]
        started_at = time.perf_counter()
        text_parts: list[str] = []
        first_frame_logged = False
        frame_count = 0
        audio_seconds = 0.0

        async def instrumented_text() -> AsyncIterator[str]:
            first_text = True
            async for chunk in _iterate_node_output(text):
                if isinstance(chunk, str):
                    text_parts.append(chunk)
                    if first_text:
                        self._debug_logger.log_tts_start(chunk)
                        first_text = False
                yield chunk

        try:
            async for frame in _iterate_node_output(super().tts_node(instrumented_text(), model_settings)):
                frame_count += 1
                audio_seconds += getattr(frame, "duration", 0.0)
                if not first_frame_logged:
                    first_frame_logged = True
                    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                    self._debug_logger.log_tts_first_frame(elapsed_ms=elapsed_ms)
                yield frame
        except Exception as exc:
            self._debug_logger.log_tts_error("".join(text_parts), error=exc)
            raise

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        self._debug_logger.log_tts_complete(
            "".join(text_parts),
            frame_count=frame_count,
            elapsed_ms=elapsed_ms,
            audio_seconds=audio_seconds,
        )
