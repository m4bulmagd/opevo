import asyncio
import time
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.health import router as health_router


class _Checks:
    def __init__(self, *, database=True, redis=True) -> None:
        self.database = database
        self.redis = redis
        self.calls: list[str] = []

    async def check_database(self) -> bool:
        self.calls.append("database")
        if isinstance(self.database, BaseException):
            raise self.database
        if callable(self.database):
            return await self.database()
        return self.database

    async def check_redis(self) -> bool:
        self.calls.append("redis")
        if isinstance(self.redis, BaseException):
            raise self.redis
        if callable(self.redis):
            return await self.redis()
        return self.redis


def _readiness_app(checks: _Checks) -> FastAPI:
    try:
        from app.routers.readiness import router as readiness_router
    except ModuleNotFoundError as error:
        pytest.fail(f"readiness router is required: {error}")
    app = FastAPI()
    app.state.readiness_checks = checks
    app.include_router(health_router)
    app.include_router(readiness_router)
    return app


def test_healthz_never_invokes_dependency_checks() -> None:
    sentinel = RuntimeError("POSTGRES_DSN_SECRET REDIS_CREDENTIAL_SECRET")
    checks = _Checks(database=sentinel, redis=sentinel)

    with TestClient(_readiness_app(checks)) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert checks.calls == []


@pytest.mark.parametrize(
    ("database", "redis", "status_code", "expected"),
    [
        (True, True, 200, {"database": "ok", "redis": "ok"}),
        (False, True, 503, {"database": "unavailable", "redis": "ok"}),
        (True, False, 503, {"database": "ok", "redis": "unavailable"}),
        (
            False,
            False,
            503,
            {"database": "unavailable", "redis": "unavailable"},
        ),
        (
            RuntimeError("postgresql://admin:DSN_SECRET@private-db:5432/app"),
            RuntimeError("redis://:REDIS_SECRET@private-redis:6379/0"),
            503,
            {"database": "unavailable", "redis": "unavailable"},
        ),
    ],
)
def test_readyz_returns_exact_fixed_contract(
    database,
    redis,
    status_code: int,
    expected: dict[str, str],
) -> None:
    with TestClient(_readiness_app(_Checks(database=database, redis=redis))) as client:
        response = client.get("/readyz")

    assert response.status_code == status_code
    assert response.json() == {
        "status": "ok" if status_code == 200 else "not_ready",
        "dependencies": expected,
    }
    rendered = response.text
    for sentinel in (
        "DSN_SECRET",
        "REDIS_SECRET",
        "private-db",
        "private-redis",
        "5432",
        "6379",
    ):
        assert sentinel not in rendered


@pytest.mark.anyio
async def test_readiness_dependency_spans_are_explicit_clients() -> None:
    from opentelemetry.trace import SpanKind

    from app.routers.readiness import ReadinessChecks

    observed: list[tuple[str, SpanKind]] = []

    class Telemetry:
        @asynccontextmanager
        async def trace_operation(self, name, _attributes, *, kind):
            observed.append((name, kind))
            yield None

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _query) -> None:
            return None

    class Engine:
        def connect(self):
            return Connection()

    class Redis:
        async def ping(self) -> bool:
            return True

    checks = ReadinessChecks(
        engine=Engine(),
        redis=Redis(),
        observability=Telemetry(),
    )
    assert await checks.check_database() is True
    assert await checks.check_redis() is True
    assert observed == [
        ("presvo.dependency.check", SpanKind.CLIENT),
        ("presvo.dependency.check", SpanKind.CLIENT),
    ]


@pytest.mark.anyio
async def test_readiness_checks_share_one_deadline_and_preserve_fast_success() -> None:
    try:
        from app.routers.readiness import run_readiness_checks
    except ModuleNotFoundError as error:
        pytest.fail(f"readiness runner is required: {error}")

    started = asyncio.Event()

    async def fast() -> bool:
        started.set()
        return True

    async def blocked() -> bool:
        await started.wait()
        await asyncio.sleep(10)
        return True

    before = time.monotonic()
    result = await run_readiness_checks(
        _Checks(database=fast, redis=blocked),
        timeout_seconds=0.05,
    )
    elapsed = time.monotonic() - before

    assert elapsed < 0.12
    assert result == {"database": "ok", "redis": "unavailable"}


@pytest.mark.anyio
async def test_readiness_hard_deadline_does_not_await_cancellation_resistant_check() -> None:
    from app.routers.readiness import run_readiness_checks

    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def resistant() -> bool:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
            return True

    before = time.monotonic()
    result = await run_readiness_checks(
        _Checks(database=True, redis=resistant),
        timeout_seconds=0.03,
    )
    elapsed = time.monotonic() - before

    assert elapsed < 0.1
    assert result == {"database": "ok", "redis": "unavailable"}
    await asyncio.wait_for(cancelled.wait(), timeout=0.05)
    release.set()
    await asyncio.sleep(0)


@pytest.mark.anyio
async def test_readiness_caller_cancellation_cancels_every_dependency_check() -> None:
    from app.routers.readiness import run_readiness_checks

    database_started = asyncio.Event()
    redis_started = asyncio.Event()
    database_cancelled = asyncio.Event()
    redis_cancelled = asyncio.Event()

    async def blocked(started: asyncio.Event, cancelled: asyncio.Event) -> bool:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    checks = _Checks(
        database=lambda: blocked(database_started, database_cancelled),
        redis=lambda: blocked(redis_started, redis_cancelled),
    )
    runner = asyncio.create_task(
        run_readiness_checks(checks, timeout_seconds=10)
    )
    await asyncio.gather(database_started.wait(), redis_started.wait())
    runner.cancel()

    with pytest.raises(asyncio.CancelledError):
        await runner
    await asyncio.wait_for(database_cancelled.wait(), timeout=0.05)
    await asyncio.wait_for(redis_cancelled.wait(), timeout=0.05)


@pytest.mark.anyio
async def test_readiness_uses_app_settings_and_closes_after_partial_startup(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main as main_module

    configured = settings.model_copy(
        update={
            "app_env": "development",
            "database_url": "postgresql+asyncpg://captured-db/app",
            "redis_url": "redis://captured-redis/4",
        }
    )
    observed: dict[str, object] = {}

    class Resource:
        async def aclose(self) -> None:
            observed["closed"] = True

    async def create_checks(settings_arg):
        observed["settings"] = settings_arg
        return Resource()

    async def fail_after_readiness(_redis_url):
        raise RuntimeError("later startup failure")

    monkeypatch.setattr(
        main_module,
        "create_readiness_checks",
        create_checks,
        raising=False,
    )
    monkeypatch.setattr(main_module, "create_arq_pool", fail_after_readiness)

    app = main_module.create_app(configured)
    with pytest.raises(RuntimeError, match="later startup failure"):
        async with app.router.lifespan_context(app):
            pass

    assert observed["settings"] is configured
    assert observed["closed"] is True


@pytest.mark.anyio
async def test_readiness_factory_disposes_engine_when_redis_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import readiness as readiness_module

    observed: dict[str, bool] = {}

    class Engine:
        async def dispose(self) -> None:
            observed["engine_disposed"] = True

    def fail_redis(_url):
        raise RuntimeError("redis construction failed")

    monkeypatch.setattr(
        readiness_module,
        "create_async_engine",
        lambda *_args, **_kwargs: Engine(),
    )
    monkeypatch.setattr(readiness_module.Redis, "from_url", fail_redis)
    settings = type(
        "Settings",
        (),
        {"database_url": "sqlite+aiosqlite://", "redis_url": "redis://invalid"},
    )()

    with pytest.raises(RuntimeError, match="redis construction failed"):
        await readiness_module.create_readiness_checks(settings)

    assert observed == {"engine_disposed": True}


def test_production_app_healthz_bypasses_observability_middleware(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main as main_module
    from app.core import observability as observability_module

    app = main_module.create_app(settings)

    def forbidden_lookup(_request):
        raise AssertionError("liveness must not look up telemetry")

    monkeypatch.setattr(
        observability_module,
        "get_request_observability",
        forbidden_lookup,
    )

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
