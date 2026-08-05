import asyncio
import logging
import math
import time
from collections.abc import Callable
from typing import Any

from app.core.observability import Observability


logger = logging.getLogger(__name__)

_SAFE_QUEUE_CLASSES = frozenset({"call_lifecycle", "background"})


class QueueObserver:
    def __init__(
        self,
        redis: Any,
        telemetry: Observability,
        *,
        queue_name: str,
        queue_class: str,
        interval_seconds: float = 15.0,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis
        self._telemetry = telemetry
        self._queue_name = queue_name
        self._queue_class = (
            queue_class if queue_class in _SAFE_QUEUE_CLASSES else "unknown"
        )
        self._interval_seconds = interval_seconds
        self._now = now
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    def start(self) -> None:
        if self._closed or self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def sample(self) -> None:
        depth_reply = await self._redis.zcard(self._queue_name)
        depth = depth_reply if type(depth_reply) is int and depth_reply >= 0 else 0
        oldest_due_age_seconds = 0.0
        if depth:
            replies = await self._redis.zrange(
                self._queue_name,
                0,
                0,
                withscores=True,
            )
            oldest_due_age_seconds = self._oldest_due_age_seconds(replies)
        self._telemetry.record_worker_queue_snapshot(
            self._queue_class,
            depth=depth,
            oldest_due_age_seconds=oldest_due_age_seconds,
        )

    async def aclose(self) -> None:
        self._closed = True
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        result = (await asyncio.gather(task, return_exceptions=True))[0]
        if isinstance(result, BaseException) and not isinstance(
            result,
            asyncio.CancelledError,
        ):
            raise result

    async def _run(self) -> None:
        while True:
            try:
                await self.sample()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "worker queue observation failed queue_class=%s error_type=unknown",
                    self._queue_class,
                )
            await asyncio.sleep(self._interval_seconds)

    def _oldest_due_age_seconds(self, replies: object) -> float:
        if not isinstance(replies, (list, tuple)) or not replies:
            return 0.0
        item = replies[0]
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return 0.0
        score = item[1]
        if type(score) not in (int, float) or not math.isfinite(score):
            return 0.0
        now = self._now()
        if type(now) not in (int, float) or not math.isfinite(now):
            return 0.0
        return max(0.0, now - score / 1000.0)
