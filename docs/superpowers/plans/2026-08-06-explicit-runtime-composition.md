# Explicit Runtime Composition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the API, call-lifecycle worker, background worker, and LiveKit agent explicit typed composition roots, deterministic resource ownership, and testable dependency boundaries while preserving existing product, queue, transaction, and provider behavior.

**Architecture:** Each executable boundary loads validated settings once and manually constructs a focused runtime. FastAPI and ARQ cross their untyped framework state once through typed accessors, while business functions receive named dependencies. Process resources use close-once lifecycle management, database sessions remain operation-scoped, and one agent shutdown coordinator orders call finalization before process-runtime and observability cleanup because LiveKit executes independent shutdown callbacks concurrently.

**Tech Stack:** Python 3.13, FastAPI 0.115+, ARQ 0.26+, SQLAlchemy 2 async ORM, Redis 5 async client, LiveKit Agents 1.4.4, LiveKit API 1.x, HTTPX 0.28, Pydantic Settings 2.x, pytest 9/AnyIO, pytest-cov 7, Ruff 0.15, mypy 2.3, uv.

## Global Constraints

- Follow the owner-approved contract in `docs/superpowers/specs/2026-08-06-explicit-runtime-composition-design.md`.
- Implement the approved combined direction **6A-1A + 6A-2A + 6A-3A + 6A-4A + 6A-5A + 6A-6A + 6A-7A**.
- Use an isolated worktree created through `superpowers:using-git-worktrees` when execution begins.
- Do not inspect or modify `Opevo_frontend/` or `.worktrees/shadcn-activation-preview`.
- Do not inspect real `.env` files. This work does not require environment changes.
- Do not touch `/tmp/opevo-voice-e2e.override.yaml`, `/tmp/opevo-telnyx-e2e.override.yaml`, or `/tmp/opevo-clerk-e2e.override.yaml`.
- Keep `worker-lifecycle` and `worker-background` as separate processes and queues while retaining their source under `apps/api`.
- Preserve queue names, worker registrations, concurrency, semantic/hard timeouts, retries, result retention, health keys, and shutdown grace.
- Preserve API contracts, authentication behavior, database schema/data, transaction and locking boundaries, outbox topics/payloads, call flow, activation, billing, telephony, recording, and provider failure classifications.
- Realtime remains deferred. Preserve the current disabled/enabled behavior without expanding the feature.
- Do not mutate deployments, credentials, external providers, or live services.
- Do not add a DI framework, service locator, dynamic registration, reflection-based autowiring, compatibility facade, or generic workflow/task framework.
- Runtime objects contain process-owned resources only. Business services and use cases receive named dependencies, never an entire runtime.
- Required dependencies fail during composition. Supported fake or disabled behavior uses an explicit adapter or explicit optional runtime field; real modes never silently fall back.
- `get_settings()` is allowed only at executable boundaries and its definition. It must not remain in routers, webhooks, services, providers, jobs, outbox handlers, pipeline builders, or agent clients.
- ARQ application dependencies cross `ctx` through one typed runtime key. ARQ-owned `redis`, `job_try`, and `enqueue_time` remain valid framework metadata.
- Database engines/pools are process-scoped; sessions and repositories are operation-scoped.
- Every owned resource is registered for cleanup immediately after construction. Partial startup unwinds in reverse order.
- Agent API/Redis transports are owned by `AgentProcessRuntime`. Because LiveKit 1.4.4 runs registered shutdown callbacks concurrently, one callback must order session finalization, process-runtime closure, and observability shutdown; these actions must not be registered independently.
- LiveKit 1.4.4 invokes `prewarm_fnc` synchronously. Agent transport factories used there must therefore be construction-only and perform no network I/O or asynchronous acquisition. `AgentApiClient` remains HTTP-client-lazy and `Redis.from_url` must not connect during construction. A future transport that cannot honor this contract must be acquired in the async entrypoint, not forced into synchronous prewarm.
- Do not add dependencies or modify any `uv.lock` file.
- Use `apply_patch` for source and documentation edits.
- Focused pytest runs do not collect repository coverage. Coverage is collected only at full API and agent gates.
- Every task follows RED → GREEN → focused static checks → scoped commit.

---

## Locked File Structure

### Create

- `apps/api/app/composition/__init__.py` — empty package marker; no re-exports.
- `apps/api/app/composition/lifecycle.py` — close-once asynchronous cleanup around `AsyncExitStack`.
- `apps/api/app/composition/runtime.py` — construction-free API/worker runtime types, errors, and typed framework-state accessors.
- `apps/api/app/composition/api.py` — API runtime construction only.
- `apps/api/app/composition/workers.py` — lifecycle/background worker runtime construction only.
- `apps/api/tests/composition/test_lifecycle.py` — close-once, cancellation, and partial-startup cleanup tests.
- `apps/api/tests/composition/test_api_composition.py` — API runtime construction and lifespan ownership tests.
- `apps/api/tests/composition/test_worker_composition.py` — worker runtime type, startup, shutdown, and provider-mode tests.
- `apps/api/tests/test_composition_architecture.py` — AST/import dependency guards and forbidden-global regression checks.
- `apps/agent/agent/composition.py` — `AgentProcessRuntime`, typed process-data access, and explicit client/publisher builders.
- `apps/agent/tests/test_composition.py` — agent process/call resource construction and ownership tests.
- `apps/agent/tests/test_composition_architecture.py` — agent forbidden-global and composition-direction guards.

### Delete after references are migrated

- `apps/api/app/workers/jobs/notifications.py` — unregistered production-dead job.
- The notification-job-only section of `apps/api/tests/workers/test_individual_jobs.py`.
- `get_engine()` and `get_session_factory()` cached accessors from `apps/api/app/core/database.py`.
- `get_redis_client()` and the optional settings fallback in `apps/api/app/core/redis.py`.
- `get_s3_storage()` from `apps/api/app/providers/storage/s3.py`.
- `get_telephony_provider()` from `apps/api/app/providers/telephony/telnyx.py`.
- Every service/provider/job fallback that constructs settings, observability, sessions, or providers implicitly.

### Primary production files modified

- `apps/api/app/main.py`
- `apps/api/app/core/auth.py`
- `apps/api/app/core/database.py`
- `apps/api/app/core/dispatch_token.py`
- `apps/api/app/core/verification_token.py`
- `apps/api/app/core/observability.py`
- `apps/api/app/core/redis.py`
- `apps/api/app/routers/activation.py`
- `apps/api/app/routers/agent.py`
- `apps/api/app/routers/billing.py`
- `apps/api/app/routers/calls.py`
- `apps/api/app/routers/dashboard.py`
- `apps/api/app/routers/development.py`
- `apps/api/app/routers/readiness.py`
- `apps/api/app/routers/websocket.py`
- `apps/api/app/services/recording_service.py`
- `apps/api/app/webhooks/livekit.py`
- All API services/providers identified by the explicit-construction sweeps in Tasks 4 and 5.
- `apps/api/app/workers/arq_worker.py`
- `apps/api/app/workers/job_policy.py`
- `apps/api/app/workers/jobs/call_finalization.py`
- `apps/api/app/workers/jobs/call_reconciliation.py`
- `apps/api/app/workers/jobs/verification_expiry.py`
- `apps/api/app/workers/outbox/delivery.py`
- `apps/api/app/workers/outbox/registry.py`
- Every topic-handler module under `apps/api/app/workers/outbox/`.
- `apps/agent/agent/main.py`
- `apps/agent/agent/pipeline_factory.py`
- `apps/agent/agent/api_client.py`
- `apps/agent/agent/event_publisher.py`
- `apps/agent/agent/session_runtime.py`
- `apps/agent/agent/verification_runtime.py`

### Test suites migrated from global patching/state mutation

- `apps/api/tests/conftest.py`
- `apps/api/tests/realtime/test_runtime_resources.py`
- `apps/api/tests/realtime/test_websocket_lifecycle.py`
- `apps/api/tests/test_readiness.py`
- `apps/api/tests/test_collection_environment.py`
- `apps/api/tests/auth/test_jwt_auth.py`
- `apps/api/tests/auth/test_verification_token.py`
- `apps/api/tests/auth/test_local_auth.py`
- `apps/api/tests/activation/test_verification_completion_api.py`
- `apps/api/tests/activation/test_development_api.py`
- `apps/api/tests/agent/test_agent_config_api.py`
- `apps/api/tests/agent/test_call_completion.py`
- `apps/api/tests/agent/test_transcript_append.py`
- `apps/api/tests/billing/test_stripe_webhooks.py`
- `apps/api/tests/livekit/test_dispatch_webhook.py`
- `apps/api/tests/livekit/test_durable_dispatch_service.py`
- `apps/api/tests/providers/test_livekit_dispatch_provider.py`
- `apps/api/tests/services/test_livekit_recording_service.py`
- `apps/api/tests/services/test_onboarding_service.py`
- `apps/api/tests/services/test_safe_service_exceptions.py`
- `apps/api/tests/services/test_subscription_service_sessions.py`
- `apps/api/tests/workers/test_arq_worker.py`
- `apps/api/tests/workers/test_call_finalization_worker.py`
- `apps/api/tests/workers/test_call_reconciliation_wakeup.py`
- `apps/api/tests/workers/test_verification_expiry_job.py`
- `apps/api/tests/workers/test_worker_observability_lifecycle.py`
- `apps/api/tests/workers/test_outbox_architecture.py`
- `apps/api/tests/workers/test_account_deactivation.py`
- `apps/api/tests/workers/test_provider_cleanup.py`
- `apps/api/tests/workers/test_phone_provisioning_cleanup.py`
- `apps/api/tests/workers/test_phone_routing_readiness.py`
- `apps/api/tests/workers/test_livekit_dispatch_outbox.py`
- `apps/api/tests/workers/test_forwarding_verification_dispatch_outbox.py`
- `apps/api/tests/workers/test_post_call_outbox_handlers.py`
- `apps/api/tests/workers/test_post_call_jobs.py`
- `apps/api/tests/workers/test_recording_reconciliation.py`
- `apps/api/tests/integration/test_outbox_delivery.py`
- `apps/agent/tests/test_api_client.py`
- `apps/agent/tests/test_main.py`
- `apps/agent/tests/test_pipeline_factory.py`
- `apps/agent/tests/test_realtime_compatibility.py`
- `apps/agent/tests/test_runtime_validation.py`
- `apps/agent/tests/test_session_runtime.py`
- `apps/agent/tests/test_session_runtime_errors.py`
- `apps/agent/tests/test_verification_runtime.py`

---

## Phase One — API Composition

### Task 1: Add close-once lifecycle and pure database factories

**Files:**
- Create: `apps/api/app/composition/__init__.py`
- Create: `apps/api/app/composition/lifecycle.py`
- Create: `apps/api/tests/composition/test_lifecycle.py`
- Modify: `apps/api/app/core/database.py`
- Test: `apps/api/tests/conftest.py`

**Interfaces:**
- Produces: `RuntimeCleanup(stack: AsyncExitStack)` with `async aclose() -> None`.
- Produces: `AsyncSessionFactory = async_sessionmaker[AsyncSession]`.
- Produces: `create_database_engine(database_url: str) -> AsyncEngine`.
- Produces: `create_session_factory(engine: AsyncEngine) -> AsyncSessionFactory`.
- Preserves temporarily: legacy cached accessors until Task 3 migrates request and worker callers.

- [ ] **Step 1: Write close-once lifecycle tests**

Create `tests/composition/test_lifecycle.py` with direct assertions:

```python
import asyncio
from contextlib import AsyncExitStack

import pytest

from app.composition.lifecycle import RuntimeCleanup


@pytest.mark.anyio
async def test_runtime_cleanup_closes_callbacks_once_in_reverse_order() -> None:
    calls: list[str] = []
    stack = AsyncExitStack()
    stack.push_async_callback(lambda: _record(calls, "first"))
    stack.push_async_callback(lambda: _record(calls, "second"))
    cleanup = RuntimeCleanup(stack)

    await asyncio.gather(cleanup.aclose(), cleanup.aclose())
    await cleanup.aclose()

    assert calls == ["second", "first"]


@pytest.mark.anyio
async def test_runtime_cleanup_continues_after_waiter_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    closed = asyncio.Event()

    async def close_resource() -> None:
        started.set()
        await release.wait()
        closed.set()

    stack = AsyncExitStack()
    stack.push_async_callback(close_resource)
    cleanup = RuntimeCleanup(stack)
    waiter = asyncio.create_task(cleanup.aclose())
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await cleanup.aclose()

    assert closed.is_set()


async def _record(calls: list[str], value: str) -> None:
    calls.append(value)
```

- [ ] **Step 2: Run RED for the missing lifecycle module**

Run from `apps/api`:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/composition/test_lifecycle.py
```

Expected: collection fails because `app.composition.lifecycle` does not exist.

- [ ] **Step 3: Implement the lifecycle primitive and pure database factories**

Create an empty `app/composition/__init__.py`. Implement `RuntimeCleanup` using one retained close task so cancellation of one waiter cannot cancel cleanup:

```python
class RuntimeCleanup:
    def __init__(self, stack: AsyncExitStack) -> None:
        self._stack = stack
        self._lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None

    async def aclose(self) -> None:
        async with self._lock:
            if self._close_task is None:
                self._close_task = asyncio.create_task(self._stack.aclose())
            close_task = self._close_task
        await asyncio.shield(close_task)
```

In `core/database.py`, add pure constructors without reading settings:

```python
AsyncSessionFactory = async_sessionmaker[AsyncSession]


def create_database_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        database_url,
        future=True,
        pool_size=10,
        max_overflow=20,
        pool_recycle=1800,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> AsyncSessionFactory:
    return async_sessionmaker(engine, expire_on_commit=False)
```

For SQLite, preserve SQLAlchemy compatibility by omitting unsupported pool-size arguments when `database_url` begins with `sqlite`.

- [ ] **Step 4: Run GREEN and focused static checks**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/composition/test_lifecycle.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app/composition app/core/database.py tests/composition/test_lifecycle.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app/composition/lifecycle.py app/core/database.py
```

Expected: all commands exit zero and concurrent/repeated closure executes callbacks once.

- [ ] **Step 5: Commit the lifecycle foundation**

```bash
git add apps/api/app/composition apps/api/app/core/database.py apps/api/tests/composition/test_lifecycle.py
git commit -m "refactor(api): add runtime lifecycle foundation"
```

### Task 2: Build and own the API runtime

**Files:**
- Create: `apps/api/app/composition/runtime.py`
- Create: `apps/api/app/composition/api.py`
- Create: `apps/api/tests/composition/test_api_composition.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/routers/readiness.py`
- Modify: `apps/api/app/core/redis.py`
- Modify: `apps/api/app/core/runtime_validation.py`
- Modify: `apps/api/app/core/observability.py`
- Modify: `apps/api/app/providers/storage/s3.py`
- Modify: `apps/api/tests/realtime/test_runtime_resources.py`
- Modify: `apps/api/tests/test_deployment_readiness.py`
- Modify: `apps/api/tests/test_readiness.py`

**Interfaces:**
- Produces in `composition/runtime.py`: `ApiRuntime` with settings, engine, session factory, Redis, observability, auth, readiness, storage, optional ARQ/realtime/LiveKit resources, and `aclose()`.
- Produces: `async build_api_runtime(settings: Settings, *, engine_factory, redis_factory, observability_factory, auth_factory, readiness_factory, storage_factory, arq_pool_factory, realtime_service_factory, webhook_receiver_factory, recording_service_factory) -> ApiRuntime`.
- Produces: `ApiRuntimeBuilder = Callable[[Settings], Awaitable[ApiRuntime]]`.
- Changes: `create_app(settings: Settings | None = None, *, runtime_builder: ApiRuntimeBuilder = build_api_runtime) -> FastAPI`.

Every factory keyword has a concrete production default, so
`build_api_runtime(settings)` is the real manual composition root. Tests
override only the factories whose resource behavior they need to observe. The
defaults are injection seams, not optional provider fallbacks.

- [ ] **Step 1: Write API construction tests with explicit fakes**

Test these cases in `test_api_composition.py`:

```python
@pytest.mark.anyio
runtime = await build_api_runtime(
    configured_settings,
    engine_factory=engine_factory,
    redis_factory=redis_factory,
    observability_factory=observability_factory,
    auth_factory=auth_factory,
    readiness_factory=readiness_factory,
    storage_factory=storage_factory,
    arq_pool_factory=arq_pool_factory,
    realtime_service_factory=realtime_service_factory,
    webhook_receiver_factory=webhook_receiver_factory,
    recording_service_factory=recording_service_factory,
)
assert runtime.settings is configured_settings
await runtime.aclose()
assert closed_resources == [
    "arq_pool",
    "storage",
    "auth",
    "redis",
    "engine",
    "observability",
]

with pytest.raises(RuntimeError, match="late construction failure"):
    await build_api_runtime(
        configured_settings,
        engine_factory=engine_factory,
        redis_factory=redis_factory,
        observability_factory=observability_factory,
        auth_factory=auth_factory,
        readiness_factory=readiness_factory,
        storage_factory=storage_factory,
        arq_pool_factory=arq_pool_factory,
        realtime_service_factory=realtime_service_factory,
        webhook_receiver_factory=fail_webhook_receiver,
        recording_service_factory=recording_service_factory,
    )
assert every_opened_resource_closed_once()
```

Use concrete local fakes for every factory argument; do not patch `app.main` globals.

Add a deployment-readiness assertion that API validation does not require
Gemini/summary settings, because summary generation is owned only by the
background worker. Keep all API-owned auth, billing, telephony/carrier,
LiveKit, storage, Redis, database, and dispatch-token checks unchanged.

- [ ] **Step 2: Run RED**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/composition/test_api_composition.py \
  tests/test_deployment_readiness.py
```

Expected: collection fails because `app.composition.api` does not exist.

- [ ] **Step 3: Implement `ApiRuntime` and explicit construction**

The runtime shape is fixed:

```python
@dataclass(slots=True)
class ApiRuntime:
    settings: Settings
    engine: AsyncEngine
    session_factory: AsyncSessionFactory
    redis_client: Redis
    observability: Observability
    auth_provider: AuthProvider
    readiness_checks: ReadinessChecks
    storage_provider: StorageProvider
    arq_pool: ArqRedis | None
    call_finalization_queue: CallFinalizationQueue | None
    realtime_service: RealtimeService | None
    livekit_webhook_receiver: object | None
    livekit_recording_service: LiveKitRecordingService | None
    _cleanup: RuntimeCleanup

    async def aclose(self) -> None:
        await self._cleanup.aclose()
```

`build_api_runtime` must:

1. call `validate_api_runtime(settings)` before opening a resource;
2. create `AsyncExitStack`;
3. initialize observability and register bounded shutdown;
4. create engine/session factory and register `engine.dispose`;
5. create Redis from the explicit URL and register `aclose`;
6. create auth and readiness from explicit dependencies, registering only the
   auth provider because readiness borrows the runtime engine and Redis client;
7. create S3 storage from explicit settings/observability;
8. create ARQ only outside test mode;
9. create realtime only when enabled through the injected factory, borrowing the owned Redis client;
10. create LiveKit receiver/recording service through injected factories only when all LiveKit credentials exist;
11. close the stack and re-raise if any later step fails.

Do not publish partial state and do not fall back to fake providers.

Narrow `validate_api_runtime` to API-owned dependencies. Remove
`summary_provider`, `summary_model`, and `gemini_api_key` from its production
requirements; Task 6 moves those checks to background-worker validation. This
is dependency-boundary isolation, not a relaxation of the system-wide
production requirement.

`create_app` loads the `Settings` object and passes it to the runtime builder;
it does not call `validate_api_runtime` a second time. Validation therefore
occurs exactly once during lifespan startup and still completes before the
server accepts requests. A custom test runtime builder assumes responsibility
for the explicit settings supplied by that test.

- [ ] **Step 4: Make readiness and Redis ownership explicit**

Change constructors to require dependencies:

```python
class ReadinessChecks:
    def __init__(self, engine: AsyncEngine, redis: Redis, observability: Observability) -> None:
        self.engine = engine
        self.redis = redis
        self.observability = observability


class RedisEventBus:
    def __init__(self, redis_client: Redis) -> None:
        self.redis_client = redis_client
```

`ReadinessChecks` and `RedisEventBus` borrow runtime resources and no longer close them. Delete the Redis event-bus URL/global fallback. Keep `create_arq_pool(redis_url: str)` explicit and remove its optional argument.

- [ ] **Step 5: Move API lifespan ownership to the runtime**

Reduce `main.py` lifespan to this behavior:

```python
def _lifespan(settings: Settings, runtime_builder: ApiRuntimeBuilder):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = await runtime_builder(settings)
        app.state.runtime = runtime
        try:
            yield
        finally:
            app.state.runtime = None
            await runtime.aclose()
    return lifespan
```

Realtime fanout creation/cancellation belongs to `build_api_runtime` and its cleanup stack. `main.py` retains router registration, CORS, rate limiting, and middleware installation only.

- [ ] **Step 6: Run API runtime GREEN tests**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/composition/test_api_composition.py \
  tests/realtime/test_runtime_resources.py \
  tests/realtime/test_websocket_lifecycle.py \
  tests/test_deployment_readiness.py \
  tests/test_readiness.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app/composition/api.py app/main.py app/routers/readiness.py app/core/redis.py tests/composition/test_api_composition.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app/composition/api.py app/main.py app/routers/readiness.py app/core/redis.py
```

Expected: startup/shutdown, disabled realtime, enabled realtime, partial failure, and duplicate close pass without deep patches in `app.main`.

- [ ] **Step 7: Commit API runtime ownership**

```bash
git add apps/api/app/composition/runtime.py apps/api/app/composition/api.py \
  apps/api/app/main.py \
  apps/api/app/routers/readiness.py apps/api/app/core/redis.py \
  apps/api/app/core/observability.py apps/api/app/core/runtime_validation.py \
  apps/api/app/providers/storage/s3.py \
  apps/api/tests/composition/test_api_composition.py \
  apps/api/tests/realtime/test_runtime_resources.py \
  apps/api/tests/realtime/test_websocket_lifecycle.py \
  apps/api/tests/test_deployment_readiness.py \
  apps/api/tests/test_readiness.py
git commit -m "refactor(api): own process resources in api runtime"
```

### Task 3: Route FastAPI dependencies through `ApiRuntime`

**Files:**
- Modify: `apps/api/app/composition/api.py`
- Modify: `apps/api/app/composition/runtime.py`
- Modify: `apps/api/app/core/database.py`
- Modify: `apps/api/app/core/auth.py`
- Modify: `apps/api/app/core/observability.py`
- Modify: `apps/api/app/routers/agent.py`
- Modify: `apps/api/app/routers/dashboard.py`
- Modify: `apps/api/app/routers/development.py`
- Modify: `apps/api/app/routers/readiness.py`
- Modify: `apps/api/app/routers/websocket.py`
- Modify: `apps/api/app/services/recording_service.py`
- Modify: `apps/api/app/webhooks/livekit.py`
- Modify: `apps/api/tests/conftest.py`
- Modify: API tests that directly mutate the replaced `app.state.*` fields.

**Interfaces:**
- Produces in `composition/runtime.py`: `get_api_runtime(request: Request) -> ApiRuntime`.
- Produces in `composition/runtime.py`: `get_api_settings(request: Request) -> Settings`.
- Changes: `get_session(request: Request) -> AsyncIterator[AsyncSession]`.
- Changes: all API state accessors to read the typed runtime.
- Deletes: per-resource `app.state.auth_provider`, `arq_pool`, `call_finalization_queue`, `observability`, `readiness_checks`, `realtime_service`, and `livekit_webhook_receiver` compatibility state.

- [ ] **Step 1: Write typed state and session failure tests**

Add tests proving:

```python
def test_get_api_runtime_rejects_missing_state() -> None:
    request = request_with_state(runtime=None)
    with pytest.raises(ApiRuntimeConfigurationError, match="API runtime is not initialized"):
        get_api_runtime(request)


@pytest.mark.anyio
async def test_get_session_rolls_back_and_closes_on_handler_failure(
    api_runtime: ApiRuntime,
) -> None:
    request = cast(
        Request,
        SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(runtime=api_runtime))
        ),
    )
    generator = get_session(request)
    session = await anext(generator)
    await session.begin()
    await generator.aclose()
    assert session.in_transaction() is False
```

Use the existing SQLite fixture to assert persisted success and rolled-back failure paths.

- [ ] **Step 2: Run RED for typed accessors**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/composition/test_api_composition.py \
  tests/agent/test_call_completion.py \
  tests/auth/test_jwt_auth.py
```

Expected: new accessor tests fail until request dependencies use `ApiRuntime`.

- [ ] **Step 3: Implement runtime accessors and operation-scoped sessions**

Use an explicit error and exact type check:

```python
class ApiRuntimeConfigurationError(RuntimeError):
    pass


def get_api_runtime(request: Request) -> ApiRuntime:
    runtime = getattr(request.app.state, "runtime", None)
    if not isinstance(runtime, ApiRuntime):
        raise ApiRuntimeConfigurationError("API runtime is not initialized")
    return runtime


def get_api_settings(request: Request) -> Settings:
    return get_api_runtime(request).settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with get_api_runtime(request).session_factory() as session:
        yield session
```

`composition/runtime.py` imports production field types only under
`TYPE_CHECKING` and uses postponed annotations. It must not import
`composition/api.py`, `composition/workers.py`, `core/database.py`, or
`core/auth.py` at runtime. `core/database.py` may import the construction-free
runtime accessor, so `composition/api.py` can import database/auth constructors
without forming a cycle.

`get_auth_provider`, readiness, recording storage, realtime, webhook receiver,
call-finalization queue, and ARQ wakeup accessors must call `get_api_runtime`.
HTTP-facing provider accessors return 503 when an optional integration is
explicitly disabled; they do not expose `None` or return 500 for that state.
Internal missing-required-resource accessors keep the configuration error.

- [ ] **Step 4: Replace direct request-state reads**

Apply these exact mappings:

```text
request.app.state.settings                -> get_api_runtime(request).settings
request.app.state.auth_provider           -> get_api_runtime(request).auth_provider
request.app.state.observability           -> get_api_runtime(request).observability
request.app.state.readiness_checks        -> get_api_runtime(request).readiness_checks
request.app.state.realtime_service        -> get_api_runtime(request).realtime_service
request.app.state.livekit_webhook_receiver-> get_api_runtime(request).livekit_webhook_receiver
request.app.state.arq_pool                -> get_api_runtime(request).arq_pool
request.app.state.call_finalization_queue -> get_api_runtime(request).call_finalization_queue
```

`get_recording_service` receives `Request`, borrows `runtime.storage_provider`, and returns a lightweight `RecordingService`.

- [ ] **Step 5: Make the shared test app use real composition**

In `tests/conftest.py`:

1. build a SQLite URL first;
2. create schema using a temporary setup engine;
3. call `create_app(settings.model_copy(update={"database_url": database_url}))`;
4. remove the `get_session` dependency override;
5. remove engine/session/Redis cache clearing;
6. keep external systems fake through test settings and explicit dependency overrides only at HTTP boundaries.

The `settings` fixture returns `Settings()` from the controlled pytest
environment and never calls `get_settings()`. Composition tests build settings
directly from keyword arguments and do not read the environment.

- [ ] **Step 6: Run the request-boundary suite**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/auth tests/agent tests/calls tests/dashboard \
  tests/activation/test_development_api.py \
  tests/realtime/test_websocket_lifecycle.py \
  tests/livekit/test_dispatch_webhook.py \
  tests/test_collection_environment.py \
  tests/test_readiness.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests/conftest.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app/composition app/core app/routers app/webhooks
```

Expected: request/session behavior passes with no per-resource application-state compatibility aliases.

- [ ] **Step 7: Commit typed FastAPI boundaries**

```bash
git add apps/api/app apps/api/tests/conftest.py apps/api/tests/auth \
  apps/api/tests/agent apps/api/tests/calls apps/api/tests/dashboard \
  apps/api/tests/activation/test_development_api.py \
  apps/api/tests/realtime apps/api/tests/livekit/test_dispatch_webhook.py \
  apps/api/tests/test_collection_environment.py apps/api/tests/test_readiness.py
git commit -m "refactor(api): route dependencies through typed runtime"
```

### Task 4: Make API domain policy dependencies explicit

**Files:**
- Modify: `apps/api/app/core/dispatch_token.py`
- Modify: `apps/api/app/core/verification_token.py`
- Modify: `apps/api/app/services/agent_config_service.py`
- Modify: `apps/api/app/services/customer_readiness_service.py`
- Modify: `apps/api/app/services/call_reconciliation_service.py`
- Modify: `apps/api/app/services/carrier_lookup_service.py`
- Modify: `apps/api/app/services/telephony_service.py`
- Modify: `apps/api/app/services/livekit_dispatch_service.py`
- Modify: `apps/api/app/services/onboarding_service.py`
- Modify: `apps/api/app/services/account_lifecycle_service.py`
- Modify: `apps/api/app/routers/activation.py`
- Modify: `apps/api/app/routers/agent.py`
- Modify: `apps/api/app/webhooks/livekit.py`
- Modify: `apps/api/app/workers/jobs/call_reconciliation.py`
- Modify: `apps/api/app/workers/outbox/customer_dispatch.py`
- Modify: directly affected service, auth-token, activation, agent, LiveKit, and reconciliation tests.

**Interfaces:**
- Produces: immutable `DispatchTokenConfig(secret: str, ttl_seconds: int)` and `dispatch_token_config(settings: Settings) -> DispatchTokenConfig` at composition boundaries.
- Changes: token create/verify helpers require `DispatchTokenConfig`.
- Changes: activation policy is passed as `activation_flow_enabled: bool`.
- Changes: provider-using services require provider/settings dependencies.

- [ ] **Step 1: Add RED tests proving global settings are forbidden**

For each constructor/function below, add a test that passes a deliberately different explicit value than the controlled environment and asserts the explicit value wins. Replace monkeypatches of `get_settings` with constructor arguments.

The locked signatures are:

```python
def create_dispatch_token(
    call_id: str,
    user_id: str,
    agent_config_id: str,
    *,
    config: DispatchTokenConfig,
) -> str:

def verify_dispatch_token(
    token: str,
    expected_call_id: str,
    expected_user_id: str | None = None,
    *,
    config: DispatchTokenConfig,
) -> dict:

class AgentConfigService:
    def __init__(
        self,
        session: AsyncSession,
        agent_config_repository: AgentConfigRepository,
        readiness_service: CustomerReadinessService,
        *,
        activation_flow_enabled: bool,
        arq_pool=None,
    ) -> None:

class CustomerReadinessService:
    def __init__(self, session: AsyncSession, *, activation_flow_enabled: bool) -> None:

class CallReconciliationService:
    def __init__(self, session_factory: AsyncSessionFactory, *, settings: Settings) -> None:

class CarrierLookupService:
    def __init__(self, session: AsyncSession | None, *, provider: CarrierLookupProvider) -> None:

class TelephonyService:
    def __init__(self, session: AsyncSession, *, provider: TelephonyProvider) -> None:
```

The code block locks function headers only; each existing method body is moved
under its new header with only the documented dependency lookup replaced.

`LiveKitDispatchService` receives `activation_flow_enabled: bool` and never calls settings itself.

- [ ] **Step 2: Run focused RED tests**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/auth/test_jwt_auth.py \
  tests/auth/test_verification_token.py \
  tests/activation/test_verification_completion_api.py \
  tests/agent/test_agent_config_api.py \
  tests/services/test_onboarding_service.py \
  tests/livekit/test_durable_dispatch_service.py \
  tests/workers/test_call_reconciliation_wakeup.py \
  tests/workers/test_livekit_dispatch_outbox.py
```

Expected: tests fail on old signatures or observe environment-derived behavior.

- [ ] **Step 3: Implement explicit token configuration**

Validate secret and TTL exactly once in `dispatch_token_config(settings)`.
`verification_token.py` accepts the same config for creation and verification.
FastAPI dependencies get the config from `get_api_settings(request)`. The
current customer-dispatch handler derives one config at its outer boundary so
this task remains green; Task 8 moves that derivation to worker composition and
binds the config into the handler.

Do not keep default config parameters. Token tests construct `DispatchTokenConfig` directly and retain all malformed/expired/algorithm/correlation assertions.

- [ ] **Step 4: Inject activation and reconciliation policy**

Add required constructor fields and propagate them through all nested construction:

```text
activation router -> CustomerReadinessService / AgentConfigService
onboarding service -> CustomerReadinessService
account lifecycle service -> CustomerReadinessService
LiveKit webhook -> LiveKitDispatchService
current call-reconciliation job -> CallReconciliationService
```

Task 7 replaces the current job's explicit settings argument with the captured
call-lifecycle runtime settings.

Keep repositories operation-scoped and keep existing defaults only for repositories/clock collaborators that are cheap and local. Remove the unused `dispatch_client` compatibility argument from `LiveKitDispatchService` and update callers/tests in the same patch.

- [ ] **Step 5: Inject carrier and telephony providers**

Router/composition code calls provider factories with explicit settings, then
passes the resulting provider to the service. `CarrierLookupService` and
`TelephonyService` no longer import provider factories. Task 5 adds explicit
observability to the provider factory signatures and updates these boundary
calls in the same commit as the constructor change.

- [ ] **Step 6: Prove no global policy lookups remain**

```bash
! rg -n "get_settings\(" \
  app/core/dispatch_token.py app/core/verification_token.py \
  app/services/agent_config_service.py \
  app/services/customer_readiness_service.py \
  app/services/call_reconciliation_service.py \
  app/services/carrier_lookup_service.py \
  app/services/telephony_service.py \
  app/services/livekit_dispatch_service.py
```

Expected: `rg` returns no matches and the shell command exits zero.

- [ ] **Step 7: Run GREEN and static checks**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/auth tests/activation tests/agent \
  tests/services/test_onboarding_service.py \
  tests/livekit/test_durable_dispatch_service.py \
  tests/workers/test_call_reconciliation_wakeup.py \
  tests/workers/test_livekit_dispatch_outbox.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app/core app/services app/routers app/webhooks tests/auth tests/activation tests/agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app/core app/services app/routers app/webhooks
```

- [ ] **Step 8: Commit explicit domain policy**

```bash
git add apps/api/app/core/dispatch_token.py apps/api/app/core/verification_token.py \
  apps/api/app/services apps/api/app/routers/activation.py \
  apps/api/app/routers/agent.py apps/api/app/webhooks/livekit.py \
  apps/api/app/workers/jobs/call_reconciliation.py \
  apps/api/app/workers/outbox/customer_dispatch.py \
  apps/api/tests/auth apps/api/tests/activation apps/api/tests/agent \
  apps/api/tests/services/test_onboarding_service.py \
  apps/api/tests/livekit/test_durable_dispatch_service.py \
  apps/api/tests/workers/test_call_reconciliation_wakeup.py \
  apps/api/tests/workers/test_livekit_dispatch_outbox.py
git commit -m "refactor(api): inject domain policy explicitly"
```

### Task 5: Remove API provider and observability fallbacks

**Files:**
- Modify: `apps/api/app/providers/carrier_lookup/factory.py`
- Modify: `apps/api/app/providers/carrier_lookup/telnyx.py`
- Modify: `apps/api/app/providers/telephony/factory.py`
- Modify: `apps/api/app/providers/telephony/telnyx.py`
- Modify: `apps/api/app/providers/subscriptions/stripe.py`
- Modify: `apps/api/app/providers/summaries/gemini.py`
- Modify: `apps/api/app/providers/storage/s3.py`
- Modify: `apps/api/app/providers/livekit_dispatch/livekit.py`
- Modify: `apps/api/app/providers/livekit_recording/livekit.py`
- Modify: `apps/api/app/services/billing_session_service.py`
- Modify: `apps/api/app/services/billing_service.py`
- Modify: `apps/api/app/services/livekit_recording_service.py`
- Modify: `apps/api/app/services/summary_service.py`
- Modify: `apps/api/app/routers/billing.py`
- Modify: `apps/api/app/webhooks/stripe.py`
- Modify: `apps/api/app/webhooks/livekit.py`
- Modify: `apps/api/app/workers/outbox/customer_dispatch.py`
- Modify: `apps/api/app/workers/outbox/verification_dispatch.py`
- Modify: `apps/api/app/workers/outbox/post_call.py`
- Modify: `apps/api/app/workers/outbox/phone.py`
- Modify: `apps/api/app/workers/outbox/phone_provisioning.py`
- Modify: `apps/api/app/workers/outbox/account_deactivation.py`
- Modify: `apps/api/app/workers/outbox/provider_cleanup.py`
- Modify: provider, billing, summary, storage, webhook, and safe-exception tests.

**Interfaces:**
- Every provider receives configuration and `Observability` explicitly.
- `S3Storage.aclose()` closes its owned urllib3 pool once.
- `GeminiSummaryProvider.aclose()` closes an internally created Google client once.
- LiveKit services receive a provider or explicit provider factory; they do not read settings.
- `SummaryService(provider: SummaryProvider)` is required.

- [ ] **Step 1: Write provider-construction and cleanup tests**

Add assertions for these locked constructors:

```python
build_carrier_lookup_provider(settings: Settings, *, observability: Observability)
create_telephony_provider(settings: Settings, *, observability: Observability)
TelnyxCarrierLookupProvider(*, api_key: str | None, observability: Observability, http_client=None)
StripeSubscriptionProvider(*, secret_key: str | None, stripe_client=None)
GeminiSummaryProvider(*, api_key: str | None, model: str, observability: Observability, client=None)
S3Storage(*, bucket_name: str, endpoint_url: str, access_key: str | None, secret_key: str | None, region: str, observability: Observability, client=None)
LiveKitDispatchAPIProvider(*, livekit_api, observability: Observability)
LiveKitRecordingProvider(*, egress_client, bucket_name: str, endpoint_url: str, access_key: str | None, secret_key: str | None, region: str, observability: Observability)
BillingSessionService(*, settings: Settings, observability: Observability, stripe_module=None)
BillingService(session: AsyncSession, *, settings: Settings)
LiveKitRecordingService(provider: RecordingProvider)
SummaryService(provider: SummaryProvider)
```

Test that missing real-provider credentials raise the existing safe configuration/provider failure and never instantiate a fake.

- [ ] **Step 2: Run provider RED tests**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/providers \
  tests/billing/test_stripe_webhooks.py \
  tests/services/test_livekit_recording_service.py \
  tests/services/test_summary_service.py \
  tests/services/test_safe_service_exceptions.py
```

- [ ] **Step 3: Remove constructor fallbacks and bind providers at boundaries**

Delete every `settings or get_settings()`, `observability or get_observability()`, and provider default in the listed modules. API request providers obtain `settings`/`observability` from `ApiRuntime`. Worker provider construction is completed in Task 8 using these explicit constructors.

Update every existing worker call site in this same task so the breaking
constructor change never leaves the repository red. Until Task 8 moves these
dependencies to the background composition root, each old handler resolves its
existing settings and telemetry once at its outer boundary, passes every
constructor argument explicitly, and closes operation-owned LiveKit, Gemini,
and S3 clients in `finally`. Do not add optional compatibility parameters to
the providers. Task 8 deletes this remaining handler-side construction when it
binds process-owned providers.

Keep fake selection solely in the existing explicit factories using `settings.telephony_mode`, `settings.carrier_lookup_mode`, and `settings.billing_mode`.

- [ ] **Step 4: Add deterministic provider cleanup**

For internally created clients:

```python
async def aclose(self) -> None:
    client = self.client
    self.client = None
    if client is None:
        return
    close = getattr(client, "aclose", None)
    if callable(close):
        await close()
        return
    close = getattr(client, "close", None)
    if callable(close):
        await asyncio.to_thread(close)
```

S3 owns the `PoolManager` it creates and calls `clear()` via `asyncio.to_thread`. Injected clients/pools remain borrowed and are not closed.

- [ ] **Step 5: Prove API provider modules contain no fallback lookups**

```bash
! rg -n "get_settings\(|get_observability\(|get_s3_storage|get_telephony_provider" \
  app/providers app/services app/routers app/webhooks
```

Expected: no matches. `get_request_observability` may remain defined in `core/observability.py`, but it must require the typed runtime and must not fall back to a global instance.

- [ ] **Step 6: Run the complete API phase gate**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  --cov=app --cov-branch --cov-report=term-missing
```

Expected: all API tests pass; line and branch coverage do not regress from the last verified baseline; no network or real provider is contacted.

- [ ] **Step 7: Commit the explicit API provider boundary**

```bash
git add apps/api/app apps/api/tests
git commit -m "refactor(api): remove provider construction fallbacks"
```

---

## Phase Two — Worker Composition

### Task 6: Add typed lifecycle and background worker runtimes

**Files:**
- Modify: `apps/api/app/composition/runtime.py`
- Create: `apps/api/app/composition/workers.py`
- Create: `apps/api/tests/composition/test_worker_composition.py`
- Modify: `apps/api/app/workers/arq_worker.py`
- Modify: `apps/api/app/workers/job_policy.py`
- Modify: `apps/api/app/core/observability.py`
- Modify: `apps/api/app/core/runtime_validation.py`
- Modify: `apps/api/tests/test_deployment_readiness.py`
- Modify: `apps/api/tests/workers/test_arq_worker.py`
- Modify: `apps/api/tests/workers/test_worker_observability_lifecycle.py`

**Interfaces:**
- Produces in `composition/runtime.py`: `WORKER_RUNTIME_KEY = "application_runtime"`.
- Produces in `composition/runtime.py`: distinct `CallLifecycleWorkerRuntime` and `BackgroundWorkerRuntime`.
- Produces in `composition/runtime.py`: `require_call_lifecycle_runtime(ctx)`, `require_background_runtime(ctx)`, and `require_worker_observability(ctx)`.
- Produces: worker builders that accept validated settings and the ARQ-owned Redis connection.
- Produces: `validate_call_lifecycle_worker_runtime(settings)` and `validate_background_worker_runtime(settings)`; deletes the cross-coupled broad worker validator.
- Changes: `instrument_job(job_name, *, queue_class, observability_getter)` requires an explicit getter.

- [ ] **Step 1: Write runtime type and lifecycle RED tests**

Test exact failures and ownership:

```python
def test_lifecycle_accessor_rejects_background_runtime() -> None:
    with pytest.raises(WorkerRuntimeConfigurationError, match="call-lifecycle"):
        require_call_lifecycle_runtime({WORKER_RUNTIME_KEY: background_runtime})


@pytest.mark.anyio
async def test_worker_runtime_does_not_close_arq_owned_redis() -> None:
    runtime = await build_call_lifecycle_worker_runtime(
        settings,
        arq_redis=borrowed_redis,
        engine_factory=engine_factory,
        observability_factory=observability_factory,
        observer_factory=observer_factory,
    )
    await runtime.aclose()
    assert borrowed_redis.close_calls == 0
    assert engine.dispose_calls == 1
    assert observer.close_calls == 1
    assert observability.shutdown_calls == 1
```

Also fail construction at every factory step and assert prior resources close exactly once.

Add validation-boundary cases proving:

- the lifecycle worker accepts settings containing only its database, Redis,
  reconciliation policy, and worker metadata, without Clerk, Stripe, Telnyx,
  LiveKit, S3, or Gemini credentials;
- the background worker requires database/Redis, a safe dispatch secret,
  LiveKit dispatch, storage, and the selected summary provider in every
  runnable non-test mode;
- selected Stripe and Telnyx modes require only their own credentials, while
  named fake modes do not; and
- production still rejects fake billing/telephony modes.

Each failure must identify setting names without values and occur before an
engine, Redis-adjacent adapter, observability provider, or external provider is
constructed.

- [ ] **Step 2: Run RED**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/composition/test_worker_composition.py \
  tests/workers/test_arq_worker.py \
  tests/test_deployment_readiness.py
```

- [ ] **Step 3: Implement distinct runtime types**

The runtime fields are explicit:

```python
@dataclass(slots=True)
class CallLifecycleWorkerRuntime:
    settings: Settings
    session_factory: AsyncSessionFactory
    arq_pool: ArqRedis
    observability: Observability
    queue_observer: QueueObserver
    now: Callable[[], datetime]
    _cleanup: RuntimeCleanup


@dataclass(slots=True)
class BackgroundWorkerRuntime:
    settings: Settings
    session_factory: AsyncSessionFactory
    arq_pool: ArqRedis
    observability: Observability
    queue_observer: QueueObserver
    outbox_handlers: Mapping[str, OutboxHandler]
    now: Callable[[], datetime]
    _cleanup: RuntimeCleanup
```

Both expose `aclose()`. Do not introduce a common public container passed to jobs; any private lifecycle helper remains inside composition.

Each builder calls its process-specific validator before constructing any
resource. `validate_call_lifecycle_worker_runtime` checks only lifecycle-owned
configuration. `validate_background_worker_runtime` owns all outbox-provider
checks removed from API validation in Task 2. Do not make either validator call
the other or restore one union validator.

- [ ] **Step 4: Cross ARQ context once**

`on_call_lifecycle_startup` and `on_background_startup` read only `ctx["redis"]`, build the correct runtime, and store only `ctx[WORKER_RUNTIME_KEY]`. `on_shutdown` pops that runtime, validates it is one of the two concrete types, and closes it. Remove application-owned `ctx` keys for sessions, handlers, observability, ARQ pool aliases, queue observer, providers, clocks, and metrics.

- [ ] **Step 5: Read worker settings once at the ARQ executable boundary**

Because ARQ reads class metadata before startup, use one module-level executable-boundary value:

```python
_WORKER_SETTINGS = get_settings()

class CallLifecycleWorkerSettings:
    redis_settings = RedisSettings.from_dsn(_WORKER_SETTINGS.redis_url)
    max_jobs = _WORKER_SETTINGS.worker_lifecycle_max_jobs
```

The remaining class attributes retain their current literal queue, polling,
shutdown, health, function, cron, timeout, retry, and result-retention values.

Both worker classes use the same captured settings object. No job or composition dependency reloads settings.
The module does not validate the union of both processes at import time.
`on_call_lifecycle_startup` and `on_background_startup` pass the captured
settings to their respective builder, where the relevant validator runs before
resources open. This prevents starting one worker from depending on the other
worker's credentials while still failing before ARQ accepts jobs.

- [ ] **Step 6: Make instrumentation use the typed runtime**

Change `instrument_job` and `apply_job_policy` signatures:

```python
def instrument_job(
    job_name: str,
    *,
    queue_class: str,
    observability_getter: Callable[[dict[str, Any]], Observability],
):

def apply_job_policy(
    function: JobFunction[ResultT],
    *,
    policy: JobPolicy,
    queue_class: str,
    observability_getter: Callable[[dict[str, Any]], Observability],
) -> JobFunction[ResultT]:
```

The existing decorator bodies remain intact except that telemetry comes from
the required getter instead of a dictionary/global fallback.

Pass `require_worker_observability` from `arq_worker.py`. Retry attempt and enqueue time remain ARQ metadata.

- [ ] **Step 7: Run GREEN and exact-registry checks**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/composition/test_worker_composition.py \
  tests/workers/test_arq_worker.py \
  tests/workers/test_worker_observability_lifecycle.py \
  tests/workers/test_job_policy.py \
  tests/integration/test_worker_queue_isolation.py \
  tests/test_deployment_readiness.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app/composition/workers.py app/workers app/core/observability.py tests/composition/test_worker_composition.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app/composition/workers.py app/workers app/core/observability.py
```

Expected: exact queue/job policies are unchanged and only the typed runtime is application-owned in `ctx`.

- [ ] **Step 8: Commit typed worker startup**

```bash
git add apps/api/app/composition/runtime.py apps/api/app/composition/workers.py \
  apps/api/app/workers \
  apps/api/app/core/observability.py apps/api/app/core/runtime_validation.py \
  apps/api/tests/composition/test_worker_composition.py \
  apps/api/tests/workers/test_arq_worker.py \
  apps/api/tests/workers/test_worker_observability_lifecycle.py \
  apps/api/tests/workers/test_job_policy.py \
  apps/api/tests/integration/test_worker_queue_isolation.py \
  apps/api/tests/test_deployment_readiness.py
git commit -m "refactor(api): add typed worker composition roots"
```

### Task 7: Migrate lifecycle jobs and delete the dead notification job

**Files:**
- Modify: `apps/api/app/workers/jobs/call_finalization.py`
- Modify: `apps/api/app/workers/jobs/call_reconciliation.py`
- Modify: `apps/api/app/workers/jobs/verification_expiry.py`
- Delete: `apps/api/app/workers/jobs/notifications.py`
- Modify: `apps/api/tests/workers/test_call_finalization_worker.py`
- Modify: `apps/api/tests/workers/test_call_reconciliation_wakeup.py`
- Modify: `apps/api/tests/workers/test_verification_expiry_job.py`
- Modify: `apps/api/tests/workers/test_individual_jobs.py`
- Modify: `apps/api/tests/workers/test_outbox_architecture.py`

**Interfaces:**
- ARQ wrappers validate one runtime and pass named dependencies.
- Produces pure job functions `finalize_call`, `reconcile_calls`, and `expire_verification_windows` with explicit keyword-only dependencies.
- Deletes the unregistered `notifications_job` and its tests after a production reference scan.

- [ ] **Step 1: Write direct use-case and wrapper RED tests**

Use these signatures:

```python
async def finalize_call(payload: dict, *, session_factory: AsyncSessionFactory) -> dict:

async def reconcile_calls(
    *,
    session_factory: AsyncSessionFactory,
    arq_pool: ArqRedis,
    observability: Observability,
    settings: Settings,
    now: Callable[[], datetime],
) -> dict[str, int]:

async def expire_verification_windows(
    *,
    session_factory: AsyncSessionFactory,
    now: Callable[[], datetime],
    batch_size: int = DEFAULT_EXPIRY_BATCH_SIZE,
) -> dict[str, int]:
```

These headers keep the existing job bodies; the implementation replaces only
framework lookups with the named arguments and extracts the ARQ wrapper.

Test wrappers with a correct runtime, missing runtime, and wrong runtime type. Test use cases directly without dictionaries.

- [ ] **Step 2: Run RED**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_call_finalization_worker.py \
  tests/workers/test_call_reconciliation_wakeup.py \
  tests/workers/test_verification_expiry_job.py
```

- [ ] **Step 3: Implement thin wrappers**

The wrappers contain no fallback:

```python
async def call_finalization_job(ctx: dict[str, Any], payload: dict) -> dict:
    runtime = require_call_lifecycle_runtime(ctx)
    return await finalize_call(payload, session_factory=runtime.session_factory)
```

Call reconciliation passes `runtime.settings`, `runtime.now`, `runtime.arq_pool`, and `runtime.observability`. Verification expiry requires `BackgroundWorkerRuntime` because its cron is registered there.

- [ ] **Step 4: Prove and remove the dead notification job**

Run from repository root:

```bash
rg -n "notifications_job|app\.workers\.jobs\.notifications" \
  apps/api/app apps/agent libs docs \
  -g '*.py' -g '*.md'
```

Expected before deletion: no production registration/call; only the job module, its three tests, and historical text references. Delete the module and the notification-job section of `test_individual_jobs.py`. Retain notification domain services/models because they have independent behavior.

- [ ] **Step 5: Run GREEN and static checks**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/workers
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app/workers tests/workers
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app/workers/jobs app/composition/workers.py
```

- [ ] **Step 6: Commit explicit lifecycle jobs**

```bash
git add apps/api/app/workers/jobs apps/api/tests/workers
git commit -m "refactor(api): inject lifecycle worker dependencies"
```

### Task 8: Bind every outbox topic to explicit dependencies

**Files:**
- Modify: every module under `apps/api/app/workers/outbox/` except empty `__init__.py` and bounded `failures.py` where no change is required.
- Modify: `apps/api/app/workers/outbox/delivery.py` — make delivery and reconciliation explicit in the same atomic cutover.
- Modify: `apps/api/app/composition/runtime.py` — point the background runtime annotation at the one-argument registry handler type.
- Modify: `apps/api/app/composition/workers.py`
- Modify: `apps/api/tests/composition/test_worker_composition.py`
- Modify: all outbox worker tests listed in the locked test section.

**Interfaces:**
- Changes in `outbox/registry.py`: `OutboxHandler = Callable[[OutboxEvent], Awaitable[None]]`.
- Produces: `build_outbox_handlers` with the exact dependency list shown in Step 1 and `Mapping[str, OutboxHandler]` return type.
- Every topic handler accepts `event` plus keyword-only dependencies; no handler accepts ARQ `ctx`.
- One shared `now: Callable[[], datetime]` replaces test-only per-handler clock keys.

**Owner decision 6A-C2A:** preserve dispatch behavior by adding
`max_call_duration_seconds` to the registry dependencies, adding
`activation_flow_enabled` and `max_call_duration_seconds` to customer dispatch,
and adding `DispatchTokenConfig` to verification dispatch. Composition binds
all three explicitly without `Settings`, dependency bags, or fallbacks.

- [ ] **Step 1: Write registry and handler-signature RED tests**

In `test_outbox_architecture.py`, assert exact topic keys and one-argument bound handlers:

```python
handlers = build_outbox_handlers(
    session_factory=session_factory,
    telephony_provider=telephony_provider,
    subscription_provider=subscription_provider,
    livekit_dispatch_provider=dispatch_provider,
    summary_provider=summary_provider,
    recording_provider=recording_provider,
    storage_provider=storage_provider,
    observability=observability,
    dispatch_token_config=token_config,
    livekit_agent_name="captured-agent",
    activation_flow_enabled=True,
    max_call_duration_seconds=321,
    now=fixed_now,
)

assert frozenset(handlers) == SUPPORTED_OUTBOX_TOPICS
for handler in handlers.values():
    required = [
        parameter.name
        for parameter in inspect.signature(handler).parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        not in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
    ]
    assert required == ["event"]
```

Add one direct explicit-dependency test per topic family. Pass values that conflict with environment settings and assert only explicit values are observed.

- [ ] **Step 2: Run RED across topic families**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_outbox_architecture.py \
  tests/workers/test_phone_provisioning_cleanup.py \
  tests/workers/test_phone_routing_readiness.py \
  tests/workers/test_livekit_dispatch_outbox.py \
  tests/workers/test_forwarding_verification_dispatch_outbox.py \
  tests/workers/test_post_call_outbox_handlers.py \
  tests/workers/test_account_deactivation.py \
  tests/workers/test_provider_cleanup.py
```

- [ ] **Step 3: Make phone handlers explicit**

Use these public signatures and propagate dependencies through private helpers:

```python
async def deliver_phone_provision(
    event: OutboxEvent,
    *,
    session_factory: AsyncSessionFactory,
    telephony_provider: TelephonyProvider,
    activation_flow_enabled: bool,
    now: Callable[[], datetime],
) -> None:

async def deliver_phone_routing(
    event: OutboxEvent,
    *,
    session_factory: AsyncSessionFactory,
    telephony_provider: TelephonyProvider,
    activation_flow_enabled: bool,
    now: Callable[[], datetime],
) -> None:
```

`phone_provisioning.py` receives `session_factory`, `telephony_provider`, and `now`; it never constructs a telephony provider.

- [ ] **Step 4: Make account lifecycle handlers explicit**

```python
async def deliver_account_deactivation(
    event: OutboxEvent,
    *,
    session_factory: AsyncSessionFactory,
    telephony_provider: TelephonyProvider,
    subscription_provider: SubscriptionProvider,
    observability: Observability,
    now: Callable[[], datetime],
) -> None:

async def deliver_provider_cleanup(
    event: OutboxEvent,
    *,
    session_factory: AsyncSessionFactory,
    telephony_provider: TelephonyProvider,
    subscription_provider: SubscriptionProvider,
    now: Callable[[], datetime],
) -> None:
```

Remove every provider/settings/observability fallback in both state machines without changing their locks, provider-operation records, compensation, retries, or commits.

- [ ] **Step 5: Make dispatch handlers explicit**

```python
async def deliver_livekit_dispatch(
    event: OutboxEvent,
    *,
    session_factory: AsyncSessionFactory,
    provider: LiveKitDispatchProvider,
    token_config: DispatchTokenConfig,
    livekit_agent_name: str,
    activation_flow_enabled: bool,
    max_call_duration_seconds: int,
    now: Callable[[], datetime],
) -> None:

async def deliver_livekit_verification_dispatch(
    event: OutboxEvent,
    *,
    session_factory: AsyncSessionFactory,
    provider: LiveKitDispatchProvider,
    token_config: DispatchTokenConfig,
    livekit_agent_name: str,
    now: Callable[[], datetime],
) -> None:
```

Preserve `_livekit_delivery.py` as the shared provider algorithm. Pass provider and timestamps explicitly.

- [ ] **Step 6: Make post-call handlers explicit**

```python
async def deliver_summary_generate(
    event: OutboxEvent,
    *,
    session_factory: AsyncSessionFactory,
    summary_provider: SummaryProvider,
) -> None:

async def deliver_recording_reconcile(
    event: OutboxEvent,
    *,
    session_factory: AsyncSessionFactory,
    recording_provider: RecordingProvider,
    storage_provider: StorageProvider,
    observability: Observability,
    now: Callable[[], datetime],
) -> None:
```

Each header retains its existing domain body and transaction boundaries while
the implementation threads the listed arguments through private helpers.

Construct `RecordingReconciler` from these dependencies inside the bound handler. Do not retain `recording_reconciler`, storage, provider, session, or clock dictionary lookups.

- [ ] **Step 7: Build the explicit registry with `functools.partial`**

`registry.py` exposes one function. Each public handler has `event` as its first positional parameter and dependencies as keyword-only parameters, so `partial` returns a one-argument callable:

```python
return {
    "account.deactivate": partial(
        deliver_account_deactivation,
        session_factory=session_factory,
        telephony_provider=telephony_provider,
        subscription_provider=subscription_provider,
        observability=observability,
        now=now,
    ),
    "provider.cleanup": partial(
        deliver_provider_cleanup,
        session_factory=session_factory,
        telephony_provider=telephony_provider,
        subscription_provider=subscription_provider,
        now=now,
    ),
    "phone.provision": partial(
        deliver_phone_provision,
        session_factory=session_factory,
        telephony_provider=telephony_provider,
        activation_flow_enabled=activation_flow_enabled,
        now=now,
    ),
    "phone.enable": partial(
        deliver_phone_routing,
        session_factory=session_factory,
        telephony_provider=telephony_provider,
        activation_flow_enabled=activation_flow_enabled,
        now=now,
    ),
    "phone.disable": partial(
        deliver_phone_routing,
        session_factory=session_factory,
        telephony_provider=telephony_provider,
        activation_flow_enabled=activation_flow_enabled,
        now=now,
    ),
    "livekit.dispatch": partial(
        deliver_livekit_dispatch,
        session_factory=session_factory,
        provider=livekit_dispatch_provider,
        token_config=dispatch_token_config,
        livekit_agent_name=livekit_agent_name,
        activation_flow_enabled=activation_flow_enabled,
        max_call_duration_seconds=max_call_duration_seconds,
        now=now,
    ),
    "livekit.verification_dispatch": partial(
        deliver_livekit_verification_dispatch,
        session_factory=session_factory,
        provider=livekit_dispatch_provider,
        token_config=dispatch_token_config,
        livekit_agent_name=livekit_agent_name,
        now=now,
    ),
    "summary.generate": partial(
        deliver_summary_generate,
        session_factory=session_factory,
        summary_provider=summary_provider,
    ),
    "recording.reconcile": partial(
        deliver_recording_reconcile,
        session_factory=session_factory,
        recording_provider=recording_provider,
        storage_provider=storage_provider,
        observability=observability,
        now=now,
    ),
}
```

Delete `DEFAULT_OUTBOX_HANDLERS`; executable routing now requires composition.
`composition/workers.py` imports `build_outbox_handlers` locally inside the
background builder after runtime types exist. Delivery and job modules import
runtime accessors from `composition/runtime.py`, never from the builder module;
this prevents a worker-composition/import cycle.

`composition/runtime.py` references `OutboxHandler` only under
`TYPE_CHECKING`, with postponed annotations; it must not runtime-import
`outbox/registry.py`. The registry may retain its handler imports because the
fully migrated topic handlers no longer import composition. Add an import-smoke
test for `app.composition.runtime`, `app.composition.workers`,
`app.workers.arq_worker`, `app.workers.outbox.registry`, and
`app.workers.outbox.delivery` so the cycle cannot be reintroduced.

- [ ] **Step 8: Construct and own background providers before publishing the runtime**

`build_background_worker_runtime` explicitly constructs:

- telephony provider from selected mode;
- subscription provider from selected billing mode;
- LiveKit API/provider when required by validated mode;
- Gemini summary provider for the selected summary mode;
- recording provider and S3 storage;
- dispatch token config;
- activation policy and maximum call duration; and
- the bound outbox registry.

Register closeable LiveKit, Gemini, and S3 resources immediately. Development
fake modes remain explicit. Production validation fails before construction if
required credentials are missing.

- [ ] **Step 9: Write direct delivery-engine RED tests**

Use locked signatures:

```python
async def deliver_outbox_batch(
    *,
    session_factory: AsyncSessionFactory,
    handlers: Mapping[str, OutboxHandler],
    observability: Observability,
    now: Callable[[], datetime],
) -> dict[str, int]:

async def reconcile_outbox(
    *,
    session_factory: AsyncSessionFactory,
    handlers: Mapping[str, OutboxHandler],
    observability: Observability,
    now: Callable[[], datetime],
) -> dict[str, int]:
```

Both headers retain the current claim/failure/reconciliation behavior while
replacing dependency discovery and handler invocation. Test unsupported topic,
retryable/terminal handler failure, metric emission, stale-lease
reconciliation, snapshot failure, cancellation, and successful delivery
without dictionary dependency injection.

- [ ] **Step 10: Run delivery RED tests**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/integration/test_outbox_delivery.py \
  tests/workers/test_post_call_jobs.py \
  tests/workers/test_outbox_architecture.py
```

- [ ] **Step 11: Split ARQ wrappers from explicit delivery behavior**

```python
async def outbox_delivery_job(ctx: dict[str, Any], _payload: dict | None = None) -> dict[str, int]:
    runtime = require_background_runtime(ctx)
    return await deliver_outbox_batch(
        session_factory=runtime.session_factory,
        handlers=runtime.outbox_handlers,
        observability=runtime.observability,
        now=runtime.now,
    )
```

`outbox_reconciliation_job` delegates to `reconcile_outbox` with the same
named runtime fields. The delivery engine calls `await handler(event)`. Delete
the default registry lookup and every handler/session/clock/metric/observability
fallback.

- [ ] **Step 12: Prove wrapper type failures and one-clock behavior**

Add wrapper tests that pass a call-lifecycle runtime to each outbox job and
assert `WorkerRuntimeConfigurationError` before any session opens. Pass one
fixed `runtime.now` value and assert claim, delivery/failure, recording
snapshot, and account-deactivation snapshot timestamps all derive from that
clock where the current behavior accepts an injected time.

- [ ] **Step 13: Run the complete worker phase gate and fallback scan**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers tests/integration/test_outbox_delivery.py \
  tests/integration/test_worker_queue_isolation.py
! rg -n "ctx\.get\(\"(session_factory|observability|outbox_handlers|telephony_provider|subscription_provider|livekit_dispatch_provider|summary_provider|storage_provider|recording_reconciler)|get_session_factory\(|get_settings\(" \
  app/workers/jobs app/workers/outbox
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests/workers
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app/workers app/composition
```

Expected: no topic or delivery module contains application dependency
fallbacks; only ARQ metadata lookups remain. All worker/outbox tests pass and
process/queue isolation is unchanged.

- [ ] **Step 14: Commit the atomic outbox composition cutover**

```bash
git add apps/api/app/workers/outbox apps/api/app/composition/runtime.py \
  apps/api/app/composition/workers.py \
  apps/api/tests/composition/test_worker_composition.py \
  apps/api/tests/workers apps/api/tests/integration/test_outbox_delivery.py \
  apps/api/tests/integration/test_worker_queue_isolation.py
git commit -m "refactor(api): complete worker composition cutover"
```

---

## Phase Three — Agent Composition

### Task 9: Make pipeline and prewarm configuration explicit

**Files:**
- Create: `apps/agent/agent/composition.py`
- Create: `apps/agent/tests/test_composition.py`
- Modify: `apps/agent/agent/main.py`
- Modify: `apps/agent/agent/pipeline_factory.py`
- Modify: `apps/agent/agent/verification_runtime.py`
- Modify: `apps/agent/tests/test_dispatch_compatibility.py`
- Modify: `apps/agent/tests/test_pipeline_factory.py`
- Modify: `apps/agent/tests/test_main.py`
- Modify: `apps/agent/tests/test_runtime_validation.py`
- Modify: `apps/agent/tests/test_verification_runtime.py`

**Interfaces:**
- Produces an intermediate `AgentProcessRuntime(settings, silero_vad)` typed process-data boundary; Task 10 adds the final owned transports and close-once lifecycle.
- Produces: `require_agent_process_runtime(proc) -> AgentProcessRuntime`.
- Changes: every pipeline builder requires `settings: AgentSettings`.
- Changes: `build_worker_options(settings: AgentSettings | None = None) -> WorkerOptions` reads defaults once.
- Changes: `run_forwarding_verification(..., settings: AgentSettings, ...)` passes
  that exact settings object to its verification-session factory.

**Owner clarification 6A-C3A:** Task 9 includes the minimal settings-only
verification consumer plumbing required by the locked
`build_verification_session(..., settings=...)` interface. The entrypoint reads
the typed `AgentProcessRuntime`, passes `runtime.settings` into
`run_forwarding_verification`, and that function passes the same object into
its session factory. API-client, event-publisher, Redis transport, close-order,
and shutdown-lifecycle changes remain deferred to Task 10.

- [ ] **Step 1: Write explicit-settings pipeline RED tests**

Replace monkeypatches of `pipeline_factory.get_settings` with direct parameters. Lock these signatures:

```python
def build_verification_session(
    tts_provider: str,
    *,
    settings: AgentSettings,
    plugin_modules: dict[str, Any] | None = None,
    session_cls=AgentSession,
):

def build_agent_runtime(
    dispatch_metadata: dict,
    *,
    settings: AgentSettings,
    plugin_modules: dict[str, Any] | None = None,
    vad=None,
    turn_detection=None,
    inference_executor=None,
    agent_cls=Agent,
    session_cls=AgentSession,
):
```

The existing builder bodies remain; every private helper receives the same
captured settings object instead of loading configuration itself.

Private plugin builders also receive `settings` explicitly. Test STT, LLM, TTS, STS, VAD, turn detector, endpointing delays, and selected-plugin-only behavior.

In `test_composition.py`, first assert that typed process data is missing or
wrongly typed, then assert that prewarm publishes one complete
`AgentProcessRuntime` containing the exact supplied settings and optional VAD.
In `test_main.py`, assert that `build_worker_options(settings)` passes the same
object to validation, inference registration, and its named prewarm closure;
no environment settings lookup is allowed when the argument is supplied.

- [ ] **Step 2: Run pipeline RED**

Run from `apps/agent`:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/test_composition.py tests/test_pipeline_factory.py \
  tests/test_main.py tests/test_runtime_validation.py
```

- [ ] **Step 3: Implement `AgentProcessRuntime` and typed process data**

```python
@dataclass(slots=True)
class AgentProcessRuntime:
    settings: AgentSettings
    silero_vad: object | None = None


def require_agent_process_runtime(proc: object) -> AgentProcessRuntime:
    runtime = getattr(proc, "userdata", None)
    if not isinstance(runtime, AgentProcessRuntime):
        raise AgentRuntimeConfigurationError("agent process runtime is not initialized")
    return runtime
```

Do not store an arbitrary dictionary in `proc.userdata`.

- [ ] **Step 4: Load settings once and pass them through prewarm**

`build_worker_options` performs:

1. `configured = settings or get_settings()`;
2. `validate_agent_runtime(configured)`;
3. `_register_inference_runners(configured)`;
4. construct a named prewarm closure that calls `prewarm_assets(proc, settings=configured)`;
5. return `WorkerOptions` using the configured LiveKit values.

`prewarm_assets` builds `AgentProcessRuntime`, including one explicitly
configured settings object and optional VAD, and assigns the completed runtime
to `proc.userdata` only after construction. Optional plugin failures retain
safe logging and an explicit `None` asset. Do not introduce client ownership in
this task: Task 10 first makes both transport constructors explicitly lazy and
then adds them to this runtime without creating a transient ownership seam.

- [ ] **Step 5: Pass settings through every pipeline helper**

Remove `get_settings` import from `pipeline_factory.py`. The following helpers receive `settings` as an explicit first/keyword argument:

```text
_resolve_speechmatics_turn_detection_mode
_resolve_gemini_llm
_resolve_gemini_api_key
_default_plugin_modules
_build_stt
_build_llm
_build_tts
_build_vad
_build_turn_detection
_build_sts_model
_build_sts_session
build_verification_session
build_agent_runtime
```

- [ ] **Step 6: Run GREEN and static checks**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/test_composition.py tests/test_pipeline_factory.py tests/test_main.py \
  tests/test_runtime_validation.py tests/test_verification_runtime.py
! rg -n "get_settings\(" agent/pipeline_factory.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent/composition.py agent/main.py agent/pipeline_factory.py tests/test_composition.py tests/test_pipeline_factory.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent/composition.py agent/main.py agent/pipeline_factory.py
```

- [ ] **Step 7: Commit explicit agent configuration**

```bash
git add apps/agent/agent/composition.py apps/agent/agent/main.py \
  apps/agent/agent/pipeline_factory.py apps/agent/tests/test_composition.py \
  apps/agent/tests/test_pipeline_factory.py apps/agent/tests/test_main.py \
  apps/agent/tests/test_runtime_validation.py apps/agent/tests/test_verification_runtime.py
git commit -m "refactor(agent): compose pipeline from explicit settings"
```

### Task 10: Give agent transports deterministic process ownership

**Files:**
- Modify: `apps/agent/agent/composition.py`
- Modify: `apps/agent/agent/api_client.py`
- Modify: `apps/agent/agent/event_publisher.py`
- Modify: `apps/agent/agent/session_runtime.py`
- Modify: `apps/agent/agent/verification_runtime.py`
- Modify: `apps/agent/agent/main.py`
- Modify: `apps/agent/tests/test_api_client.py`
- Modify: `apps/agent/tests/test_realtime_compatibility.py`
- Modify: `apps/agent/tests/test_session_runtime.py`
- Modify: `apps/agent/tests/test_session_runtime_errors.py`
- Modify: `apps/agent/tests/test_verification_runtime.py`
- Modify: `apps/agent/tests/test_main.py`
- Modify: `apps/agent/tests/test_composition.py`

**Interfaces:**
- `AgentApiClient` requires base URL, timeout, and retry count explicitly.
- `RedisEventBus` requires a Redis client and closes it only when explicitly owned.
- `EventPublisher` requires an event bus and exposes idempotent `aclose()`.
- Produces: `build_agent_process_runtime(settings, *, api_client_factory, event_publisher_factory, silero_vad=None) -> AgentProcessRuntime`.
- `AgentProcessRuntime` owns and closes its API client and publisher after job finalization.
- `SessionRuntime` and verification runtime borrow transports and never close them.

- [ ] **Step 1: Write transport ownership RED tests**

Add these cases:

```python
@pytest.mark.anyio
async def test_process_runtime_closes_in_reverse_construction_order() -> None:
    runtime = build_agent_process_runtime(
        settings,
        api_client_factory=lambda _settings: api_client,
        event_publisher_factory=lambda _settings: publisher,
    )
    await asyncio.gather(runtime.aclose(), runtime.aclose())
    assert calls[-2:] == ["publisher.close", "api_client.close"]


@pytest.mark.anyio
async def test_process_runtime_closes_transports_once_after_session_finalize() -> None:
    session_runtime = SessionRuntime(failing_publisher, api_client=api_client)
    runtime = build_agent_process_runtime(
        settings,
        api_client_factory=lambda _settings: api_client,
        event_publisher_factory=lambda _settings: failing_publisher,
    )
    await session_runtime.finalize(metadata, duration_seconds=5)
    await runtime.aclose()
    await runtime.aclose()
    assert api_client.close_calls == 1
    assert failing_publisher.close_calls == 1


@pytest.mark.anyio
async def test_event_publisher_closes_owned_redis_once() -> None:
    publisher = build_event_publisher(settings, redis_factory=lambda *_: redis)
    await asyncio.gather(publisher.aclose(), publisher.aclose())
    assert redis.close_calls == 1


def test_prewarm_does_not_publish_a_partial_runtime_when_factory_fails() -> None:
    original_userdata = object()
    proc.userdata = original_userdata
    api_client = build_agent_api_client(settings)

    def fail_publisher(_settings: AgentSettings) -> EventPublisher:
        raise RuntimeError("publisher construction failed")

    with pytest.raises(RuntimeError, match="publisher construction failed"):
        prewarm_assets(
            proc,
            settings=settings,
            api_client_factory=lambda _settings: api_client,
            event_publisher_factory=fail_publisher,
        )
    assert proc.userdata is original_userdata
    assert api_client.http_client is None
```

Also test that both production factories perform no network I/O during prewarm,
plus verification success, provider failure, session-start failure,
cancellation, and cleanup failure without leaking the API client. The partial
construction test intentionally expects no close call: the API wrapper has not
acquired an HTTP client, and the failing publisher factory returned no owned
object.

- [ ] **Step 2: Run RED**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/test_composition.py \
  tests/test_main.py \
  tests/test_api_client.py \
  tests/test_realtime_compatibility.py \
  tests/test_session_runtime.py \
  tests/test_session_runtime_errors.py \
  tests/test_verification_runtime.py
```

- [ ] **Step 3: Remove settings from agent clients**

Lock the constructor and composition factories:

```python
class AgentApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        max_retries: int,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:

        install_safe_http_client_logging()
        self.base_url = base_url.rstrip("/")
        self.http_client = http_client
        self._owns_http_client = http_client is None
        self.timeout = timeout
        self.max_retries = max_retries

def build_agent_api_client(settings: AgentSettings) -> AgentApiClient:
    return AgentApiClient(
        base_url=settings.api_base_url,
        timeout=settings.api_timeout_seconds,
        max_retries=settings.api_max_retries,
    )
```

Delete `get_settings` from `api_client.py`.

- [ ] **Step 4: Make publisher ownership explicit**

```python
class RedisEventBus:
    def __init__(self, redis_client: Redis, *, owns_client: bool) -> None:
        self.redis_client = redis_client
        self._owns_client = owns_client

    async def aclose(self) -> None:
        if not self._owns_client:
            return
        self._owns_client = False
        await self.redis_client.aclose()

class EventPublisher:
    def __init__(self, event_bus: RedisEventBus) -> None:
        self.event_bus = event_bus

    async def aclose(self) -> None:
        await self.event_bus.aclose()

def build_event_publisher(
    settings: AgentSettings,
    *,
    redis_factory=Redis.from_url,
) -> EventPublisher:
    redis = redis_factory(settings.redis_url, decode_responses=True)
    return EventPublisher(RedisEventBus(redis, owns_client=True))
```

Injected event buses/Redis clients remain borrowed when `owns_client=False`.

- [ ] **Step 5: Transfer transport ownership from session/verification code to the process runtime**

Extend the Task 9 runtime only after both constructors obey the synchronous,
no-I/O prewarm contract:

```python
@dataclass(slots=True)
class AgentProcessRuntime:
    settings: AgentSettings
    api_client: AgentApiClient
    event_publisher: EventPublisher
    _cleanup: AgentRuntimeCleanup
    silero_vad: object | None = None

    async def aclose(self) -> None:
        await self._cleanup.aclose()


def build_agent_process_runtime(
    settings: AgentSettings,
    *,
    api_client_factory=build_agent_api_client,
    event_publisher_factory=build_event_publisher,
    silero_vad: object | None = None,
) -> AgentProcessRuntime:
```

The builder creates an `AsyncExitStack`, constructs the lazy API wrapper and
immediately registers its close callback, then constructs the publisher and
immediately registers its close callback. It returns only the complete runtime.
Its factory protocol explicitly forbids I/O and async acquisition.
`AgentRuntimeCleanup` wraps that stack and retains one cleanup task; concurrent
`aclose()` calls join it through `asyncio.shield`. Reverse-order cleanup closes
the publisher before the API wrapper. Either failure is safely reported while
cleanup continues to the other resource, and caller cancellation propagates
while the retained cleanup task finishes for a later join.

Keep `SessionRuntime.finalize()` retryable when durable completion is not
acknowledged, but remove API-client closure from it. `SessionRuntime` borrows
the process runtime's publisher/API client and has no transport-close method.
Pass the process API client into forwarding verification and set
`close_api_client=False`; verification continues closing only its LiveKit
session. `AgentProcessRuntime.aclose()` uses one retained close task, closes the
publisher first and API client second, safely reports cleanup failures, and
preserves cancellation.

- [ ] **Step 6: Inject call resources in `entrypoint`**

Retrieve `AgentProcessRuntime`, pass `runtime.settings` to
`build_agent_runtime`, and lend `runtime.event_publisher` and
`runtime.api_client` to `SessionRuntime`. Register one shutdown callback before
dispatch parsing. That callback reads an optional `SessionRuntime` captured by
the entrypoint, awaits `finalize()`, awaits `AgentProcessRuntime.aclose()` in
`finally`, and only then shuts down observability. This single callback avoids
the concurrent-callback race in LiveKit Agents 1.4.4 and still closes process
resources when parsing or setup fails before a session runtime exists.

`prewarm_assets` constructs the final process runtime before optional asset
loading, attaches the loaded VAD before publishing `proc.userdata`, and calls
the safe observability initializer only after the runtime is complete. Remove
the duplicate observability initialization from `entrypoint`. Do not register
separate callbacks for session finalization, transport closure, or
observability shutdown.

Forwarding verification passes `runtime.settings` into its session factory,
borrows `runtime.api_client`, and leaves transport closure to the single job
shutdown callback after verification session cleanup.

- [ ] **Step 7: Run the complete agent phase gate**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  --cov=agent --cov-branch --cov-report=term-missing
! rg -n "get_settings\(" agent/api_client.py agent/event_publisher.py agent/pipeline_factory.py
```

Expected: the full agent suite passes, transport closure is proven on every termination path, and settings lookup remains only in `main.py`/`config.py`.

- [ ] **Step 8: Commit deterministic agent ownership**

```bash
git add apps/agent/agent apps/agent/tests
git commit -m "refactor(agent): own process transports explicitly"
```

---

## Phase Four — Enforcement and Final Verification

### Task 11: Delete obsolete globals, add architecture guards, and complete 6A

**Files:**
- Create: `apps/api/tests/test_composition_architecture.py`
- Create: `apps/agent/tests/test_composition_architecture.py`
- Modify: `apps/api/app/core/database.py`
- Modify: `apps/api/app/core/redis.py`
- Modify: `apps/api/app/providers/storage/s3.py`
- Modify: `apps/api/app/providers/telephony/telnyx.py`
- Modify: `apps/api/tests/conftest.py`
- Modify: tests still clearing settings/resource caches or patching removed globals.
- Modify: `docs/engineering/2026-07-30-agent-api-review-decisions.md`

**Interfaces:**
- Deletes all superseded cached resource factories and silent fallbacks.
- Adds source-level dependency guards for API, workers, and agent.
- Marks Issue 6A implemented only after every verification command passes.

- [ ] **Step 1: Write architecture guards before deleting the remaining seams**

Use `ast` to inspect production Python files. The API guard must enforce:

```python
ALLOWED_SETTINGS_CALLERS = {
    Path("app/main.py"),
    Path("app/workers/arq_worker.py"),
    Path("app/core/config.py"),
}

FORBIDDEN_GLOBAL_IMPORTS = {
    ("app.core.database", "get_engine"),
    ("app.core.database", "get_session_factory"),
    ("app.core.redis", "get_redis_client"),
    ("app.providers.storage.s3", "get_s3_storage"),
    ("app.providers.telephony.telnyx", "get_telephony_provider"),
}
```

The test walks every `app/**/*.py`, records `ImportFrom` and calls to imported `get_settings`, and reports exact violating paths. It also asserts:

- `app/workers/**/*.py` never imports `app.main` or `app.routers.*`;
- `app/services`, `app/repositories`, `app/providers`, and `app/models` never import `app.composition.*`;
- `app/workers/jobs` and `app/workers/outbox` contain no `ctx.get` calls for application dependency keys.

The agent guard allows `get_settings()` only in `agent/main.py` and its definition in `agent/config.py`, and prevents `agent/api_client.py`, `event_publisher.py`, `pipeline_factory.py`, `session_runtime.py`, and `verification_runtime.py` from importing composition or settings globals.

#### 6A-C4A guard-design amendment

Owner-approved decision 6A-C4A supersedes the call-dataflow and ctx-provenance
wording above. Architecture guards remain intentionally syntactic and bounded:

- Outside the exact executable settings modules, reject direct, aliased, and
  star imports of `get_settings`; imports of the relevant config module; and
  module-scope bindings named `get_settings`. Specific typed imports such as
  `Settings` and `AgentSettings` remain valid. Nested lambda, comprehension,
  class, and function-local shadows without a forbidden import remain valid.
- In `app/workers/jobs` and `app/workers/outbox`, a function with an actual
  `ctx` parameter may use it only as the sole direct argument to the runtime
  accessor assigned to that worker family. Aliasing, rebinding, passing `ctx`
  elsewhere, and direct metadata/application-key reads are rejected. Current
  jobs/outbox code needs no ARQ metadata whitelist.
- All six deleted factory names are reserved at API module scope. A small
  recursive binding collector follows module-level compound statements and
  records assignment/import/target/pattern/function/class bindings while
  stopping at function, class, lambda, and comprehension scopes.
- Do not implement Python call binding, evaluation scopes, possible provenance,
  or control-flow interpretation in these guards. Import escapes and prohibited
  `ctx` syntax fail directly, independent of runtime dataflow.

#### 6A-C4A Round 4 syntax-escape clarification

The final review keeps 6A-C4A syntax-directed while closing two remaining
escape families:

- Settings checks resolve only names established by direct `Import` and
  `ImportFrom` statements, then follow syntactic attribute chains. Access to
  the exact `app.core.config.get_settings` or `agent.config.get_settings`
  attribute is forbidden outside the executable roots. Literal
  `importlib.import_module` calls (including direct import aliases) and bare
  `__import__` calls may not name the config module or a literal package
  ancestor from which it can be reached. Existing literal and formatted
  LiveKit plugin imports remain valid. The guard deliberately does not resolve
  assignment aliases, computed strings, arbitrary dataflow, `getattr`, or
  other reflective access.
- Worker `ctx` checking follows lexical syntax rather than independently
  rescanning every nested function. Decorators, defaults, keyword defaults,
  parameter annotations, return annotations, class decorators, bases, and
  class keywords are checked in the enclosing scope where Python evaluates
  them. A nested callable body with a lexical local `ctx` binding is local;
  otherwise it captures the worker `ctx`. Class bodies are checked
  sequentially, while methods, lambdas, comprehensions, and nested class bodies
  do not inherit the class namespace and therefore continue to capture an
  enclosing worker `ctx` unless they bind their own.
- This clarification adds only bounded import-binding resolution and lexical
  scope traversal. It still forbids call binding, assignment/string
  provenance, control-flow interpretation, and reflection.

#### 6A-C4A Round 5 final syntax contract

The final guard removes the remaining import-binding pipeline instead of
expanding it. In every non-executable settings module:

- Module-form imports of the process package are forbidden: `import app` and
  `import app.*` in API production, and `import agent` and `import agent.*` in
  agent production. Dependencies must use explicit `ImportFrom` syntax. This
  import boundary is intentionally stronger than lexical name resolution, so
  a later or enclosing shadow of the imported name does not make the module
  import valid.
- `ImportFrom` rejects config-bearing package escapes and stars from relevant
  ancestors: `from app import core`, `from app.core import config`, and the
  corresponding relative/star forms; the agent guard applies the analogous
  `agent.config` rule. Exact `get_settings` and star imports from the config
  module remain forbidden, while `Settings`, `AgentSettings`, and unrelated
  explicit dependencies remain valid. `get_settings` also remains reserved at
  module scope.
- Literal dynamic calls are matched from syntax alone: a bare or attribute
  `import_module(...)` and a bare `__import__(...)`, independent of how the
  callable name was imported. Positional and keyword literal `name` arguments
  are checked. Relative `import_module` names with a positional or keyword
  literal `package` are normalized with `importlib.util.resolve_name`; resolved
  config modules and package ancestors fail safely. Nonliteral/invalid
  relative calls and unrelated or LiveKit literal/formatted-string calls stay
  outside this deliberately bounded rule.

Worker `ctx` checking uses the containing callable's lexical binders. Ordinary
parameters, assignments, imports, definitions, targets, patterns, and named
expressions bind locally. Named expressions in comprehension iterables,
filters, elements, keys, and values count as containing-callable bindings;
comprehension iteration targets do not. `global ctx` means a nested callable
does not capture the worker parameter and is allowed by this boundary, whereas
`nonlocal ctx` preserves the enclosing capture and remains subject to the
direct-access rule. Sequential class namespaces also account for named
expressions; sync/async loop and with targets; exception targets; match
star/as/mapping captures; destructuring; definitions; and imports before the
relevant body. Methods, lambdas, comprehensions, and nested classes do not
inherit that class namespace.

The dynamic-literal helpers contain 38 identical lines in each architecture
test root; the app-specific settings walkers contain 45 API lines and 40 agent
lines. This is the only intentional cross-root guard duplication left. The API
and agent are separately packaged applications with independent test
environments and package roots, so extracting those test helpers would create
a shared test/production support dependency between processes for a small,
stable syntax rule. No such shared dependency is added.

#### 6A-C5A exceptional Round 6 class-suite clarification

Owner-approved decision 6A-C5A permits one final bounded correction to the API
architecture test only. Sequential class-local visibility applies within every
child statement list of class-level `if`, `while`, `try`, and `try*` syntax:

- Each `if`/`else`, `while`/`else`, `try` body, exception handler, `else`, and
  `finally` suite starts with the incoming class visibility. Statements within
  that suite are processed sequentially, so an import, definition, assignment,
  or destructuring binder named `ctx` hides the enclosing worker `ctx` for
  later statements in the same suite.
- Suite visibility is branch-local. No binder is joined or propagated to a
  sibling suite or to statements after the compound statement. Conditions,
  exception types, and any genuine direct use of the enclosing worker `ctx`
  remain subject to the existing direct-access rule.
- The implementation reuses `_visit_nodes` through one saved/reset
  `_visit_class_branches` helper. `If`/`While` and `Try`/`TryStar` share their
  respective visitors. Other construct-specific class-scope switches remain
  explicit because consolidating different evaluation orders would create the
  over-generalized scope abstraction that 6A-C4A rejected.

This clarification adds no joins, provenance, dataflow interpretation, shared
test support, or production behavior. Round 6 is the final owner-authorized
Task 11 architecture-guard fix round. The later complete-range review findings
43A–47A are separately authorized corrections covered by Tasks 12–15.

- [ ] **Step 2: Run architecture RED**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/test_composition_architecture.py
cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/test_composition_architecture.py
```

Expected: failures name every remaining legacy import/call.

- [ ] **Step 3: Delete legacy factories and test cache clearing**

Delete:

```text
core.database.get_engine
core.database.get_session_factory
core.redis.get_redis_client
providers.storage.s3.get_s3_storage
providers.telephony.telnyx.get_telephony_provider
core.observability.get_observability
```

Remove their `lru_cache` imports and every `cache_clear()` call. Keep `get_settings()` only as an executable-boundary convenience; ordinary tests instantiate `Settings`/`AgentSettings` explicitly.

Replace remaining deep monkeypatches with constructor arguments, runtime builders, FastAPI dependency overrides, or direct use-case calls. Do not keep aliases for deleted names.

- [ ] **Step 4: Run zero-reference and placeholder scans**

From repository root:

```bash
! rg -n "get_engine\(|get_session_factory\(|get_redis_client\(|get_s3_storage\(|get_telephony_provider\(" \
  apps/api/app apps/agent/agent
! rg -n "get_settings\(" apps/api/app \
  -g '*.py' | rg -v "app/core/config.py|app/main.py|app/workers/arq_worker.py"
! rg -n "get_settings\(" apps/agent/agent \
  -g '*.py' | rg -v "agent/config.py|agent/main.py"
! rg -n "ctx\.get\(\"(session_factory|observability|outbox_handlers|telephony_provider|subscription_provider|livekit_dispatch_provider|summary_provider|storage_provider|recording_reconciler)" \
  apps/api/app/workers
```

Expected: all commands exit zero.

- [ ] **Step 5: Run full API verification**

From `apps/api`:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  --cov=app --cov-branch --cov-report=term-missing
```

Expected: all API tests pass; line coverage is at least 91.74% and branch coverage is at least 80.25%, unless the repository's immediately pre-implementation verified baseline is higher, in which case the higher baseline applies.

- [ ] **Step 6: Run full agent verification**

From `apps/agent`:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  --cov=agent --cov-branch --cov-report=term-missing
```

Expected: all agent tests pass and neither line nor branch coverage regresses from the immediately pre-implementation baseline.

- [ ] **Step 7: Run cross-runtime integration and import checks**

From `apps/api`:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/integration/test_agent_runtime_transcript_durability.py \
  tests/integration/test_local_activation_to_number.py \
  tests/integration/test_outbox_delivery.py \
  tests/integration/test_worker_queue_isolation.py \
  tests/livekit/test_dispatch_webhook.py \
  tests/workers/test_outbox_architecture.py \
  tests/test_composition_architecture.py
```

Expected: explicit composition preserves API→outbox→worker and agent→API durable paths without live providers.

- [ ] **Step 8: Inspect the final diff for completion criteria**

Run:

```bash
git diff --check
git diff --stat
git status --short
```

Review every changed constructor and shutdown path. Confirm:

1. all four runtimes have one root;
2. no compatibility fallbacks remain;
3. no runtime object is passed into business logic;
4. no resource has two owners;
5. partial startup cleanup is tested;
6. settings/provider mode failures are safe and explicit;
7. worker source remains under `apps/api` and worker services remain separate;
8. realtime, deployment, schema, queue policy, and provider behavior are unchanged;
9. protected paths and real environment files are untouched.

- [ ] **Step 9: Update the engineering decision ledger**

In `docs/engineering/2026-07-30-agent-api-review-decisions.md`:

- change Issue 6 status from `Accepted` to `Implemented`;
- link the approved design and this implementation plan;
- record the final test counts, Ruff/mypy status, line/branch coverage, and commit range;
- state that worker process isolation remains and source extraction is deferred until deployment/security evidence justifies it;
- leave realtime Issues 1A/14A and performance-governance Issue 16A unchanged.

- [ ] **Step 10: Commit the completed 6A implementation**

```bash
git add apps/api apps/agent docs/engineering/2026-07-30-agent-api-review-decisions.md
git commit -m "refactor: complete explicit runtime composition"
```

The commit is allowed only after Steps 4–8 pass and the staged diff contains no protected or unrelated files.

---

## Final-review correction addendum

The complete-range Standards and Spec review found five gaps after Task 11.
The owner approved 43A, 44A, 45A, 46A, and 47A. Section 13 of the design is
the controlling specification for Tasks 12–15 below. These tasks do not reopen
realtime, deployment, database, queue-policy, provider-selection, or source-app
extraction decisions.

Tasks 12–14 each use a fresh implementer, test-first RED/GREEN evidence, and an
independent Spec then Standards review before the next task starts. A reviewer
finding may receive at most five bounded fix rounds; a scope or owner-decision
conflict returns to the owner before code changes. Task 15 uses fresh read-only
reviewers for the complete corrected range.

### Task 12: Make API LiveKit configuration explicit and fail safe

**Files:**
- Modify: `apps/api/app/core/runtime_validation.py`
- Modify: `apps/api/app/webhooks/livekit.py`
- Modify: `apps/api/tests/test_deployment_readiness.py`
- Modify: `apps/api/tests/composition/test_api_composition.py`
- Modify: `apps/api/tests/livekit/test_dispatch_webhook.py`

**Interfaces:**
- Produces: one validation rule for the `livekit_url`, `livekit_api_key`, and
  `livekit_api_secret` configuration group.
- Produces: `get_webhook_receiver(request: Request) -> object`, returning a
  receiver or raising HTTP 503; it never returns `None` to a route handler.
- Preserves: stable LiveKit routes, `ApiRuntime` optional fields for explicitly
  disabled development/test runtimes, and existing complete production wiring.

- [ ] **Step 1: Write configuration-state RED tests**

In `test_deployment_readiness.py`, add explicit state-table coverage:

```python
LIVEKIT_DISABLED = {
    "livekit_url": None,
    "livekit_api_key": None,
    "livekit_api_secret": None,
}


@pytest.mark.parametrize("app_env", ["development", "test"])
def test_api_runtime_allows_fully_disabled_livekit_locally(
    base_settings: Settings,
    app_env: str,
) -> None:
    validate_api_runtime(
        base_settings.model_copy(update={"app_env": app_env, **LIVEKIT_DISABLED})
    )


@pytest.mark.parametrize(
    ("configured", "missing_names"),
    [
        ({"livekit_url": "wss://livekit.example.com"}, {"LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"}),
        ({"livekit_api_key": "key"}, {"LIVEKIT_URL", "LIVEKIT_API_SECRET"}),
        ({"livekit_api_secret": "secret"}, {"LIVEKIT_URL", "LIVEKIT_API_KEY"}),
        ({"livekit_url": "wss://livekit.example.com", "livekit_api_key": "key"}, {"LIVEKIT_API_SECRET"}),
        ({"livekit_url": "wss://livekit.example.com", "livekit_api_secret": "secret"}, {"LIVEKIT_API_KEY"}),
        ({"livekit_api_key": "key", "livekit_api_secret": "secret"}, {"LIVEKIT_URL"}),
    ],
)
def test_api_runtime_rejects_partial_livekit_configuration(
    base_settings: Settings,
    configured: dict[str, str],
    missing_names: set[str],
) -> None:
    settings = base_settings.model_copy(
        update={"app_env": "development", **LIVEKIT_DISABLED, **configured}
    )
    with pytest.raises(RuntimeError) as caught:
        validate_api_runtime(settings)
    assert missing_names <= set(str(caught.value).replace(",", "").split())


def test_staging_api_requires_complete_livekit_configuration(
    base_settings: Settings,
) -> None:
    settings = base_settings.model_copy(
        update={"app_env": "staging", **LIVEKIT_DISABLED}
    )
    with pytest.raises(RuntimeError) as caught:
        validate_api_runtime(settings)
    assert {"LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"} <= set(
        str(caught.value).replace(",", "").split()
    )
```

Keep the existing complete production validation test as the complete-state
case. Add a composition assertion that partial settings fail before resource
construction:

```python
@pytest.mark.anyio
async def test_api_composition_rejects_partial_livekit_before_resources_start(
    settings,
) -> None:
    from app.composition.api import build_api_runtime

    partial_settings = settings.model_copy(
        update={
            "app_env": "development",
            "livekit_url": "wss://livekit.example.com",
            "livekit_api_key": "key",
            "livekit_api_secret": None,
        }
    )
    with pytest.raises(RuntimeError, match="LIVEKIT_API_SECRET"):
        await build_api_runtime(
            partial_settings,
            engine_factory=_forbidden_factory("engine"),
        )
```

Because validation is the first builder operation, the forbidden engine factory
also proves Redis, observability, webhook, and recording factories cannot run.

- [ ] **Step 2: Write disabled-request RED tests**

In `test_dispatch_webhook.py`, install an `ApiRuntime` whose
`livekit_webhook_receiver` is `None`, call `/webhooks/livekit`, and assert:

```python
@pytest.mark.anyio
async def test_disabled_livekit_webhook_returns_service_unavailable(
    async_client,
) -> None:
    response = await async_client.post(
        "/webhooks/livekit",
        content=b"{}",
        headers={"authorization": "Bearer ignored"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "LiveKit webhook receiver is not initialized"
    }
```

Retain the existing recording dependency 503 assertion and add a paired test
if it does not already assert the disabled runtime directly. No provider factory
or handler service may run on either disabled request path.

- [ ] **Step 3: Run Task 12 RED tests**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/test_deployment_readiness.py \
  tests/composition/test_api_composition.py \
  tests/livekit/test_dispatch_webhook.py
```

Expected: the partial/staging state table and disabled webhook request fail for
the missing validation and `None` dereference behavior only.

- [ ] **Step 4: Implement the three-state validation rule**

In `runtime_validation.py`, define the group once and validate it before the
development early return:

```python
LIVEKIT_REQUIRED_SETTINGS = (
    "livekit_url",
    "livekit_api_key",
    "livekit_api_secret",
)


def _validate_livekit_configuration(settings: Settings, environment: str) -> None:
    missing = _require(settings, LIVEKIT_REQUIRED_SETTINGS)
    if not missing:
        return
    if len(missing) != len(LIVEKIT_REQUIRED_SETTINGS):
        raise RuntimeError(
            "Missing or invalid required runtime settings: " + ", ".join(missing)
        )
    if environment not in {"development", "test"}:
        raise RuntimeError(
            "Missing or invalid required runtime settings: " + ", ".join(missing)
        )
```

Call the helper from `validate_api_runtime` after authentication validation and
before any environment return. Keep the production required-settings tuple as
the final production-wide safety net; do not add a second LiveKit mode setting.

In `webhooks/livekit.py`, mirror the existing recording dependency:

```python
def get_webhook_receiver(request: Request) -> object:
    receiver = get_api_runtime(request.app).livekit_webhook_receiver
    if receiver is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LiveKit webhook receiver is not initialized",
        )
    return receiver
```

- [ ] **Step 5: Run Task 12 GREEN and static checks**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/test_deployment_readiness.py \
  tests/composition/test_api_composition.py \
  tests/livekit/test_dispatch_webhook.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check \
  app/core/runtime_validation.py app/webhooks/livekit.py \
  tests/test_deployment_readiness.py tests/composition/test_api_composition.py \
  tests/livekit/test_dispatch_webhook.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
```

Expected: all selected tests and static checks pass.

- [ ] **Step 6: Commit Task 12**

```bash
git add apps/api/app/core/runtime_validation.py \
  apps/api/app/webhooks/livekit.py \
  apps/api/tests/test_deployment_readiness.py \
  apps/api/tests/composition/test_api_composition.py \
  apps/api/tests/livekit/test_dispatch_webhook.py
git commit -m "fix(api): make LiveKit disablement explicit"
```

### Task 13: Delete obsolete worker and generated execution artifacts

**Files:**
- Delete: `apps/api/app/workers/outbox/_owned_resources.py`
- Delete: `apps/api/app/workers/outbox/_livekit_client.py`
- Delete: `apps/api/tests/workers/test_owned_resources.py`
- Delete: `.superpowers/sdd/2026-08-06-explicit-runtime-composition/task-3-report.md`
- Delete: `.superpowers/sdd/2026-08-06-explicit-runtime-composition/task-4-report.md`
- Delete: `.superpowers/sdd/2026-08-06-explicit-runtime-composition/task-5-report.md`
- Delete: `.superpowers/sdd/2026-08-06-explicit-runtime-composition/task-6-report.md`
- Delete: `.superpowers/sdd/2026-08-06-explicit-runtime-composition/task-7-report.md`
- Delete: `.superpowers/sdd/2026-08-06-explicit-runtime-composition/task-8-report.md`

**Interfaces:**
- Removes: operation-scoped resource ownership superseded by process-owned
  runtimes and an unconsumed LiveKit settings adapter.
- Preserves: `RuntimeCleanup`, API/worker runtime ownership, current provider
  construction, and the tracked design/plan/engineering decision record.

- [ ] **Step 1: Prove the production modules are dead**

From the repository root:

```bash
rg -n "operation_owned_resources|require_livekit_client_config|LiveKitClientConfig|LiveKitClientConfigurationError" \
  apps/api/app apps/api/tests
```

Expected: `_owned_resources.py` is referenced only by
`test_owned_resources.py`; `_livekit_client.py` has no external reference.
Any additional production consumer blocks this task and requires owner review.

- [ ] **Step 2: Prove durable documentation contains the approved record**

```bash
for decision in 6A-C1A 6A-C2A 6A-C3A 6A-C4A 6A-C5A; do
  rg -n "$decision" \
    docs/superpowers/specs/2026-08-06-explicit-runtime-composition-design.md \
    docs/superpowers/plans/2026-08-06-explicit-runtime-composition.md \
    docs/engineering/2026-07-30-agent-api-review-decisions.md
done
rg -n "3069|701|106|91\.92|80\.32|89\.25|73\.62" \
  docs/superpowers/plans/2026-08-06-explicit-runtime-composition.md \
  docs/engineering/2026-07-30-agent-api-review-decisions.md
```

Expected: every owner decision is present in tracked durable documentation and
the last complete verification evidence is retained outside task reports.

- [ ] **Step 3: Delete only the approved files**

Delete the nine listed files with patch-based file deletion. Do not delete the
ignored SDD progress ledger or any report outside the explicit-runtime-composition
directory. Do not modify `.gitignore`.

- [ ] **Step 4: Run post-deletion reference and API worker checks**

```bash
! rg -n "operation_owned_resources|require_livekit_client_config|LiveKitClientConfig|LiveKitClientConfigurationError" \
  apps/api/app apps/api/tests
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/composition/test_lifecycle.py \
  tests/composition/test_worker_composition.py \
  tests/workers/test_arq_worker.py
```

Confirm `test_owned_resources.py` is absent and the three remaining files pass.
Also run `ruff check app tests` and `mypy app` so deletion cannot hide a stale
import.

- [ ] **Step 5: Audit the deletion diff and commit Task 13**

```bash
git diff --name-status
git diff --check
git status --short
```

Expected: exactly the approved obsolete modules, orphan-only test, and six task
reports are deleted; no durable spec, plan, ledger, protected path, environment
file, or unrelated report is removed.

```bash
git add -u -- \
  apps/api/app/workers/outbox/_owned_resources.py \
  apps/api/app/workers/outbox/_livekit_client.py \
  apps/api/tests/workers/test_owned_resources.py \
  .superpowers/sdd/2026-08-06-explicit-runtime-composition
git commit -m "refactor: remove obsolete runtime artifacts"
```

### Task 14: Make agent settings and injected plugins authoritative

**Files:**
- Modify: `apps/agent/agent/debug_streams.py`
- Modify: `apps/agent/agent/pipeline_factory.py`
- Modify: `apps/agent/tests/test_debug_streams.py`
- Modify: `apps/agent/tests/test_pipeline_factory.py`

**Interfaces:**
- Changes: `StreamDebugLogger.from_dispatch_metadata(metadata, *, enabled)`;
  the caller must provide the validated boolean.
- Produces: `AgentPipelineConfigurationError`, a stable missing-plugin error
  that names the absent registry key and contains no credential values.
- Preserves: `plugin_modules=None` as the only production-default import path;
  explicit complete mappings and all existing pipeline/provider behavior.

- [ ] **Step 1: Write explicit-debug-settings RED tests**

In `test_pipeline_factory.py`, parameterize conflicting environment values:

```python
@pytest.mark.parametrize(
    ("environment_value", "settings_value"),
    [("true", False), ("false", True)],
)
def test_pipeline_debug_logger_uses_explicit_settings_when_environment_conflicts(
    monkeypatch: pytest.MonkeyPatch,
    environment_value: str,
    settings_value: bool,
) -> None:
    monkeypatch.setenv("AGENT_DEBUG_STREAMS", environment_value)
    agent, _session = build_agent_runtime(
        DEFAULT_DISPATCH_METADATA,
        settings=make_settings(agent_debug_streams=settings_value),
        plugin_modules=COMPLETE_FAKE_PLUGINS,
        session_cls=FakeSession,
    )
    assert isinstance(agent, InstrumentedAgent)
assert agent._debug_logger.enabled is settings_value
```

In `test_debug_streams.py`, lock the factory's explicit argument independently
of pipeline construction:

```python
import pytest


@pytest.mark.parametrize("enabled", [False, True])
def test_stream_debug_logger_factory_uses_explicit_enabled(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
) -> None:
    monkeypatch.setenv("AGENT_DEBUG_STREAMS", "true" if not enabled else "false")
    debug_logger = StreamDebugLogger.from_dispatch_metadata(
        {"call_id": "call_123", "user_id": "user_123"},
        enabled=enabled,
    )
    assert debug_logger.enabled is enabled
```

Define `DEFAULT_DISPATCH_METADATA` and `COMPLETE_FAKE_PLUGINS` once in the test
module to remove repeated fixture dictionaries only where all values are truly
identical. Do not turn variant provider configurations into one clever builder.

- [ ] **Step 2: Write explicit-registry RED tests**

Cover `None`, empty, partial, and complete registries:

```python
def test_agent_runtime_empty_plugin_registry_does_not_load_defaults(monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline_factory,
        "_default_plugin_modules",
        lambda *_args, **_kwargs: pytest.fail("explicit registry loaded defaults"),
    )
    with pytest.raises(
        pipeline_factory.AgentPipelineConfigurationError,
        match="speechmatics",
    ):
        build_agent_runtime(
            DEFAULT_DISPATCH_METADATA,
            settings=make_settings(),
            plugin_modules={},
            agent_cls=FakeAgent,
            session_cls=FakeSession,
        )
```

Add a partial registry case whose selected speech plugin exists but `google`,
`silero`, or `turn_detector_multilingual` is missing; assert the exact missing
key and that no default importer runs. Parameterize an STS empty/partial case
missing `google`. Add explicit `plugin_modules=None` tests for STT/LLM/TTS and
STS that record the selected default imports, and retain complete-mapping tests
for both modes. Optional VAD and turn-detector cases use settings that enable one
at a time and assert the missing optional registry key without importing a
default.

- [ ] **Step 3: Run Task 14 RED tests**

```bash
cd apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/test_debug_streams.py tests/test_pipeline_factory.py
```

Expected: conflict cases expose the environment reread, and empty/partial
registries either load defaults or raise raw `KeyError` instead of the required
stable configuration error.

- [ ] **Step 4: Remove the debug environment read**

Delete `debug_streams_enabled` and its `os` import. Change the factory and caller:

```python
@classmethod
def from_dispatch_metadata(
    cls,
    metadata: dict[str, Any],
    *,
    enabled: bool,
) -> "StreamDebugLogger":
    return cls(
        enabled=enabled,
        call_id=metadata.get("call_id"),
        user_id=metadata.get("user_id"),
    )
```

```python
debug_logger=StreamDebugLogger.from_dispatch_metadata(
    dispatch_metadata,
    enabled=settings.agent_debug_streams,
)
```

Do not add a logger factory or another settings lookup.

- [ ] **Step 5: Make injected registry failures explicit**

In `pipeline_factory.py`:

```python
class AgentPipelineConfigurationError(RuntimeError):
    """The selected agent pipeline is missing an explicitly required plugin."""


def _require_plugin(plugins: dict[str, Any], name: str) -> Any:
    try:
        return plugins[name]
    except KeyError:
        raise AgentPipelineConfigurationError(
            f"Required pipeline plugin is unavailable: {name}"
        ) from None
```

Resolve the registry with an identity check:

```python
plugins = (
    _default_plugin_modules(settings, config)
    if plugin_modules is None
    else plugin_modules
)
```

Use `_require_plugin` at each selected STT, LLM, TTS, STS, VAD, and turn-detector
lookup. Assign a repeated selected module to a local variable so Speechmatics
mode resolution and construction do not perform the same lookup twice. Do not
catch exceptions raised inside plugin constructors.

- [ ] **Step 6: Run Task 14 GREEN and static checks**

```bash
cd apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/test_debug_streams.py tests/test_pipeline_factory.py \
  tests/test_runtime_validation.py tests/test_composition.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check \
  agent/debug_streams.py agent/pipeline_factory.py \
  tests/test_debug_streams.py tests/test_pipeline_factory.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
```

Expected: all selected tests and static checks pass; environment conflict tests
prove settings authority and no test imports an unrequested production plugin.

- [ ] **Step 7: Commit Task 14**

```bash
git add apps/agent/agent/debug_streams.py \
  apps/agent/agent/pipeline_factory.py \
  apps/agent/tests/test_debug_streams.py \
  apps/agent/tests/test_pipeline_factory.py
git commit -m "fix(agent): enforce explicit pipeline configuration"
```

### Task 15: Reverify the complete corrected Issue 6 range

**Files:**
- Modify: `docs/superpowers/plans/2026-08-06-explicit-runtime-composition.md`
- Modify: `docs/engineering/2026-07-30-agent-api-review-decisions.md`

**Interfaces:**
- Consumes: independently approved Tasks 12–14.
- Produces: exact final test counts, coverage, cleanup evidence, corrected commit
  range, and a second complete-range Standards/Spec approval.

- [x] **Step 1: Run frozen dependency and static gates**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
```

A lockfile change is out of scope and must stop the task.

- [x] **Step 2: Run the complete API suite in isolated services**

Verify loopback ports 55467 and 56397 are unused, then start only these two
uniquely named disposable containers:

```bash
! ss -ltn | rg -q ':55467|:56397'
docker run --detach --rm --name opevo-issue6a-final-postgres \
  --env POSTGRES_DB=ai_call_test \
  --env POSTGRES_USER=postgres \
  --env POSTGRES_PASSWORD=postgres \
  --publish 127.0.0.1:55467:5432 \
  --health-cmd='pg_isready -U postgres -d ai_call_test' \
  --health-interval=5s --health-timeout=5s --health-retries=10 \
  postgres:17.8-bookworm
docker run --detach --rm --name opevo-issue6a-final-redis \
  --publish 127.0.0.1:56397:6379 \
  --health-cmd='redis-cli ping' \
  --health-interval=5s --health-timeout=5s --health-retries=10 \
  redis:7.4.7-alpine
```

Wait for both health checks, then export only the isolated service URLs:

```bash
until test "$(docker inspect --format '{{.State.Health.Status}}' \
  opevo-issue6a-final-postgres)" = healthy; do sleep 1; done
until test "$(docker inspect --format '{{.State.Health.Status}}' \
  opevo-issue6a-final-redis)" = healthy; do sleep 1; done
export APP_ENV=test
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55467/ai_call_test
export TEST_DATABASE_URL="$DATABASE_URL"
export REDIS_URL=redis://127.0.0.1:56397/0
export TEST_REDIS_URL="$REDIS_URL"
```

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  --cov=app --cov-report=term-missing --cov-report=json:coverage.json
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python \
  ../../scripts/check_python_coverage.py check \
  --report coverage.json --baseline coverage-baseline.json
```

Expected: zero API skips or failures and no line/branch coverage regression from
91.92%/80.32%.

- [x] **Step 3: Run the complete agent and cross-runtime suites**

```bash
cd apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  --cov=agent --cov-report=term-missing --cov-report=json:coverage.json
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python \
  ../../scripts/check_python_coverage.py check \
  --report coverage.json --baseline coverage-baseline.json
cd ../api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/integration/test_agent_runtime_transcript_durability.py \
  tests/integration/test_local_activation_to_number.py \
  tests/integration/test_outbox_delivery.py \
  tests/integration/test_worker_queue_isolation.py \
  tests/livekit/test_dispatch_webhook.py \
  tests/workers/test_outbox_architecture.py \
  tests/test_composition_architecture.py
```

Four credentialed LiveKit evaluations may skip; no other skip is accepted.
Expected: no regression from 89.25%/73.62% and all cross-runtime tests pass.

- [x] **Step 4: Remove isolated resources and audit the repository**

Remove only Task 15's two explicitly named disposable containers:

```bash
docker rm --force opevo-issue6a-final-postgres opevo-issue6a-final-redis
test -z "$(docker ps -a --filter name=opevo-issue6a-final --format '{{.Names}}')"
docker compose -f compose.dev.yaml ps
```

The final command must still show only the original seven `bmad-opevo` services
with their prior health. No named network or volume is created by Step 2. Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/test_composition_architecture.py
cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/test_composition_architecture.py
cd ../..
! rg -n "operation_owned_resources|require_livekit_client_config|debug_streams_enabled" \
  apps/api/app apps/api/tests apps/agent/agent apps/agent/tests
test -z "$(git ls-files '.superpowers/sdd/2026-08-06-explicit-runtime-composition/task-*-report.md')"
git diff --check
git status --short
```

Also compare the final changed-path list with the protected-path list before
recording completion:

```bash
! git diff --name-only c56187794d3c12e0daca833f5f8f2e729e98eead...HEAD | \
  rg '^(Opevo_frontend/|\.worktrees/shadcn-activation-preview/)|(^|/)\.env$'
```

Any protected or real environment file stops the task. The fixed `/tmp`
voice/Telnyx/Clerk override files are not read, changed, or used by this plan.

- [x] **Step 5: Update durable evidence and commit it separately**

Record exact test counts, coverage, correction commit hashes, cleanup evidence,
and owner decisions 43A–47A in the plan and engineering ledger. Keep Issue 6A
`Implemented`; leave 1A, 14A, 16A, realtime, deployment, and worker extraction
unchanged. Commit only the two durable documentation files.

- [ ] **Step 6: Run a fresh complete-range two-axis review**

Two fresh read-only reviewers compare `c56187794d3c12e0daca833f5f8f2e729e98eead...HEAD`:

- Standards: `CONTRIBUTING.md`, repository tooling, and the Fowler smell baseline.
- Spec: the approved design, this plan, engineering decision ledger, and every
  owner decision through 47A.

Both axes must return zero findings. Any finding is presented to the owner with
options; no unapproved implementation loop begins.

#### Task 15 verification evidence before the final complete-range review

The owner approved 43A, 44A, 45A, 46A, and 47A, followed by the bounded
clarifications 43A-1A, 43A-2A, 43A-3A, 45A-1A, 45A-2A, and 45A-3A. The
correction implementation is recorded by:

- `4264b2f`, `ebf8083`, and `fd03ad1` for the explicit three-state LiveKit
  validation, shared setting group, disabled-request behavior, and explicit
  test-state table;
- `ece5307`, `48056ea`, and `f26a709` for durable decision preservation,
  deletion of the two obsolete worker mechanisms and tracked generated
  reports, and the corrected optional environment-source wording; and
- `a86c7fb` for authoritative agent debug settings and explicit injected
  plugin registries.

Each correction endpoint received independent Spec and Standards approval with
zero open findings before Task 15. The first complete API run then exposed a
0.007-point line-coverage rounding regression after the approved deletion. The
owner approved 48A: add meaningful coverage for the previously untested unsafe
Clerk JWKS URL validation path rather than a coverage-only assertion. Commit
`dafbec9` implements that test-only correction and received independent Spec
and Standards approval with zero findings.

Fresh verification at `dafbec9044eaefaac76fe4555b7f3c7d74e417c4` produced:

- both frozen lock checks passed; complete Ruff checks passed for API and agent;
  mypy passed for 187 API source files and 16 agent source files;
- 3,091 API tests passed with zero skips or failures against the uniquely named
  disposable PostgreSQL and Redis services, with 11,822 of 12,860 statements
  covered (91.928460%, reported as 91.93%) and 2,685 of 3,340 branches covered
  (80.389222%, reported as 80.39%); the stored coverage ratchet passed;
- 714 agent tests passed with only the four approved credentialed LiveKit
  evaluation skips, with 1,347 of 1,506 statements covered (89.442231%,
  reported as 89.44%) and 297 of 398 branches covered (74.623116%, reported as
  74.62%); the stored coverage ratchet passed;
- the exact cross-runtime slice passed 108 tests; and
- the focused API architecture guard passed 34 tests and the agent guard passed
  seven tests.

Ports 55467 and 56397 and the two exact disposable container names were clear
before startup. After the suites, only `opevo-issue6a-final-postgres` and
`opevo-issue6a-final-redis` were removed. Container, named-network, and
named-volume filters for `opevo-issue6a-final` were empty afterward. The
original seven `bmad-opevo` services retained their preflight state: web, API,
PostgreSQL, Redis, and MinIO were healthy, and both workers were running.

The obsolete-resource/debug-setting reference scan, protected-path scan,
tracked-report scan, `git diff --check`, and repository status audit were clean.
The approved local-only retention is evidenced separately: all six Task 3-8
reports remain present locally, ignored, and untracked. No real environment file
or fixed `/tmp` voice, Telnyx, or Clerk override was read, changed, or used. The
complete corrected implementation endpoint is
`c56187794d3c12e0daca833f5f8f2e729e98eead...dafbec9044eaefaac76fe4555b7f3c7d74e417c4`;
this Task 15 evidence commit is documentation-only and intentionally follows
that endpoint. Step 6 remains pending until two fresh read-only reviewers assess
the range including this evidence commit.

#### Post-49A and 50A verification evidence before definitive review

The first corrected complete-range Spec review reported zero findings. Its
Standards review approved the range with one non-blocking cleanup finding: the
unreachable `LiveKitDispatchConfigurationError` compatibility surface. The
owner approved **49A**, recorded in the
[LiveKit Dispatch Error Cleanup Plan](2026-08-07-livekit-dispatch-error-cleanup.md),
to remove the dead exception, its two translations, and synthetic-only tests
without adding an alias, broad catch, or replacement policy. Commit
`d7f2a0d942844ff500f884ab29106cb01ac5bf15` implements that exact cleanup and
received fresh independent Spec and Standards approvals with zero findings.

The first complete post-49A API run then identified a meaningful untested
contract: malformed LiveKit list responses were covered, while malformed create
responses were not. The owner approved **50A** rather than lowering the
coverage target. Test-only commit
`6dc96ff6ee6f5e6272573f2b0315b46a34ea9637` parameterizes the same explicit
failure contract across list and create operations, adding one collected case
without changing production behavior.

Fresh final verification at that code endpoint passed both frozen lock checks,
complete API/agent Ruff checks, and mypy for 187 API and 16 agent source files.
The API passed 3,089 tests with zero skips or failures, covering 11,817 of
12,853 statements (91.939625%, reported as 91.94%) and 2,685 of 3,340 branches
(80.389222%, reported as 80.39%). The agent passed 714 tests with exactly the
four approved credentialed LiveKit evaluation skips, covering 1,347 of 1,506
statements (89.442231%, reported as 89.44%) and 297 of 398 branches
(74.623116%, reported as 74.62%). Both stored coverage ratchets passed. The
exact cross-runtime slice passed 104 tests; the API and agent architecture
guards passed 34 and seven tests.

Ports 55469 and 56399 and the two exact `opevo-issue6a-final3` container names
were clear before startup. Only those healthy disposable PostgreSQL and Redis
containers were removed after verification, no matching container remained,
and the original seven `bmad-opevo` services matched preflight. Obsolete-error,
tracked-report, protected-path, diff, and status audits were clean, while all
six approved Task 3-8 local reports remained ignored and untracked. No real
environment file, fixed `/tmp` override, deployment, provider account, or
non-isolated database was read or changed.

The complete post-50A code endpoint is
`c56187794d3c12e0daca833f5f8f2e729e98eead...6dc96ff6ee6f5e6272573f2b0315b46a34ea9637`.
The final evidence commit is documentation-only and intentionally follows that
endpoint. A fresh complete-range Spec and Standards review of the range
including the evidence commit remains the final integration gate. Issue 6A
remains Implemented; realtime, deployment, database, queue-policy, worker-source
extraction, and Issues 1A, 14A, and 16A remain unchanged.

#### Post-51A, 52A, and 53A verification evidence before definitive review

Owner decision **51A** closes runtime environment selection in both executables
to `development`, `test`, `staging`, and `production`, canonicalizing case and
surrounding whitespace before the explicit `Literal` boundary and rejecting
empty, misspelled, and custom values from constructor and process sources.
Test-first RED evidence covered those boundary cases, dotenv hermeticity,
composition, development-only routing, and production validation. Commit
`e591478f7f23e8dad621b6197fff2a55eed6a7a2` completed GREEN without a shared
cross-app abstraction. Independent Spec and Standards reviews each approved
51A with zero findings.

Owner decision **52A** adds a focused test-only characterization of late API
composition cancellation. It retains the exact `asyncio.CancelledError`, proves
an absent cause, and proves every earlier owned resource closes exactly once in
reverse registration order. The characterization passed against existing
production behavior, so no production fix was needed. Commit
`219155c674cab3d5cc3f64d587c76cd2b68d9bfb` received independent Spec and
Standards/test-quality approvals with zero findings.

The first complete post-52A API run produced 3,106 passes and one failure: the
dashboard-reference-time matrix still treated `preview` as a valid environment,
while 51A correctly rejected it at the environment boundary first. Owner
decision **53A** removed only that overlapping dashboard-specific parameter;
the API and agent constructor/process invalid-environment matrices still
explicitly cover `preview`. Test-only commit
`7c93cb521b8e98bbf51b5e4c2226da943b5142e5` completed GREEN and received
independent Spec and Standards/test-quality approvals with zero findings. The
approved correction design/plan commit is `7624f34`.

Fresh final6 verification at code endpoint
`7c93cb521b8e98bbf51b5e4c2226da943b5142e5` produced:

- both frozen lock checks and complete API/agent Ruff checks passed; mypy passed
  for 187 API and 16 agent source files;
- 3,106 API tests passed with zero skips or failures and one dependency
  deprecation warning, covering 11,826 of 12,862 statements (91.945265%,
  reported as 91.95%) and 2,688 of 3,342 branches (80.430880%, reported as
  80.43%); both the stored ratchet and stricter 91.93%/80.39% endpoint passed;
- 732 agent tests passed with exactly the four approved credentialed LiveKit
  evaluation skips, covering 1,360 of 1,517 statements (89.650626%, reported
  as 89.65%) and 299 of 400 branches (74.75%); both the stored ratchet and
  stricter 89.44%/74.62% endpoint passed;
- the exact cross-runtime slice passed 104 tests; the API and agent architecture
  guards passed 38 and seven tests; and
- explicit constructor/process canonicalization and invalid-environment slices
  passed 16 API and 16 agent cases; obsolete runtime and dispatch-error
  references remained absent.

Ports 55472 and 56402 and the exact `opevo-issue6a-final6-postgres` and
`opevo-issue6a-final6-redis` names were clear before startup. Both disposable
services became healthy; afterward only those exact containers were removed,
and matching container, network, volume, port, and name checks were empty. The
original seven running `bmad-opevo` services and the pre-existing successful
`migrate`/`minio-init` one-shots matched preflight by container ID and state.

The protected-path, lock/baseline-hash, tracked-report, stale-reference,
`git diff --check`, and status audits were clean. All six approved Task 3-8
reports remain present locally, ignored, and untracked. No protected path, real
environment file, fixed `/tmp` voice/Telnyx/Clerk override, deployment,
provider account, non-isolated database, lockfile, baseline, or threshold was
read or changed. All Python test and coverage evidence above was produced
outside the filesystem sandbox; the earlier in-sandbox timeout attempt is
discarded environment evidence, not a product failure.

The complete corrected code endpoint is
`c56187794d3c12e0daca833f5f8f2e729e98eead...7c93cb521b8e98bbf51b5e4c2226da943b5142e5`.
This evidence update is documentation-only and intentionally follows that
endpoint. Fresh definitive complete-range Spec and Standards reviews including
the evidence commit remain pending and must both report zero findings before
integration is offered. Issue 6A remains Implemented; realtime, deployment,
database, queue policy, worker-source extraction, and Issues 1A, 14A, and 16A
remain unchanged.
