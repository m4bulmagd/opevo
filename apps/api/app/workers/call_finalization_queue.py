from arq.connections import ArqRedis


class CallFinalizationQueue:
    def __init__(self, redis: ArqRedis) -> None:
        self.redis = redis

    async def enqueue(self, payload: dict) -> str:
        job_id = f"call-finalization:{payload['call_id']}"
        await self.redis.enqueue_job("call_finalization_job", payload, _job_id=job_id)
        return job_id
