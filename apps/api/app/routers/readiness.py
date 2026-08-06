import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from opentelemetry.trace import SpanKind
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.composition.runtime import get_api_runtime
from app.core.observability import Observability


router = APIRouter()


class ReadinessChecks:
    def __init__(
        self,
        engine: AsyncEngine,
        redis: Redis,
        observability: Observability,
    ) -> None:
        self.engine = engine
        self.redis = redis
        self.observability = observability

    async def check_database(self) -> bool:
        async with self.observability.trace_operation(
            "presvo.dependency.check",
            {
                "presvo.dependency": "database",
                "presvo.operation": "readiness",
            },
            kind=SpanKind.CLIENT,
        ):
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        return True

    async def check_redis(self) -> bool:
        async with self.observability.trace_operation(
            "presvo.dependency.check",
            {
                "presvo.dependency": "redis",
                "presvo.operation": "readiness",
            },
            kind=SpanKind.CLIENT,
        ):
            return bool(await self.redis.ping())

async def run_readiness_checks(
    checks,
    *,
    timeout_seconds: float = 2.0,
) -> dict[str, str]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout_seconds)
    tasks = {
        "database": asyncio.create_task(checks.check_database()),
        "redis": asyncio.create_task(checks.check_redis()),
    }
    try:
        done, _pending = await asyncio.wait(
            tasks.values(),
            timeout=max(0.0, deadline - loop.time()),
        )
        result: dict[str, str] = {}
        for dependency, task in tasks.items():
            if task not in done or task.cancelled():
                result[dependency] = "unavailable"
                continue
            try:
                result[dependency] = "ok" if task.result() else "unavailable"
            except Exception:
                result[dependency] = "unavailable"
        return result
    finally:
        for task in tasks.values():
            if not task.done():
                task.cancel()
            task.add_done_callback(_consume_task_result)


def _consume_task_result(task: asyncio.Task) -> None:
    try:
        task.exception()
    except asyncio.CancelledError:
        return


def get_readiness_checks(request: Request):
    return get_api_runtime(request.app).readiness_checks


@router.get("/readyz")
async def readiness(request: Request) -> JSONResponse:
    dependencies = await run_readiness_checks(get_readiness_checks(request))
    ready = all(value == "ok" for value in dependencies.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ok" if ready else "not_ready",
            "dependencies": dependencies,
        },
    )
