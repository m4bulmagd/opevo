from typing import Final

from arq.connections import ArqRedis


CALL_LIFECYCLE_QUEUE_NAME: Final = "arq:queue"
BACKGROUND_QUEUE_NAME: Final = "arq:queue:background"
QUEUE_CLASS_CALL_LIFECYCLE: Final = "call_lifecycle"
QUEUE_CLASS_BACKGROUND: Final = "background"


async def enqueue_outbox_wakeup(redis: ArqRedis) -> None:
    await redis.enqueue_job(
        "outbox_delivery_job",
        {},
        _queue_name=BACKGROUND_QUEUE_NAME,
    )
