# Task 6 report — typed worker composition roots

## Status

Complete against base `be7de3a3818a61bef5b46086e01c74b431f25476`.

The call-lifecycle and background ARQ processes now have distinct typed
runtimes, process-specific validation and construction, one application-owned
ARQ context key, deterministic process-resource ownership, and explicit job
instrumentation lookup. Queue topology and policy metadata are unchanged.

## RED evidence

The Task 6 runtime, validation, construction, context, and ownership tests were
written before production changes. The prescribed RED command was run from
`apps/api`:

```text
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/composition/test_worker_composition.py \
  tests/workers/test_arq_worker.py \
  tests/test_deployment_readiness.py
```

It failed during collection for the intended missing contract:

```text
ImportError: cannot import name 'WORKER_RUNTIME_KEY' from
'app.composition.runtime'
1 error in 0.79s
```

During refactor review, the construction-failure matrix was expanded from the
background root to both roots. A deliberate mutation removed lifecycle partial
cleanup before the new test ran:

```text
python -m pytest -q tests/composition/test_worker_composition.py \
  -k 'lifecycle and session_factory and construction_failure'
```

The mutation produced `1 failed, 47 deselected`: the engine and observability
close counts remained zero. The mutation was restored with `apply_patch`; the
complete composition file then passed, and the final matrix includes all
meaningful factory stages in both roots.

## Implementation

### Typed runtime boundary

- Added `WORKER_RUNTIME_KEY = "application_runtime"`.
- Added distinct, non-inheriting `CallLifecycleWorkerRuntime` and
  `BackgroundWorkerRuntime` dataclasses with exactly the fields locked by the
  plan. Background alone exposes the temporary two-argument outbox handler
  mapping required before Task 8.
- Added `WorkerRuntimeConfigurationError` and separate lifecycle/background
  accessors. Missing, arbitrary, and cross-process runtime values fail with the
  process name.
- Added an explicit observability accessor that accepts either concrete
  runtime through two explicit branches; no runtime union/base class or common
  service locator was introduced.
- Kept `composition/runtime.py` construction-free: provider, database,
  observer, and handler types are imported only under `TYPE_CHECKING`; the
  module does not import either composition builder.

### Process-specific validation

- Replaced the broad `validate_worker_runtime` with independent
  `validate_call_lifecycle_worker_runtime` and
  `validate_background_worker_runtime` functions. Neither calls the other.
- Lifecycle validation owns only `DATABASE_URL` and `REDIS_URL`; Pydantic
  continues to validate reconciliation and worker numeric policy fields.
- Background validation owns database/Redis, selected Stripe and Telnyx
  credentials, production mode restrictions, dispatch signing, LiveKit,
  storage, and Gemini summary configuration.
- Runnable non-test background modes require dispatch, LiveKit, storage, and
  the selected summary provider. Explicit `test` mode skips external-provider
  requirements. Named fake billing/telephony modes skip their provider
  credentials outside production; production rejects those fake modes.
- Errors list setting keys only. Tests prove unsafe mode/provider values and
  credential sentinels are never included in messages.

### Construction and ownership

- Added `composition/workers.py` with one builder per process. Each validates
  before the exit stack or any injected factory is invoked.
- Both roots construct observability, a database engine/session factory, and a
  queue observer. The background root additionally captures the existing
  Task 5/default handler registry without moving provider construction ahead
  of Task 8.
- Each owned resource is registered immediately. Normal, repeated, and
  concurrent shutdown closes observer, engine, then observability exactly once.
- ARQ Redis is borrowed, retained as `runtime.arq_pool`, and never registered
  for cleanup.
- Every meaningful failure window is tested: observability, engine, session
  factory, background handler registry, observer factory, and observer start.
  All previously opened resources unwind in reverse order.
- Partial cleanup waits through outer cancellation using the reviewed
  completion-signal pattern. Construction cancellation has precedence;
  cleanup cancellation remains cancellation; ordinary cleanup errors are
  safely reported without replacing the primary construction error.
- Runtime shutdown delegates to the reviewed `RuntimeCleanup`, preserving its
  retained close task, shielding, join, cancellation, and error semantics.

### ARQ context and executable boundary

- `_WORKER_SETTINGS = get_settings()` is the only worker settings call. Both
  ARQ settings classes derive Redis and concurrency metadata from that same
  captured object.
- Reload tests inject a complete `Settings(_env_file=None, app_env="test", ...)`
  object, count exactly one call, and restore the module under controlled test
  environment values so no real dotenv file is inspected.
- Startup reads only `ctx["redis"]`, constructs the correct runtime, and writes
  only `ctx[WORKER_RUNTIME_KEY]`. It preserves ARQ-owned `redis`, `job_try`, and
  `enqueue_time` entries.
- Shutdown pops the one runtime key, rejects an invalid concrete type, closes a
  valid runtime, and is idempotent when invoked again. It never closes Redis.

### Instrumentation and Task 7 sequencing bridge

- `instrument_job` and `apply_job_policy` now require an
  `observability_getter`; all five registered policies pass
  `require_worker_observability` explicitly.
- Queue-delay metadata still reads only ARQ-owned `enqueue_time` and `job_try`;
  tracing, outcomes, timeout classification, and retry conversion are
  unchanged.
- Removing `arq_pool` and `observability` context aliases would otherwise have
  silently disabled call-reconciliation outbox wakeups and telemetry before
  Task 7. As the minimal sequencing bridge, `call_reconciliation_job` now
  validates the lifecycle runtime and reads only those two dependencies from
  it. Its settings, session, clock, service, and use-case extraction remain
  deferred to Task 7.
- The direct wakeup regression passes a context with no `arq_pool` or
  `observability` alias and proves the exact background queue wakeup remains.
- Other jobs and handlers retain their Task 5/global outer-boundary bridges;
  no Task 7/8 dependency migration or compatibility context alias was added.

## Exact configuration, context, and registry proofs

- Runtime shape/accessor/configuration/resource tests: final composition file
  contains 49 passing tests.
- Lifecycle accepts a production settings object with Clerk, Stripe, Telnyx,
  LiveKit, S3, and summary values absent.
- Background rejects each missing common key individually and accepts selected
  Stripe with only `STRIPE_SECRET_KEY`, selected Telnyx with only its API key
  and active/disabled connection IDs, and named fake modes without either
  provider's credentials.
- Validation-before-construction tests use forbidden engine, session,
  observability, observer, and handler factories and observe zero calls.
- Startup-context tests compare the whole dictionary: lifecycle retains
  `redis`, `job_try`, and `enqueue_time`; background retains `redis`; each adds
  only `application_runtime`.
- Source sweep of `arq_worker.py` finds only two `ctx["redis"]` reads, two
  runtime-key writes, and one runtime-key pop. It finds exactly one
  `get_settings()` call.
- Literal registry/policy tests preserve:
  - lifecycle queue `arq:queue`, functions `call_finalization_job` and
    `call_reconciliation_job`, and the reconciliation cron;
  - background queue `arq:queue:background`, function `outbox_delivery_job`,
    and reconciliation/verification-expiry crons;
  - max jobs 10/4, polling 0.5, completion waits 60/30, health interval 15,
    and the existing health keys;
  - hard timeouts 35/65/305/305/65, max tries 3/1/1/1/1, minute schedules,
    retry delays, and zero cron/reconciliation result retention.

## Verification

The first combined sandboxed run reached `214 passed` before the known
restricted-sandbox subprocess/thread wakeup timeout. The same focused gate was
rerun outside the sandbox and passed `215 passed in 17.40s`.

Final prescribed Task 6 gate, outside the sandbox:

```text
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/composition/test_worker_composition.py \
  tests/workers/test_arq_worker.py \
  tests/workers/test_worker_observability_lifecycle.py \
  tests/workers/test_job_policy.py \
  tests/integration/test_worker_queue_isolation.py \
  tests/test_deployment_readiness.py
```

Result: `215 passed, 2 skipped in 16.65s`.

Final full stable-tree API suite, outside the sandbox:

```text
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
```

Result: `2868 passed, 133 skipped, 1 warning in 254.99s`. The warning is the
pre-existing Starlette/httpx deprecation warning.

Final static gates:

```text
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
# All checks passed!

UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
# Success: no issues found in 190 source files
```

`git diff --check be7de3a3818a61bef5b46086e01c74b431f25476` exits zero.
The changed-path scan contains no lockfile, dependency, environment, compose,
deployment, migration, schema, frontend, or other protected path.

## Changed files

Production:

- `apps/api/app/composition/runtime.py`
- `apps/api/app/composition/workers.py` (new)
- `apps/api/app/core/observability.py`
- `apps/api/app/core/runtime_validation.py`
- `apps/api/app/workers/arq_worker.py`
- `apps/api/app/workers/job_policy.py`
- `apps/api/app/workers/jobs/call_reconciliation.py` (minimal approved bridge)

Tests:

- `apps/api/tests/composition/test_worker_composition.py` (new)
- `apps/api/tests/workers/test_arq_worker.py`
- `apps/api/tests/workers/test_worker_observability_lifecycle.py`
- `apps/api/tests/workers/test_job_policy.py`
- `apps/api/tests/workers/test_call_reconciliation_wakeup.py`
- `apps/api/tests/integration/test_worker_queue_isolation.py`
- `apps/api/tests/test_deployment_readiness.py`
- `apps/api/tests/test_observability.py`
- this report

## Self-review against BASE

- Reviewed the complete BASE diff, not only the new composition module.
- Confirmed exact runtime fields and no common worker runtime/container type.
- Confirmed business/provider modules do not import the builder module and the
  construction-free runtime module has no runtime dependency cycle.
- Confirmed process validators are separate and the removed broad validator has
  zero remaining references.
- Confirmed no application dependency alias is written to ARQ context.
- Confirmed Redis is borrowed on normal shutdown and every partial failure.
- Confirmed every constructed owned resource closes once in reverse order and
  a cleanup error/cancellation cannot skip later resources.
- Confirmed settings capture, queue registries, cron metadata, semantic/hard
  timeouts, retries, retention, concurrency, health, and shutdown values are
  unchanged.
- Confirmed Task 5 provider ownership and handler construction remain intact;
  only the wakeup/telemetry-preservation bridge was pulled forward.
- Confirmed no dependency, lockfile, environment, compose, deployment,
  migration, schema, external provider, or frontend path changed.

## Concerns and deferred scope

No open Task 6 concern.

Tasks 7 and 8 still own lifecycle job/use-case extraction and the atomic bound
outbox/provider migration. Until those tasks, the documented Task 5
handler-side construction and legacy session/settings bridges remain by design.

## Fix Round 1/5

### Findings addressed

- Added `LIVEKIT_AGENT_NAME` to background-worker fail-fast validation for
  every runnable non-test environment. Missing (`None`), empty, and
  whitespace-only values are rejected in development, staging, and production.
  The exact error contains only the setting key.
- Added a direct builder regression using forbidden observability, engine,
  session, handler, and observer factories. An invalid agent name raises before
  any factory is called.
- Added worker-composition partial-startup tests for construction-origin
  cancellation, outer cancellation while an engine close is blocked, and
  cleanup-origin cancellation. Each test proves all prior resources close once
  in reverse order, the outer builder does not complete before cleanup, no
  cleanup task is orphaned, and cancellation is not logged as an ordinary
  failure.
- Corrected partial-startup cleanup to run the unpublished `AsyncExitStack`
  directly in the retained cleanup task. Successful runtimes still use the
  reviewed `RuntimeCleanup`. Avoiding the extra shield only for unpublished
  partial runtimes preserves the original cleanup-origin `CancelledError`
  identity and arguments.

### RED evidence

The new tests were written before either production fix. The initial focused
composition run produced:

```text
11 failed, 50 passed in 3.88s
```

Nine failures showed that `None`, empty, and whitespace-only LiveKit agent
names were accepted across development, staging, and production. The direct
factory-order test reached the forbidden observability factory. The cleanup
cancellation test received a new empty `CancelledError` instead of the closer's
`CancelledError("cleanup-origin")` instance.

Both repaired behaviors received an independent mutation check:

- Removing `livekit_agent_name` from the required-key tuple produced
  `10 failed, 51 deselected`; all nine key matrix cases and the
  validation-before-factories test failed.
- Reintroducing `RuntimeCleanup(stack).aclose()` inside partial cleanup produced
  `1 failed, 60 deselected`; cleanup-origin cancellation identity was lost.

Both mutations were restored with `apply_patch` before final verification.

### Verification

Focused worker composition and deployment-validation gate, run outside the
restricted sandbox:

```text
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/composition/test_worker_composition.py \
  tests/test_deployment_readiness.py
```

Result: `181 passed in 13.45s`.

Focused static gates:

```text
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check \
  app/composition/workers.py app/core/runtime_validation.py \
  tests/composition/test_worker_composition.py \
  tests/test_deployment_readiness.py
# All checks passed!

UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy \
  app/composition/workers.py app/core/runtime_validation.py
# Success: no issues found in 2 source files
```

### Fix-round self-review and open conflict

- Only the worker validator, unpublished partial-cleanup path, direct
  composition tests, and this report changed.
- Production-development validation remains strict; no requirement was
  weakened.
- `compose.dev.yaml` and `CONTRIBUTING.md` were not modified.
- The reviewer-noted Compose/development-policy conflict remains explicitly
  open pending owner direction and is not resolved or masked by this fix.

## Fix Round 2/5 — 6A-C1A

### Owner decision implemented

- Added one reusable `x-api-env-files` YAML sequence to `compose.dev.yaml`.
  The API and the existing shared worker declaration reference the same optional
  `./apps/api/.env`; worker lifecycle and background services therefore receive
  the source without duplicating it.
- Kept every explicit worker environment value unchanged. Compose precedence
  continues to make those values override values from the shared env file.
- Did not add provider credentials, placeholder provider values, or dummy
  provider settings to the checked-in Compose model. No real `.env` file was
  opened or parsed during implementation or verification.
- Updated local-development guidance to explain that the core background worker
  needs API-side dispatch, LiveKit agent, and selected summary-provider
  configuration and intentionally fails fast when it is incomplete. The voice
  agent remains excluded unless `--profile voice` is selected.

### RED evidence

The deployment-readiness assertions were changed before the Compose file. The
focused run produced:

```text
1 failed, 9 passed in 1.30s
```

The failure was the intended missing `env_file` key on both checked-in worker
services. The controlled explicit background-provider validation matrix already
passed, confirming that strict runtime validation did not need to change.

### Readiness coverage

- The Compose render helper now combines `--env-file os.devnull` with
  `--no-env-resolution` for source-only inspection. Its fixture proves that this
  mode reads neither a project `.env` nor a service `env_file`.
- The checked-in development model proves the API, lifecycle worker, and
  background worker resolve to the same optional API env-file path.
- The background worker's explicit Compose environment is checked for the
  absence of dispatch, LiveKit, agent-name, and selected Gemini summary
  placeholders.
- A controlled `_env_file=None` settings matrix proves every complete explicit
  provider configuration is accepted and each missing required provider value
  is rejected by name.

### Verification

Targeted RED-to-GREEN rerun:

```text
10 passed in 0.99s
```

Focused deployment and worker-composition suite, run outside the restricted
sandbox because an existing lifespan test launches a subprocess:

```text
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/test_deployment_readiness.py \
  tests/composition/test_worker_composition.py
# 189 passed in 14.27s
```

The same focused suite first reached `188 passed` in the restricted sandbox;
its only failure was the existing subprocess test timing out at the sandbox's
60-second boundary. The unsandboxed rerun above passed completely.

Compose syntax/schema validation explicitly bypassed project and service env
files:

```text
docker compose --env-file /dev/null -f compose.dev.yaml \
  config --quiet --no-env-resolution
# exit 0
```

Static gates:

```text
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
# All checks passed!

UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
# Success: no issues found in 190 source files
```

### Fix-round self-review and concerns

- Strict background validation, runtime composition, queues, cron metadata,
  concurrency, shutdown, and health behavior are unchanged.
- The Compose anchor is a small shared data declaration; it does not hide worker
  commands, process policy, or explicit environment overrides.
- The readiness tests inspect normalized Compose structure without reading the
  developer's real API env file.
- No open concern remains for owner decision 6A-C1A.
