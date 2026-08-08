# Call-Lifecycle and Background Worker Isolation Design

**Date:** 2026-08-05

**Status:** Owner-approved written contract

## Context

Opevo currently runs every API background job through one ARQ worker class and
one Redis queue. `WorkerSettings` registers call finalization, call
reconciliation, outbox delivery, outbox reconciliation, and forwarding-
verification expiry together. The same worker process therefore combines short
database-backed call-lifecycle work with provider-facing work that can occupy a
slot for substantially longer.

The PostgreSQL call state and transactional outbox already provide durable
recovery. Queue delivery is a wakeup and execution mechanism rather than the
source of truth. Even so, one slow provider or a burst of provider work can
consume the shared worker's capacity and delay customer-visible call
finalization or lifecycle recovery. The single worker is also one scaling,
restart, health, and failure domain.

The owner selected review Issues **4A + 4B**, approved two independently
managed worker services, and selected an initial controlled-beta target of ten
simultaneous calls. The owner also chose the explicit lifecycle name
`CallLifecycleWorkerSettings` and retained `BackgroundWorkerSettings` for the
heterogeneous work that may safely lag without compromising durable call
correctness.

This change must deliver genuine queue and process isolation without starting
Issue 5's outbox-module split or Issue 6's process-wide composition-root work.

## Decisions

### 1. Define two worker classes and two services

The API image exposes two ARQ settings classes:

```text
CallLifecycleWorkerSettings  -> worker-lifecycle
BackgroundWorkerSettings     -> worker-background
```

`worker-lifecycle` owns only:

- `call_finalization_job` as an explicitly enqueued function; and
- `call_reconciliation_job` as both a registered function and a once-per-minute
  cron job.

`worker-background` owns only:

- `outbox_delivery_job` as an explicitly enqueued function;
- `outbox_reconciliation_job` as a once-per-minute cron job; and
- `verification_expiry_job` as a once-per-minute cron job.

The registries are disjoint. A worker must not register another class's jobs as
a fallback because that would make a routing mistake silently defeat capacity
isolation. Registry-completeness tests use exact literal allowlists.

Both services use the same API image or development build. They differ only in
their settings-class command, queue, concurrency, timeout/retry policy,
healthcheck, and termination grace. The LiveKit voice-agent service remains a
separate process and depends on healthy call-lifecycle processing, not on the
background worker.

### 2. Keep the existing Redis queue as the call-lifecycle queue

Queue routing uses two stable, non-configurable names:

```python
CALL_LIFECYCLE_QUEUE_NAME = "arq:queue"
BACKGROUND_QUEUE_NAME = "arq:queue:background"
```

The corresponding bounded metric values are `call_lifecycle` and `background`.

Keeping `arq:queue` for call lifecycle preserves already-enqueued finalization
and reconciliation work across the first rollout. The background name is new
and explicit. Queue names are code constants rather than environment settings;
different API and worker configuration must not be able to strand jobs by
silently choosing different names.

Every enqueue site routes explicitly:

- `CallFinalizationQueue.enqueue()` always passes
  `_queue_name=CALL_LIFECYCLE_QUEUE_NAME` and retains its deterministic job ID.
- One small background-enqueue module owns the call to
  `ArqRedis.enqueue_job(..., _queue_name=BACKGROUND_QUEUE_NAME)`.
- Outbox wakeup callers use that module while retaining their existing
  operation-specific best-effort logging and durable transaction semantics.
- A call-reconciliation result that wakes the outbox uses the same background
  interface.
- Cron jobs inherit the queue of the worker that owns their settings class.

The shared module is a routing seam, not a generic task framework. It does not
own provider policy, logging copy, database transactions, retries, or arbitrary
job registration.

### 3. Apply explicit class and job policies

The only capacity values exposed as validated environment settings are:

| Setting | Default | Allowed range | Consumer |
| --- | ---: | ---: | --- |
| `WORKER_LIFECYCLE_MAX_JOBS` | 10 | 1–100 | `CallLifecycleWorkerSettings` |
| `WORKER_BACKGROUND_MAX_JOBS` | 4 | 1–50 | `BackgroundWorkerSettings` |

These settings appear in the API settings model, worker Compose environments,
the safe example environment, and deployment-readiness tests. API and web
behavior do not branch on them. The defaults describe the selected controlled-
beta evidence target, not a production-scale claim.

The semantic job policies are code constants:

| Job | Semantic timeout | ARQ attempts | Reason |
| --- | ---: | ---: | --- |
| `call_finalization_job` | 30 seconds | 3 | Short, idempotent, customer-visible database work benefits from prompt retry. |
| `call_reconciliation_job` | 60 seconds | 1 | The next scheduled run is the retry and must not overlap a retry storm. |
| `outbox_delivery_job` | 300 seconds | 1 | PostgreSQL outbox state owns provider retry policy. |
| `outbox_reconciliation_job` | 300 seconds | 1 | The next scheduled run is the retry. |
| `verification_expiry_job` | 60 seconds | 1 | Idempotent scheduled database maintenance repeats every minute. |

The instrumented wrapper enforces each semantic timeout. ARQ receives a hard
timeout five seconds greater than the semantic timeout so the application can
record a timeout outcome before ARQ's final cancellation bound. The hard bound
remains a safety mechanism if instrumentation or cleanup itself stalls.

Both classes retain ARQ's current 0.5-second polling cadence. Result retention
follows the owner-approved 4B-M-A contract. ARQ 0.27 builds one effective
name-keyed function registry and then overwrites same-name function metadata
with cron metadata. Because direct and cron reconciliation intentionally share
the single `call_reconciliation_job` name, both registrations explicitly use
zero result retention. No current caller consumes `Job.result()`;
reconciliation outcomes remain available through safe logs and metrics and
durable PostgreSQL state. Direct call finalization and outbox delivery retain
worker-level/default result retention, while all cron jobs retain zero result
retention. Tests assert both source lists and a constructed real ARQ `Worker`
so the effective name-keyed contract cannot diverge silently.

The lifecycle worker accepts up to ten concurrent jobs and waits up to 60
seconds for active work during shutdown. Its Compose service has a 75-second
stop grace. The background worker accepts up to four concurrent jobs, waits up
to 30 seconds during shutdown, and has a 45-second stop grace.

Each class has a distinct ARQ health key and a 15-second health update interval:

```text
opevo:worker:call-lifecycle:health
opevo:worker:background:health
```

Compose healthchecks invoke ARQ's supported `--check` command against the exact
settings class. One worker's heartbeat cannot satisfy the other worker's
healthcheck.

### 4. Preserve durable failure and cancellation semantics

Call finalization remains idempotent. Its retry adapter retries only the
following failures:

- the 30-second semantic timeout;
- SQLAlchemy pool-acquisition timeout;
- SQLAlchemy operational or disconnection errors; and
- SQLAlchemy database errors explicitly marked `connection_invalidated`.

The adapter explicitly does not retry integrity errors, validation failures,
malformed payloads, programming defects, unclassified exceptions, or shutdown
cancellation. Raw exception text is not used to classify a failure. After the
first retryable failure ARQ delays the second attempt by one second; after the
second it delays the third attempt by five seconds. A retryable failure on the
third attempt is allowed to fail normally. The existing call-reconciliation
state machine then remains the durable recovery path; the queue layer does not
add a second lifecycle state machine.

The semantic timeout and instrumentation wrapper run inside the retry adapter.
This ordering lets observability record the real attempt outcome as `timeout`
or `error` before the adapter converts an eligible failure into ARQ's `Retry`
signal. The adapter obtains the one-based attempt number from ARQ's job context,
bounds it before metric use, and never converts `CancelledError` into a retry.
ARQ's function registration still declares `max_tries=3` as the hard attempt
ceiling.

Outbox delivery continues catching and durably classifying provider failures
inside the outbox job. ARQ does not multiply that retry schedule. A database or
process failure outside the classified provider path can end the one ARQ
attempt; the outbox processing lease and the next reconciliation run recover
the event.

Semantic timeout raises a distinct timeout outcome. Shutdown cancellation
raises a cancelled outcome. Database context managers must roll back unfinished
transactions in both cases. Neither outcome is converted into a provider
failure or logged with raw exception text, job payloads, phone numbers, tokens,
provider responses, or credentials.

Background wakeup enqueue failure remains best effort after the durable state
commit. Call-finalization enqueue failure retains the existing customer-visible
unavailable result because prompt finalization acceptance requires a queued
job. The queue split does not weaken either contract.

### 5. Observe queue state without reading job payloads

Each worker starts one queue observer after logging, runtime validation, and
telemetry initialization. The observer uses the ARQ-owned Redis connection and
samples its own queue every 15 seconds. It reads only sorted-set cardinality and
the oldest score; it never loads or deserializes a job body.

The queue score represents scheduled eligibility. The age metric is therefore
the non-negative age of the oldest **due** job, not the age of future deferred
work.

The metrics are:

```text
opevo.worker.queue.depth{queue_class}
opevo.worker.queue.oldest_due.age{queue_class}
opevo.worker.queue.delay{queue_class, job, attempt}
opevo.worker.job.duration{queue_class, job, outcome, attempt}
```

Allowed queue classes, job names, outcomes, and bounded attempt values are
validated before recording. Outcomes are exactly `success`, `error`, `timeout`,
and `cancelled`. The existing duration histogram's count records executed
attempts; a second redundant attempt counter is not added.

A transient Redis observation failure emits one fixed-shape safe warning and
does not stop the worker or alter job results. The next interval retries.
Shutdown cancels and awaits the observer exactly once before telemetry closes;
the observer does not close the ARQ-owned Redis pool.

The worker's health key is the authoritative process-liveness signal. Missing
queue samples while that healthcheck is unhealthy are not interpreted as an
empty queue.

### 6. Prove the controlled-beta isolation target

A Docker-backed integration test uses a disposable Redis database and real ARQ
worker machinery. It:

1. starts one worker with the production lifecycle queue name and concurrency;
2. starts one worker with the production background queue name and concurrency;
3. fills all four background slots with synthetic work blocked on a test-owned
   event;
4. enqueues ten short lifecycle jobs;
5. requires all ten lifecycle jobs to finish before the background event is
   released; and
6. calculates queue delay from independently captured enqueue/start times and
   requires lifecycle p95 to be at most two seconds in the controlled Docker
   environment.

The test uses literal expected queue names and counts rather than production
helpers to calculate its expectations. It fails if both classes share a queue,
if the lifecycle worker has fewer than ten slots, if a caller omits explicit
routing, or if background saturation consumes lifecycle capacity.

This is a bounded acceptance test for ten simultaneous calls. It is not a
claim about cloud scheduling, provider latency, database saturation, or larger
traffic. Issue 16A remains responsible for production SLO selection and later
capacity calibration using representative infrastructure.

## Data flow

```text
agent completion
      |
      v
CallFinalizationQueue -- explicit arq:queue --> worker-lifecycle
                                                  |
                                                  v
                                      durable call finalization
                                                  |
                                                  v
                                      PostgreSQL outbox rows
                                                  |
durable mutations                                  |
      |                                            |
      +--> background wakeup -- arq:queue:background
                                                   |
                                                   v
                                         worker-background
                                                   |
                                                   v
                                     bounded provider delivery
```

PostgreSQL remains authoritative. Redis queues wake or schedule work; they do
not replace call state, outbox state, recording-operation state, or account-
deactivation state.

## Compose and deployment topology

Development and production Compose define `worker-lifecycle` and
`worker-background`. Shared image/build, environment, dependency, volume,
hardening, and restart configuration uses YAML anchors where that removes real
duplication. Commands, healthchecks, queue-specific limits, and stop grace
remain explicit at each service.

The agent depends on a healthy `worker-lifecycle`. It does not depend on the
background worker because accepting and conducting an already-admitted call
must not depend on slow provider maintenance. API readiness continues proving
Redis connectivity; worker health is independently checked at deployment.

The initial ordered rollout is:

1. deploy `worker-background` from the new API image;
2. deploy `worker-lifecycle` while the old generic worker remains available;
3. deploy the new API so new wakeups route explicitly;
4. verify both new health keys, queue depth/age, call reconciliation, and outbox
   reconciliation;
5. wait until no old API replica can enqueue generic work and the legacy/default
   backlog is empty; and
6. drain and remove the old generic worker.

During coexistence, the old worker and `worker-lifecycle` can both consume the
default queue. A legacy outbox wakeup consumed by the lifecycle worker can be
rejected as an unknown function; this does not lose its PostgreSQL outbox row.
The new background reconciliation run discovers it within one minute. The new
lifecycle worker must not register background functions merely to suppress this
bounded migration signal.

Rollback first restores the previous API routing while both new workers remain
available, waits for the explicit queues to drain, restores the previous generic
worker, and removes the new workers last. If emergency rollback cannot wait for
an orphaned background wakeup, the old worker's outbox reconciliation discovers
the durable row. If it cannot wait for an orphaned lifecycle attempt, existing
call reconciliation recovers the durable call state. Runbooks describe these as
delayed recovery paths, not zero-delay guarantees.

Implementation and repository verification do not deploy, recreate, or alter
the user's live local containers. Live cutover requires a separate explicit
authorization after merge.

## Error and edge-case behavior

- Invalid concurrency settings below or above their bounds fail settings
  construction before a worker starts.
- API and worker code cannot configure different queue names.
- A missing background worker cannot invalidate an already-committed durable
  mutation; queue depth/age and health expose the outage.
- A missing lifecycle worker leaves finalization jobs queued and makes its
  healthcheck fail; call reconciliation remains the eventual durable recovery
  path after service restoration.
- Duplicate wakeups remain safe because outbox claims are atomic and handlers
  remain idempotent.
- Simultaneous lifecycle workers compete through existing database locks and
  generation checks.
- Call-finalization semantic timeouts and recognized transient SQLAlchemy
  connection failures retry after one and five seconds, with no fourth attempt.
- Call-finalization integrity, validation, payload, programming, and
  unclassified failures do not retry.
- A shutdown-cancelled call-finalization attempt remains cancelled and does not
  consume a retry delay.
- A cancelled provider call does not become a retryable provider failure.
- An observer failure cannot cancel a business job or close the shared Redis
  pool.
- Naive enqueue timestamps remain normalized to UTC by existing
  instrumentation behavior.
- Unknown job, queue, outcome, or attempt labels collapse to bounded safe values
  and never copy untrusted text.
- Scheduled future retries contribute to queue depth but have zero due age
  until their score becomes eligible.

## Testing strategy

Implementation follows strict red-green-refactor. Every production behavior is
introduced by a focused failing test that names the break it catches.

1. Add literal registry tests that fail because only `WorkerSettings` exists
   and its functions are mixed.
2. Add routing tests that fail because finalization and outbox wakeups omit
   `_queue_name`.
3. Add settings-boundary tests for exact defaults and invalid minimum/maximum
   values.
4. Add job-policy tests that execute semantic timeout, retry, and cancellation
   behavior rather than grepping source constants.
5. Add queue-observer tests for depth, due age, future scores, Redis failure,
   recovery, and exact-once shutdown.
6. Extend observability tests for literal bounded attributes and sentinel
   rejection.
7. Add the real-Redis isolation test for four blocked background jobs and ten
   lifecycle jobs, including the two-second p95 gate.
8. Add Compose render tests for exact services, image equality, commands,
   healthchecks, stop periods, dependencies, and absence of the generic worker.
9. Add rollout/recovery characterization proving durable outbox and call state
   do not depend on a wakeup remaining on a particular queue.
10. Update local E2E orchestration and test it by running the existing script
    with snapshot updates disabled.
11. Run focused tests after every red-green cycle, then complete API tests,
    coverage ratchets, Ruff, mypy, Compose renders, container-image import
    checks, and affected agent/web gates.

Mutation checks must demonstrate that sharing queue names, swapping a routing
constant, registering a background job on the lifecycle worker, omitting a
health key, removing the timeout distinction, or reducing lifecycle concurrency
below ten fails an intended test.

## Documentation changes

Update the following durable documents in the same implementation:

- `docs/architecture/backend-context.md` for queue ownership and durability;
- `docs/architecture/production-deployment.md` for two worker services;
- `docs/runbooks/deploy.md` for ordered coexistence and drain;
- `docs/runbooks/rollback.md` for reverse ordering and recovery;
- `docs/runbooks/incident-response.md` for per-class health/depth/due-age;
- `docs/PROJECT_STATUS.md` for implemented worker isolation and the bounded
  controlled-beta evidence;
- `docs/engineering/2026-07-30-agent-api-review-decisions.md` to record 4A+4B
  implementation evidence without overstating production scale; and
- safe environment and contributor guidance affected by the two commands.

Historical plans and specifications are not rewritten to pretend they always
described the split topology.

## Performance and maintenance

The selected topology adds one long-running process and Redis connection pool.
It trades that small baseline resource cost for independent concurrency,
health, restart, deployment, and scaling domains. Both services reuse one API
artifact, so there is no second build or dependency family.

The queue observer performs two bounded Redis sorted-set reads every 15 seconds
per worker. It does not scan queues or deserialize jobs. Metric cardinality is
bounded by two queue classes, five job names, four outcomes, and small attempt
values.

`BackgroundWorkerSettings` is intentionally heterogeneous. Its explicit
registry prevents it from becoming an implicit dumping ground. Issue 5 remains
the planned split of the large outbox handler module and its provider-facing
topic families. This design neither hides nor duplicates that work.

## Non-goals

- Splitting `outbox_topics.py` or implementing Issue 5A.
- Introducing process-wide composition roots or implementing Issue 6A.
- Upgrading LiveKit or implementing Issue 8A.
- Transcript-query or memory optimization from Issue 15A.
- Production-scale SLO and capacity certification from Issue 16A.
- Enabling realtime or implementing Issues 1A/14A.
- Adding a DI framework, service locator, process supervisor, generic workflow
  engine, priority queue, dead-letter product, or Redis migration script.
- Changing provider retry classifications, database schema, durable state
  machines, billing, telephony, recording, activation, authentication, or
  customer UI behavior.
- Deploying, pushing, opening a PR, or recreating the live local stack without
  separate authorization.

## Acceptance criteria

- The two settings classes and Compose services have disjoint, exact job
  ownership.
- Every production enqueue supplies its stable queue explicitly through the
  approved seam.
- Concurrency, semantic timeouts, retry ceilings, graceful completion, stop
  grace, health keys, and health intervals match this contract.
- Queue depth, oldest-due age, queue delay, duration, attempt, and bounded
  outcome data are observable without reading payloads.
- Background saturation cannot delay the ten-job lifecycle acceptance load
  case beyond the two-second p95 queue-delay gate.
- Timeout, cancellation, Redis observation failure, duplicate wakeup, rolling
  coexistence, and rollback recovery paths have automated evidence.
- Development and production Compose render with two workers using the same API
  artifact, and the existing local E2E runner starts both.
- Complete quality gates pass without weakening coverage, skips, dependency
  policy, or security checks.
- Documentation records bounded controlled-beta evidence and does not claim a
  deployment or production-scale certification.
