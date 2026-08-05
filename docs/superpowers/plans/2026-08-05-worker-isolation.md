# Call-Lifecycle and Background Worker Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Isolate customer-visible call-lifecycle work from slower background work with explicit Redis routing, independently managed ARQ workers, bounded execution policies, safe queue telemetry, and a controlled ten-call concurrency proof.

**Architecture:** Keep PostgreSQL as the durable authority and use two fixed ARQ queues as execution/wakeup channels. Route all queue writes through two small explicit interfaces, wrap each registered job with a code-owned semantic policy, and run two settings classes from the same API image. A payload-blind observer and a real-Redis test prove that saturated background capacity cannot consume lifecycle capacity.

**Tech Stack:** Python 3.13, FastAPI, Pydantic Settings, ARQ 0.27-compatible APIs, Redis 7.4, SQLAlchemy 2, OpenTelemetry, pytest/AnyIO, Docker Compose.

## Global Constraints

- Work only in `/home/mo/code/ai/bmad-opevo/.worktrees/worker-isolation` on `codex/worker-isolation`.
- Do not inspect or modify `Presvo_frontend/` or `.worktrees/shadcn-activation-preview`.
- Do not read actual `.env` files; modify only `apps/api/.env.example` when documenting safe settings.
- Do not modify `/tmp/presvo-voice-e2e.override.yaml`, `/tmp/presvo-telnyx-e2e.override.yaml`, or `/tmp/presvo-clerk-e2e.override.yaml`.
- Do not alter the main API virtual environment at `/home/mo/code/ai/bmad-opevo/apps/api/.venv`.
- Do not deploy, recreate, restart, or otherwise alter the user's live local services. A separately named disposable Redis container is allowed only for the isolated acceptance test and must be removed afterward.
- Keep production API and worker telephony on Telnyx; do not add fake-provider or database shortcuts.
- Realtime Issues 1A and 14A remain deferred.
- Use `CALL_LIFECYCLE_QUEUE_NAME = "arq:queue"` and `BACKGROUND_QUEUE_NAME = "arq:queue:background"`; queue names are code constants, never environment settings.
- Expose only `WORKER_LIFECYCLE_MAX_JOBS` (default 10, range 1–100) and `WORKER_BACKGROUND_MAX_JOBS` (default 4, range 1–50) as worker-capacity environment settings.
- Lifecycle jobs are exactly call finalization and call reconciliation. Background jobs are exactly outbox delivery, outbox reconciliation, and forwarding-verification expiry.
- Preserve best-effort outbox wakeups after durable commits and customer-visible failure when call-finalization enqueue is unavailable.
- Never log or measure job payloads, job IDs, phone numbers, transcripts, provider bodies, tokens, or credentials.
- Apply strict red-green-refactor and retain every regression test introduced by this plan.

---

## File Structure

- Create `apps/api/app/workers/queueing.py`: fixed queue names and the sole background-wakeup enqueue function.
- Modify `apps/api/app/workers/call_finalization_queue.py`: sole call-finalization enqueue implementation with deterministic ID and explicit queue.
- Create `apps/api/app/workers/job_policy.py`: immutable per-job policies, semantic timeout wrapper, and narrow call-finalization retry adapter.
- Modify `apps/api/app/core/observability.py`: bounded worker labels, queue gauges, and attempt-aware timeout/cancellation instrumentation.
- Create `apps/api/app/workers/queue_observer.py`: payload-blind queue depth/oldest-due sampler with idempotent lifecycle.
- Modify `apps/api/app/workers/arq_worker.py`: shared startup/shutdown lifecycle and two disjoint ARQ settings classes.
- Modify `apps/api/app/core/config.py` and `apps/api/.env.example`: validated concurrency controls and safe documentation.
- Modify the ten current outbox wakeup callers and `apps/api/app/services/livekit_dispatch_service.py`: route through the two explicit enqueue interfaces while retaining caller-owned logging and transaction behavior.
- Modify `compose.dev.yaml`, `compose.yaml`, and `scripts/run-local-e2e.sh`: two services, healthchecks, grace periods, dependencies, and restart/log behavior.
- Add focused tests under `apps/api/tests/workers/` and update affected existing API tests/fakes.
- Update architecture, deployment, incident, rollback, project-status, and engineering-decision documentation listed in Task 7.

### Task 1: Establish explicit queue contracts and remove direct enqueue duplication

**Files:**
- Create: `apps/api/app/workers/queueing.py`
- Create: `apps/api/tests/workers/test_queue_routing.py`
- Modify: `apps/api/app/workers/call_finalization_queue.py`
- Modify: `apps/api/app/routers/agent.py`
- Modify: `apps/api/app/routers/calls.py`
- Modify: `apps/api/app/services/account_lifecycle_service.py`
- Modify: `apps/api/app/services/activation_go_live_service.py`
- Modify: `apps/api/app/services/activation_provisioning_service.py`
- Modify: `apps/api/app/services/agent_config_service.py`
- Modify: `apps/api/app/services/billing_service.py`
- Modify: `apps/api/app/services/livekit_dispatch_service.py`
- Modify: `apps/api/app/webhooks/livekit.py`
- Modify: `apps/api/app/workers/jobs/call_reconciliation.py`
- Modify: `apps/api/tests/fakes.py`
- Modify: affected tests in `apps/api/tests/activation/`, `apps/api/tests/billing/`, `apps/api/tests/calls/`, `apps/api/tests/livekit/`, and `apps/api/tests/workers/`

**Interfaces:**
- Produces: `CALL_LIFECYCLE_QUEUE_NAME: Final[str]`, `BACKGROUND_QUEUE_NAME: Final[str]`, `QUEUE_CLASS_CALL_LIFECYCLE: Final[str]`, and `QUEUE_CLASS_BACKGROUND: Final[str]`.
- Produces: `async def enqueue_outbox_wakeup(redis: ArqRedis) -> None`.
- Produces: `CallFinalizationQueue.enqueue(payload: dict) -> str`, retaining `call-finalization:{call_id}` as `_job_id`.
- Consumes: ARQ's reserved `_queue_name` and `_job_id` enqueue keywords.

- [ ] **Step 1: Create an isolated worktree-local API environment**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups
```

Expected: dependencies install into this worktree's ignored
`apps/api/.venv`; the main checkout's API environment remains untouched.

- [ ] **Step 2: Write failing literal-routing and single-owner tests**

```python
class CapturePool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict]] = []

    async def enqueue_job(self, name: str, payload: dict, **kwargs) -> None:
        self.calls.append((name, payload, kwargs))


@pytest.mark.anyio
async def test_background_wakeup_uses_fixed_background_queue() -> None:
    pool = CapturePool()
    await enqueue_outbox_wakeup(pool)
    assert pool.calls == [
        ("outbox_delivery_job", {}, {"_queue_name": "arq:queue:background"})
    ]


@pytest.mark.anyio
async def test_call_finalization_keeps_id_and_uses_lifecycle_queue() -> None:
    pool = CapturePool()
    call_id = uuid4()
    job_id = await CallFinalizationQueue(pool).enqueue({"call_id": str(call_id)})
    assert job_id == f"call-finalization:{call_id}"
    assert pool.calls == [
        (
            "call_finalization_job",
            {"call_id": str(call_id)},
            {"_job_id": job_id, "_queue_name": "arq:queue"},
        )
    ]
```

Add an AST-based ownership test that walks `apps/api/app/**/*.py`, finds calls whose attribute is `enqueue_job`, and asserts that literal `outbox_delivery_job` calls exist only in `workers/queueing.py` and literal `call_finalization_job` calls exist only in `workers/call_finalization_queue.py`. This catches later bypasses without rejecting worker registration strings.

- [ ] **Step 3: Run the routing tests and verify RED**

Run: `cd apps/api && uv run --frozen --no-sync python -m pytest tests/workers/test_queue_routing.py -q`

Expected: FAIL because `app.workers.queueing` does not exist and finalization omits `_queue_name`.

- [ ] **Step 4: Add the fixed routing seam**

```python
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
```

Change `CallFinalizationQueue.enqueue()` to pass both `_job_id=job_id` and `_queue_name=CALL_LIFECYCLE_QUEUE_NAME`. Replace each existing outbox `enqueue_job` call with `enqueue_outbox_wakeup(pool)` but leave its surrounding `try/except`, safe operation label, and post-commit position unchanged. In `LiveKitDispatchService.handle_participant_left()`, replace the duplicated finalization job name/ID construction with `CallFinalizationQueue(self.arq_pool).enqueue({"call_id": str(call.id)})`.

- [ ] **Step 5: Make test doubles accept reserved ARQ keywords and strengthen caller assertions**

Keep broad shared fakes backward compatible:

```python
async def enqueue_job(self, name, payload, **_kwargs):
    self.enqueued_jobs.append((name, payload))
```

For routing-sensitive tests, capture the kwargs and assert `_queue_name == "arq:queue:background"` or `_queue_name == "arq:queue"`. Preserve tests proving that a failed background wakeup does not roll back durable state and that a failed finalization enqueue remains visible to the caller/log path.

- [ ] **Step 6: Run focused callers and verify GREEN**

Run:

```bash
cd apps/api
uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_queue_routing.py \
  tests/workers/test_call_reconciliation_wakeup.py \
  tests/livekit/test_durable_dispatch_service.py \
  tests/livekit/test_durable_dispatch_webhook.py \
  tests/activation/test_activation_go_live_service.py \
  tests/activation/test_activation_provisioning_service.py \
  tests/billing/test_stripe_webhooks.py \
  tests/calls/test_call_history_api.py
```

Expected: PASS, including the existing enqueue-failure durability cases.

- [ ] **Step 7: Commit the queue boundary**

```bash
git add apps/api/app apps/api/tests
git commit -m "refactor: route worker jobs to explicit queues"
```

### Task 2: Add bounded job policies, retry classification, and attempt-aware observability

**Files:**
- Create: `apps/api/app/workers/job_policy.py`
- Create: `apps/api/tests/workers/test_job_policy.py`
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/app/core/observability.py`
- Modify: `apps/api/app/workers/arq_worker.py`
- Modify: `apps/api/.env.example`
- Modify: `apps/api/tests/conftest.py`
- Modify: `apps/api/tests/test_observability.py`
- Modify: `apps/api/tests/test_reconciliation_settings.py`

**Interfaces:**
- Produces: immutable `JobPolicy` values for all five jobs, with `hard_timeout_seconds == semantic_timeout_seconds + 5`.
- Produces: `def apply_job_policy(function, *, policy: JobPolicy, queue_class: str)` returning an async ARQ-compatible callable.
- Produces: `def is_retryable_call_finalization_error(error: BaseException) -> bool`.
- Produces: `Settings.worker_lifecycle_max_jobs: int` and `Settings.worker_background_max_jobs: int`.
- Changes: `instrument_job(job_name: str, *, queue_class: str)` and worker metric recorders require bounded queue class/job/outcome/attempt attributes.

- [ ] **Step 1: Write failing settings and policy tests**

```python
def test_worker_capacity_defaults_and_boundaries() -> None:
    settings = Settings(database_url="sqlite+aiosqlite://", redis_url="redis://localhost")
    assert settings.worker_lifecycle_max_jobs == 10
    assert settings.worker_background_max_jobs == 4
    for name, value in (
        ("worker_lifecycle_max_jobs", 0),
        ("worker_lifecycle_max_jobs", 101),
        ("worker_background_max_jobs", 0),
        ("worker_background_max_jobs", 51),
    ):
        with pytest.raises(ValidationError):
            Settings(
                database_url="sqlite+aiosqlite://",
                redis_url="redis://localhost",
                **{name: value},
            )


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (TimeoutError(), True),
        (SQLAlchemyTimeoutError(), True),
        (OperationalError("statement", {}, Exception()), True),
        (DisconnectionError(), True),
        (DBAPIError("statement", {}, Exception(), connection_invalidated=True), True),
        (IntegrityError("statement", {}, Exception()), False),
        (ValueError("bad payload"), False),
        (RuntimeError("defect"), False),
        (asyncio.CancelledError(), False),
    ],
)
def test_call_finalization_retry_classifier_is_narrow(error, retryable) -> None:
    assert is_retryable_call_finalization_error(error) is retryable
```

Add async tests that execute the wrapper and assert first/second retryable failures raise `arq.Retry` with `defer_score` 1000/5000, the third raises the original exception, non-retryable errors and `CancelledError` are unchanged, and a blocked coroutine becomes `TimeoutError` at its semantic bound.

- [ ] **Step 2: Write failing observability outcome and cardinality tests**

Extend `test_observability.py` to require these exact attributes:

```python
assert delay[0][1] == {
    "queue_class": "call_lifecycle",
    "job": "call_finalization",
    "attempt": 2,
}
assert duration[0][1] == {
    "queue_class": "call_lifecycle",
    "job": "call_finalization",
    "outcome": "success",
    "attempt": 2,
}
```

Execute success, `TimeoutError`, `CancelledError`, and ordinary error paths. Pass sentinel queue/job/attempt values and assert they collapse to `unknown`, `unknown`, and `0`; assert the sentinel text and `job_id` never appear in measurements or span attributes. Add `verification_expiry` to the accepted job-name test.

- [ ] **Step 3: Run both new test groups and verify RED**

Run: `cd apps/api && uv run --frozen --no-sync python -m pytest tests/workers/test_job_policy.py tests/test_observability.py -q`

Expected: FAIL because policy/settings do not exist and instrumentation lacks queue, attempt, timeout, and cancellation distinctions.

- [ ] **Step 4: Implement settings, constants, semantic bounds, and retry adapter**

Add to `Settings`:

```python
worker_lifecycle_max_jobs: int = Field(default=10, ge=1, le=100)
worker_background_max_jobs: int = Field(default=4, ge=1, le=50)
```

Add `WORKER_LIFECYCLE_MAX_JOBS=10` and `WORKER_BACKGROUND_MAX_JOBS=4` to
`_construction_settings_environment()` so inherited shell values cannot make
tests nondeterministic and the test-mode settings source never reads dotenv.

Document both values in `apps/api/.env.example` without adding secrets:

```dotenv
# Independent ARQ capacity controls; queue names and job policies are code-owned.
WORKER_LIFECYCLE_MAX_JOBS=10
WORKER_BACKGROUND_MAX_JOBS=4
```

Implement policy constants with these exact values:

```python
CALL_FINALIZATION_POLICY = JobPolicy("call_finalization_job", "call_finalization", 30, 3, (1, 5))
CALL_RECONCILIATION_POLICY = JobPolicy("call_reconciliation_job", "call_reconciliation", 60, 1)
OUTBOX_DELIVERY_POLICY = JobPolicy("outbox_delivery_job", "outbox_delivery", 300, 1)
OUTBOX_RECONCILIATION_POLICY = JobPolicy("outbox_reconciliation_job", "outbox_reconciliation", 300, 1)
VERIFICATION_EXPIRY_POLICY = JobPolicy("verification_expiry_job", "verification_expiry", 60, 1)
```

The implementation order inside `apply_job_policy` must be semantic timeout, then instrumentation, then the retry adapter. The retry adapter reads one-based `ctx["job_try"]`, retries only attempts 1 and 2, and raises `Retry(defer=1)` then `Retry(defer=5)`. Check `IntegrityError` before the broader SQLAlchemy database classes, and classify `DBAPIError` only when `connection_invalidated is True`. Do not classify from exception strings.

- [ ] **Step 5: Implement bounded observability**

Add queue classes `{call_lifecycle, background}`, outcomes `{success, error, timeout, cancelled}`, attempts `{1, 2, 3}` plus numeric fallback `0`, and `verification_expiry` to the fixed allowlists. Change the recording interfaces to:

```python
def record_worker_queue_delay(
    self, queue_class: str, job: str, attempt: int, seconds: float
) -> None: ...

def record_worker_job_duration(
    self,
    queue_class: str,
    job: str,
    outcome: str,
    attempt: int,
    seconds: float,
) -> None: ...
```

In `instrument_job`, normalize naive enqueue timestamps to UTC, clamp negative delay to zero, classify `CancelledError` before `TimeoutError` before `BaseException`, and record the exact bounded attributes. Keep call-ID extraction limited to an independently validated UUID and never record the ARQ job ID.

Update every existing `instrument_job` call in `arq_worker.py` in this same
cycle: call finalization/reconciliation use `call_lifecycle`; outbox delivery,
outbox reconciliation, and verification expiry use `background`. This keeps the
module importable before Task 4 replaces these temporary mixed-registry
wrappers with the final policy-owned registrations.

- [ ] **Step 6: Run policy, settings, and observability tests and verify GREEN**

Run:

```bash
cd apps/api
uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_job_policy.py \
  tests/test_observability.py \
  tests/test_reconciliation_settings.py
uv run --frozen --no-sync ruff check app/workers/job_policy.py app/core/config.py app/core/observability.py tests/workers/test_job_policy.py tests/test_observability.py
uv run --frozen --no-sync mypy app/workers/job_policy.py app/core/config.py app/core/observability.py
```

Expected: all tests and static checks PASS.

- [ ] **Step 7: Commit execution policies**

```bash
git add apps/api/app/workers/job_policy.py apps/api/app/workers/arq_worker.py apps/api/app/core/config.py apps/api/app/core/observability.py apps/api/.env.example apps/api/tests
git commit -m "feat: enforce bounded worker job policies"
```

### Task 3: Add the payload-blind queue observer

**Files:**
- Create: `apps/api/app/workers/queue_observer.py`
- Create: `apps/api/tests/workers/test_queue_observer.py`
- Modify: `apps/api/app/core/observability.py`
- Modify: `apps/api/tests/test_observability.py`

**Interfaces:**
- Produces: `QueueObserver(redis, telemetry, *, queue_name: str, queue_class: str, interval_seconds: float = 15.0, now: Callable[[], float] = time.time)`.
- Produces: `QueueObserver.start() -> None`, `QueueObserver.sample() -> None`, and idempotent `QueueObserver.aclose() -> None`.
- Adds: `Observability.record_worker_queue_snapshot(queue_class: str, *, depth: int, oldest_due_age_seconds: float) -> None`.

- [ ] **Step 1: Write failing observer tests**

Use a fake Redis object exposing only `zcard(queue_name)` and `zrange(queue_name, 0, 0, withscores=True)`. Require:

```python
await observer.sample()
assert redis.calls == [
    ("zcard", "arq:queue"),
    ("zrange", "arq:queue", 0, 0, True),
]
assert telemetry.snapshots == [
    ("call_lifecycle", 3, pytest.approx(2.5))
]
```

Cover an empty queue, an oldest score in the future (age exactly 0), a due score (non-negative seconds), invalid/empty Redis replies, Redis failure followed by successful next sampling, `start()` called twice, `aclose()` called twice, and cancellation while sleeping. Assert the fake's `close`/`aclose` method is never called and a private payload sentinel cannot enter logs or metrics because `zrange` returns scores only.

- [ ] **Step 2: Run observer tests and verify RED**

Run: `cd apps/api && uv run --frozen --no-sync python -m pytest tests/workers/test_queue_observer.py tests/test_observability.py -q`

Expected: FAIL because the observer and queue gauges do not exist.

- [ ] **Step 3: Implement bounded gauges and observer lifecycle**

Create gauges:

```text
presvo.worker.queue.depth
presvo.worker.queue.oldest_due.age
```

Each carries only `{"queue_class": safe_queue_class}`. `sample()` reads the cardinality and one `(member, score)` tuple, ignores the member bytes, and computes `max(0.0, now() - score / 1000.0)`. `_run()` samples immediately, catches ordinary Redis exceptions with the fixed safe warning `worker queue observation failed queue_class=%s error_type=unknown`, then retries after the interval. It must re-raise `CancelledError`. `aclose()` cancels and awaits its owned task once without closing Redis.

- [ ] **Step 4: Run observer/observability tests and verify GREEN**

Run:

```bash
cd apps/api
uv run --frozen --no-sync python -m pytest tests/workers/test_queue_observer.py tests/test_observability.py -q
uv run --frozen --no-sync ruff check app/workers/queue_observer.py tests/workers/test_queue_observer.py
uv run --frozen --no-sync mypy app/workers/queue_observer.py
```

Expected: PASS with no payload access and idempotent shutdown.

- [ ] **Step 5: Commit queue observation**

```bash
git add apps/api/app/workers/queue_observer.py apps/api/app/core/observability.py apps/api/tests/workers/test_queue_observer.py apps/api/tests/test_observability.py
git commit -m "feat: observe worker queues without payload access"
```

### Task 4: Split ARQ registries and worker lifecycles

**Files:**
- Modify: `apps/api/app/workers/arq_worker.py`
- Rewrite focused assertions in: `apps/api/tests/workers/test_arq_worker.py`
- Modify: `apps/api/tests/workers/test_verification_expiry_job.py`

**Interfaces:**
- Produces: `CallLifecycleWorkerSettings` and `BackgroundWorkerSettings`; removes generic `WorkerSettings`.
- Produces: `on_call_lifecycle_startup(ctx)`, `on_background_startup(ctx)`, and shared idempotent `on_shutdown(ctx)`.
- Consumes: Task 1 queue constants, Task 2 policy-wrapped functions, Task 3 `QueueObserver`.

- [ ] **Step 1: Replace mixed-registry tests with exact allowlists**

```python
assert registered_names(CallLifecycleWorkerSettings.functions) == {
    "call_finalization_job",
    "call_reconciliation_job",
}
assert cron_names(CallLifecycleWorkerSettings.cron_jobs) == {
    "call_reconciliation_job"
}
assert registered_names(BackgroundWorkerSettings.functions) == {
    "outbox_delivery_job"
}
assert cron_names(BackgroundWorkerSettings.cron_jobs) == {
    "outbox_reconciliation_job",
    "verification_expiry_job",
}
assert not hasattr(arq_worker, "WorkerSettings")
```

Add literal assertions for queues, max jobs, 0.5-second polling, completion waits 60/30, health update interval 15, health keys `presvo:worker:call-lifecycle:health` and `presvo:worker:background:health`, and every registered function/cron's `timeout_s` and `max_tries`. Reload the module after setting each concurrency environment value and prove 10/4 defaults plus valid overrides are consumed by the correct class.

Assert explicitly that enqueued functions retain `keep_result_s is None` so
ARQ's current worker-level result retention remains in force, while cron jobs
retain their existing zero-result-retention behavior.

- [ ] **Step 2: Add failing startup/shutdown isolation tests**

Patch logging, validation, telemetry, handler creation, and observer construction. Prove both startup paths order logging → validation → telemetry → observer, only background startup constructs `outbox_handlers`, both set `ctx["arq_pool"] is ctx["redis"]`, and shutdown awaits observer close before telemetry shutdown. Invoke shutdown twice and assert each owned resource closes exactly once.

- [ ] **Step 3: Run worker settings tests and verify RED**

Run: `cd apps/api && uv run --frozen --no-sync python -m pytest tests/workers/test_arq_worker.py tests/workers/test_verification_expiry_job.py -q`

Expected: FAIL because only mixed `WorkerSettings` exists.

- [ ] **Step 4: Implement the two settings classes**

Register each callable with `arq.worker.func` using the policy's `arq_name`, `hard_timeout_seconds`, and `max_tries`. Register cron jobs once per minute with the same explicit timeout/tries. Configure these class values exactly:

```python
class CallLifecycleWorkerSettings:
    queue_name = CALL_LIFECYCLE_QUEUE_NAME
    max_jobs = get_settings().worker_lifecycle_max_jobs
    poll_delay = 0.5
    job_completion_wait = 60
    health_check_interval = 15
    health_check_key = "presvo:worker:call-lifecycle:health"


class BackgroundWorkerSettings:
    queue_name = BACKGROUND_QUEUE_NAME
    max_jobs = get_settings().worker_background_max_jobs
    poll_delay = 0.5
    job_completion_wait = 30
    health_check_interval = 15
    health_check_key = "presvo:worker:background:health"
```

Use telemetry service names `presvo-worker-call-lifecycle` and `presvo-worker-background`, adding both to the observability service allowlist. Startup must use ARQ's existing `ctx["redis"]`; it must not create or close a second Redis pool. Only background startup constructs the outbox handler registry.

- [ ] **Step 5: Run worker registry/lifecycle tests and verify GREEN**

Run:

```bash
cd apps/api
uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_arq_worker.py \
  tests/workers/test_verification_expiry_job.py \
  tests/workers/test_individual_jobs.py \
  tests/workers/test_call_finalization_worker.py
uv run --frozen --no-sync ruff check app/workers/arq_worker.py tests/workers/test_arq_worker.py
uv run --frozen --no-sync mypy app/workers/arq_worker.py
```

Expected: PASS with disjoint exact registries.

- [ ] **Step 6: Commit worker split**

```bash
git add apps/api/app/workers/arq_worker.py apps/api/app/core/observability.py apps/api/tests/workers
git commit -m "feat: split lifecycle and background workers"
```

### Task 5: Split Compose services and local E2E orchestration

**Files:**
- Modify: `compose.dev.yaml`
- Modify: `compose.yaml`
- Modify: `scripts/run-local-e2e.sh`
- Modify: `apps/api/tests/test_deployment_readiness.py`

**Interfaces:**
- Produces: `worker-lifecycle` command `app.workers.arq_worker.CallLifecycleWorkerSettings` and `worker-background` command `app.workers.arq_worker.BackgroundWorkerSettings`.
- Produces: ARQ `--check` healthchecks against the exact settings class.
- Removes: generic Compose service and script token `worker`.

- [ ] **Step 1: Write failing rendered-Compose assertions**

Use existing `load_local_compose_yaml()` and `load_compose_yaml()` helpers. For both documents, assert exactly these worker services exist, both share the same build/image and common environment, their commands and capacity variables are exact, stop grace is 75/45 seconds, and no `worker` service remains. Assert healthchecks contain:

```text
/app/.venv/bin/arq --check app.workers.arq_worker.CallLifecycleWorkerSettings
/app/.venv/bin/arq --check app.workers.arq_worker.BackgroundWorkerSettings
```

Assert the agent depends on healthy `worker-lifecycle` in both documents and
has no background-worker dependency. Update every existing string-slice test
to use rendered service dictionaries so adding a service cannot invalidate
parsing boundaries silently.

- [ ] **Step 2: Strengthen local-E2E script assertions**

Require build/start/log/restart sets to contain both workers. The failure log command must be `compose logs api worker-lifecycle worker-background web`; initial waits must use `wait_for_health` for each worker; the recovery phase must restart API plus both workers and wait for both worker healthchecks. Keep the voice `agent` absent from the disposable browser journey.

- [ ] **Step 3: Run deployment tests and verify RED**

Run: `cd apps/api && uv run --frozen --no-sync python -m pytest tests/test_deployment_readiness.py -q`

Expected: FAIL because Compose and the E2E runner still name one generic worker.

- [ ] **Step 4: Implement DRY Compose definitions with explicit per-service policy**

Use one YAML anchor per Compose file for common API-image worker build/image, environment, dependencies, volume/hardening, and restart configuration. Keep command, healthcheck, and `stop_grace_period` visible under each concrete service. Put both capacity environment values in the shared worker environment with defaults 10 and 4. Do not expose API-only Clerk identity settings to either worker.

Update `scripts/run-local-e2e.sh` to build, start, health-check, log, and restart both concrete names. Do not start the LiveKit agent or change its credential/provider configuration.

- [ ] **Step 5: Render both Compose files and run deployment tests**

Run:

```bash
cd apps/api
uv run --frozen --no-sync python -m pytest tests/test_deployment_readiness.py -q
cd ../..
docker compose -f compose.dev.yaml config --no-env-resolution >/dev/null
env \
  ACTIVATION_FLOW_ENABLED=true \
  AGENT_DISPATCH_JWT_SECRET=test-only-test-only-test-only-test-only \
  AGENT_IMAGE=presvo-agent:verification \
  API_BASE_URL=https://api.example.invalid \
  API_IMAGE=presvo-api:verification \
  CLERK_AUTHORIZED_PARTIES=https://app.example.invalid \
  CLERK_ISSUER=https://clerk.example.invalid \
  CLERK_SECRET_KEY=disposable \
  CLERK_WEBHOOK_SECRET=disposable \
  CORS_ALLOWED_ORIGINS=https://app.example.invalid \
  DATABASE_URL=postgresql+asyncpg://u:p@db.invalid/db \
  GEMINI_API_KEY=disposable \
  LIVEKIT_API_KEY=disposable \
  LIVEKIT_API_SECRET=disposable \
  LIVEKIT_URL=wss://livekit.example.invalid \
  NEXT_PUBLIC_API_BASE_URL=https://api.example.invalid \
  NEXT_PUBLIC_APP_URL=https://app.example.invalid \
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_disposable \
  REDIS_URL=redis://redis.invalid:6379/0 \
  S3_ACCESS_KEY=disposable \
  S3_ENDPOINT_URL=https://s3.example.invalid \
  S3_REGION=eu-west-3 \
  S3_SECRET_KEY=disposable \
  SPEECHMATICS_API_KEY=disposable \
  STORAGE_BUCKET_NAME=recordings \
  STRIPE_BILLING_PORTAL_CONFIGURATION_ID=bpc_disposable \
  STRIPE_BILLING_PORTAL_RETURN_URL=https://app.example.invalid \
  STRIPE_CHECKOUT_CANCEL_URL=https://app.example.invalid \
  STRIPE_CHECKOUT_SUCCESS_URL=https://app.example.invalid \
  STRIPE_PRICE_STARTER=price_disposable \
  STRIPE_SECRET_KEY=stripe-test-fixture \
  STRIPE_WEBHOOK_SECRET=whsec_disposable \
  SUMMARY_MODEL=gemini-2.5-flash \
  SUMMARY_PROVIDER=gemini \
  TELNYX_ACTIVE_CONNECTION_ID=disposable \
  TELNYX_API_KEY=disposable \
  TELNYX_DISABLED_CONNECTION_ID=disposable \
  TELNYX_ORDERING_ENABLED=true \
  WEB_IMAGE=presvo-web:verification \
  docker compose -f compose.yaml config >/dev/null
```

Expected: tests PASS and both renders exit 0. These commands render configuration only; they do not start or alter services.

- [ ] **Step 6: Commit process topology**

```bash
git add compose.dev.yaml compose.yaml scripts/run-local-e2e.sh apps/api/tests/test_deployment_readiness.py
git commit -m "ops: run isolated lifecycle and background workers"
```

### Task 6: Prove ten-call isolation with real ARQ and Redis

**Files:**
- Create: `apps/api/tests/integration/test_worker_queue_isolation.py`
- Verify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `TEST_REDIS_URL`, but only for loopback hosts; rewrites its database component to reserved test database 15.
- Consumes: literal production queue names and class concurrency values.
- Produces: controlled acceptance evidence that background saturation does not delay ten lifecycle jobs beyond p95 2 seconds.

- [ ] **Step 1: Write the real-Redis isolation test**

The test must skip when `TEST_REDIS_URL` is absent and reject non-loopback hosts. It must use database 15, `flushdb()` only there before and after the test, and close all pools/workers in `finally`. Build two real `arq.worker.Worker` instances with `handle_signals=False`: background queue/concurrency 4 and lifecycle queue/concurrency 10. The background probe increments a started counter and awaits a test-owned `asyncio.Event`; the lifecycle probe records `time.monotonic()` keyed by a synthetic sequence number.

Enqueue four background jobs to literal `arq:queue:background`, wait until all four slots are blocked, then independently record enqueue times for ten jobs sent to literal `arq:queue`. Assert all lifecycle results arrive before releasing the background event. Calculate nearest-rank p95 with:

```python
delays = sorted(started_at[index] - enqueued_at[index] for index in range(10))
p95 = delays[math.ceil(0.95 * len(delays)) - 1]
assert p95 <= 2.0
```

Also assert the two production settings classes expose literal queue names and default max-jobs 10/4 so lowering lifecycle capacity or sharing queues breaks this test.

- [ ] **Step 2: Run without Redis and verify the safe skip**

Run: `cd apps/api && env -u TEST_REDIS_URL uv run --frozen --no-sync python -m pytest tests/integration/test_worker_queue_isolation.py -q -rs`

Expected: one explicit skip stating that dedicated Redis is required; no connection or mutation occurs.

- [ ] **Step 3: Run against a separately named disposable Redis**

Use a free non-live host port and a unique test-only container name. Start Redis 7.4.7, run the test with `TEST_REDIS_URL` pointing to that loopback port, and stop only that named disposable container in a cleanup trap. Do not use the running Presvo Compose Redis service.

```bash
test_redis_container="presvo-worker-isolation-test-${PPID}"
test_redis_id=$(docker run --detach --rm \
  --name "$test_redis_container" \
  --publish 127.0.0.1:56380:6379 \
  redis:7.4.7-alpine)
trap 'docker stop "$test_redis_id" >/dev/null 2>&1 || true' EXIT HUP INT TERM
until docker exec "$test_redis_id" redis-cli ping | rg -q '^PONG$'; do
  sleep 1
done
cd apps/api
TEST_REDIS_URL=redis://127.0.0.1:56380/0 \
  uv run --frozen --no-sync python -m pytest \
  tests/integration/test_worker_queue_isolation.py -q
docker stop "$test_redis_id" >/dev/null
trap - EXIT HUP INT TERM
```

Expected: PASS; ten lifecycle jobs complete before background release and p95 is at most 2 seconds.

- [ ] **Step 4: Verify CI already supplies a job-owned Redis**

Assert the existing API CI job retains both its Redis 7.4.7 service and
`TEST_REDIS_URL: redis://127.0.0.1:6379/0`. The new test derives database 15,
while application tests continue using database 0. No workflow edit, deploy
job, or external service is necessary.

- [ ] **Step 5: Commit controlled-beta evidence**

```bash
git add apps/api/tests/integration/test_worker_queue_isolation.py
git commit -m "test: prove lifecycle queue isolation at beta target"
```

### Task 7: Document ownership, rollout, rollback, and bounded evidence

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture/backend-context.md`
- Modify: `docs/architecture/production-deployment.md`
- Modify: `docs/architecture/staging-smoke-runbook.md`
- Modify: `docs/runbooks/deploy.md`
- Modify: `docs/runbooks/rollback.md`
- Modify: `docs/runbooks/incident-response.md`
- Modify: `docs/PROJECT_STATUS.md`
- Modify: `docs/engineering/2026-07-30-agent-api-review-decisions.md`
- Modify: `apps/api/tests/test_deployment_readiness.py`

**Interfaces:**
- Documents: exact worker names, job ownership, health keys, queue metrics, ordered coexistence, durable recovery, and the limit of the ten-call proof.
- Preserves: no claim of production certification, cloud SLO, or realtime completion.

- [ ] **Step 1: Add failing durable-document contract assertions**

In `test_deployment_readiness.py`, require current architecture/runbooks to contain both service names, both health keys, both queue names, rollout order `background → lifecycle → API → legacy drain`, reverse rollback ordering, and the statement that PostgreSQL outbox/call state—not Redis—is authoritative. Require project status and the Issue 4 ledger row to say “implemented” and “controlled ten-call local/CI evidence,” while still retaining load/recovery drills as a production-readiness gate.

- [ ] **Step 2: Run the documentation contract and verify RED**

Run: `cd apps/api && uv run --frozen --no-sync python -m pytest tests/test_deployment_readiness.py -q`

Expected: FAIL because current documents still describe `presvo-worker` and a single worker process.

- [ ] **Step 3: Write the exact operational contract**

Add this ownership table to backend/deployment documentation:

| Service | Queue | Jobs | Health key | Default slots |
| --- | --- | --- | --- | ---: |
| `worker-lifecycle` | `arq:queue` | call finalization; call reconciliation | `presvo:worker:call-lifecycle:health` | 10 |
| `worker-background` | `arq:queue:background` | outbox delivery/reconciliation; verification expiry | `presvo:worker:background:health` | 4 |

Document the deploy order exactly:

1. Start `worker-background` from the new API image.
2. Start `worker-lifecycle` while the generic worker still consumes the default queue.
3. Roll out the new API so new wakeups route explicitly.
4. Verify both health keys, depth/oldest-due metrics, and both reconciliation jobs.
5. Wait for old API replicas to disappear and the legacy/default backlog to drain.
6. Drain and remove the generic worker.

Document rollback as previous API routing first, explicit queue drain second, generic worker restoration third, and new worker removal last. State that an orphaned outbox wakeup is recovered by durable outbox reconciliation within its schedule and an orphaned lifecycle attempt by call reconciliation after service restoration; neither path is a zero-delay guarantee.

In incident response, add separate checks for worker health, `presvo.worker.queue.depth{queue_class}`, and `presvo.worker.queue.oldest_due.age{queue_class}`. In project status and the decision ledger, mark 4A+4B implemented with a bounded ten-call controlled test, but keep representative cloud load, alert routing, recovery drills, and production SLO selection open under Issue 16A.

- [ ] **Step 4: Run documentation/deployment contracts and verify GREEN**

Run: `cd apps/api && uv run --frozen --no-sync python -m pytest tests/test_deployment_readiness.py -q`

Expected: PASS with no stale operational command naming only the generic worker in current runbooks or contributor guidance. Historical specifications/plans remain unchanged.

- [ ] **Step 5: Commit durable documentation**

```bash
git add README.md docs apps/api/tests/test_deployment_readiness.py
git commit -m "docs: operate isolated worker services"
```

### Task 8: Complete repository verification without touching live services

**Files:**
- Modify only if a verification failure exposes a defect in files already in scope.

**Interfaces:**
- Produces: evidence for API behavior, coverage, static analysis, Compose rendering, agent compatibility, and web regression safety.

- [ ] **Step 1: Prove no routing or topology bypass remains**

Run:

```bash
rg -n 'enqueue_job\("outbox_delivery_job"' apps/api/app
rg -n 'enqueue_job\("call_finalization_job"' apps/api/app
rg -n 'arq_worker\.WorkerSettings|[[:space:]]worker:' compose.dev.yaml compose.yaml scripts README.md docs apps/api/tests --glob '!docs/superpowers/**'
```

Expected: direct enqueue matches only the two approved queue modules; no current runtime/runbook/test references target generic `WorkerSettings` or a generic worker service. Review matches manually because prose can use the generic noun “worker” legitimately.

- [ ] **Step 2: Run API lint, type, full test, and coverage ratchet gates**

Run:

```bash
cd apps/api
uv lock --check
uv run --frozen --no-sync ruff check app tests
uv run --frozen --no-sync mypy app
uv run --frozen --no-sync python -m pytest -q --cov=app --cov-report=term-missing --cov-report=json:coverage.json
uv run --frozen --no-sync python ../../scripts/check_python_coverage.py check --report coverage.json --baseline coverage-baseline.json
```

Expected: every command exits 0; no line or branch coverage regression is accepted.

- [ ] **Step 3: Run neighboring application gates**

Run:

```bash
cd apps/agent
uv lock --check
uv run --frozen --no-sync ruff check agent tests
uv run --frozen --no-sync mypy agent
uv run --frozen --no-sync python -m pytest -q
cd ../web
npm run test:ci
```

Expected: all commands exit 0. Do not run credential-gated voice evaluations and do not start the live voice stack.

- [ ] **Step 4: Re-render Compose and inspect the complete diff**

Repeat Task 5's render-only commands, then run:

```bash
git diff --check main...HEAD
git status --short
git diff --stat main...HEAD
git diff --name-only main...HEAD
```

Expected: no whitespace errors; only Issue 4 worker-isolation code/tests/docs/configuration are changed; neither protected directory nor any actual `.env` file appears.

- [ ] **Step 5: Request final two-stage code review**

Use `superpowers:requesting-code-review` to review both the approved design and the complete branch diff. Resolve correctness findings with a new failing regression test before the fix, rerun the narrow gate, and repeat the affected full gate.

- [ ] **Step 6: Commit only review-driven corrections, if present**

```bash
git add apps/api compose.dev.yaml compose.yaml scripts README.md docs
git commit -m "fix: close worker isolation review gaps"
```

Skip this commit when review produces no code or documentation changes. Do not merge, push, deploy, or restart live services without a separate owner instruction.
