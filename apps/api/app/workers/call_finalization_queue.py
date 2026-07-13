from arq.connections import ArqRedis


class CallFinalizationQueue:
    def __init__(self, redis: ArqRedis) -> None:
        self.redis = redis

    async def enqueue(self, payload: dict) -> str:
        if not isinstance(payload, dict) or set(payload) != {"call_id"}:
            raise ValueError("Call finalization payload must contain call_id only")
        job_id = f"call-finalization:{payload['call_id']}"
        await self.redis.enqueue_job("call_finalization_job", payload, _job_id=job_id)
        return job_id
