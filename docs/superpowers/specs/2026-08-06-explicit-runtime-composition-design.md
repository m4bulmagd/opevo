# Explicit Runtime Composition Design

**Date:** 2026-08-06

**Status:** Owner-approved written contract

## Context

Presvo already runs its API, call-lifecycle worker, background worker, and
LiveKit agent as separate processes. The process topology is sound, but
dependency construction is distributed across framework entrypoints, request
providers, ARQ job dictionaries, business functions, and module-level caches.

The API lifespan partially owns authentication, readiness, ARQ, realtime,
observability, and webhook resources. Database, Redis, storage, and some
provider construction remain behind cached global factories. Several request
paths construct provider-facing services themselves.

The ARQ workers have the largest hidden dependency surface. Startup writes
multiple application dependencies into ARQ's untyped `ctx` dictionary, while
jobs and outbox handlers use `ctx.get(...)` and silently fall back to global
session factories, settings, observability, clocks, or newly constructed
providers. A missing startup dependency can therefore produce different
behavior instead of failing as a composition error.

The agent repeats settings lookup through pipeline construction and creates an
event publisher per customer call. The publisher lazily creates a Redis client
without owning a corresponding close path. Tests compensate for these hidden
dependencies by clearing global caches and deeply monkeypatching modules.

Review Issue **6A** addresses process-wide composition and resource ownership.
The owner approved the following decisions:

- **6A-1A:** design API, workers, and agent together, then implement them in
  independently verified phases;
- **6A-2A:** preserve separate worker processes while keeping worker source
  colocated with the API;
- **6A-3A:** make a clean cutover rather than retaining compatibility
  fallbacks;
- **6A-4A:** give every resource an explicit process, operation, or call
  lifetime;
- **6A-5A:** use small typed runtimes and explicit service parameters, not a
  dependency container or DI framework;
- **6A-6A:** fail fast for missing required dependencies and model supported
  fake or disabled modes explicitly; and
- **6A-7A:** require a full composition, lifecycle, failure-path, integration,
  and architecture test matrix.

There is no deployed environment or important persisted database state that
requires a compatibility period. This freedom is used to remove hidden
construction paths, not to change customer-facing behavior.

## Goals

1. Give each executable process one obvious composition root.
2. Read and validate configuration once at each executable boundary.
3. Give every long-lived resource one documented owner and deterministic
   cleanup.
4. Make required dependencies visible in types and function signatures.
5. Preserve request, transaction, queue, outbox, call, and provider semantics.
6. Make services and jobs constructible in tests without environment mutation,
   network access, or deep global patching.
7. Fail at startup for invalid composition instead of failing during customer
   work or silently selecting a different provider.
8. Make a later physical worker-app extraction straightforward if operational
   evidence justifies it.

## Non-goals

- No move from `apps/api/app/workers` to a new `apps/worker` source package.
- No new shared-domain package solely to support a future worker extraction.
- No queue topology, concurrency, retry, timeout, healthcheck, or outbox
  contract change.
- No API, database schema, stored-data, or migration change.
- No authentication, activation, telephony, billing, recording, or call-flow
  behavior change.
- No realtime implementation; the previously deferred realtime work remains
  deferred.
- No deployment or external-provider mutation.
- No generic task framework, service locator, reflection-based autowiring, or
  dependency-injection framework.
- No abstraction of lightweight repository or use-case construction merely so
  every object originates in a process root.

## 1. Process and source boundaries

Operational isolation and source-package isolation are separate decisions.
Presvo retains the existing operational boundaries:

```text
API process
Call-lifecycle ARQ worker process
Background ARQ worker process
LiveKit agent process/job process
```

The two ARQ workers continue using the API source package and shared domain
models, repositories, migrations, provider contracts, and outbox semantics.
They must not import FastAPI routers or API application startup. Boundary tests
enforce that rule.

Keeping the source colocated avoids extracting a shared domain package before
there is evidence of an independent worker release cadence, materially
different dependency set, narrower credential boundary, or separate ownership
model. Process-level failure and scaling isolation already exist. Explicit
composition makes a later source extraction safer because dependencies will no
longer be discovered through globals.

## 2. Composition architecture

Each executable process has one manual composition root:

```text
Validated configuration
    |
    +-- API composition root
    |     +-- database engine/session factory
    |     +-- Redis and infrastructure clients
    |     +-- authentication and observability
    |     `-- ApiRuntime -> FastAPI request dependencies
    |
    +-- Call-lifecycle worker composition root
    |     `-- CallLifecycleWorkerRuntime -> lifecycle ARQ jobs
    |
    +-- Background worker composition root
    |     `-- BackgroundWorkerRuntime -> background jobs/outbox handlers
    |
    `-- Agent composition root
          `-- AgentProcessRuntime -> per-call runtime
```

The anticipated organization is deliberately small:

```text
apps/api/app/composition/
    api.py
    workers.py
    runtime.py

apps/agent/agent/
    composition.py
```

The implementation plan may adjust filenames to follow existing package
conventions, but it must preserve the responsibilities and dependency
direction in this design. It must not create one shallow module per dependency.

Composition modules may import infrastructure, providers, services, and
framework adapters. Business services, repositories, provider interfaces, and
domain modules must not import composition modules.

## 3. Dependency scopes and ownership

Dependencies have one of three lifetimes.

### Process-scoped

Process-scoped resources include validated settings, database engines and
pools, Redis clients or pools, reusable HTTP/provider clients, storage clients
that require cleanup, and observability infrastructure. They are created once
for their owning process and closed once at shutdown.

### Operation-scoped

Database sessions, transactions, repositories bound to a session, and
lightweight request/job use cases are operation-scoped. A process runtime may
own the session factory, but it does not retain an open session. Operation
helpers guarantee rollback on failure and closure on success, failure,
cancellation, and timeout.

### Call-scoped

Agent conversation state, customer configuration snapshots, session state,
and call orchestration are isolated per call. They may borrow process-owned
clients but do not close those clients.

The effective LiveKit worker model may make one agent job process correspond to
one call. The ownership distinction remains explicit so cleanup does not depend
on that implementation detail.

### Ownership rules

- A runtime closes only resources it created or explicitly adopted.
- ARQ's Redis connection is borrowed from ARQ and is not closed by an
  application runtime.
- A resource is registered for cleanup immediately after successful creation.
- A resource created before a later startup failure is still closed.
- Shutdown is idempotent and safe when invoked more than once.
- Shutdown closes resources in reverse construction order.

## 4. Lifecycle construction

An `AsyncExitStack` provides the standard partial-construction unwind
mechanism. Each root follows this sequence:

1. validate process-specific settings before opening resources;
2. create a resource;
3. immediately register its synchronous or asynchronous cleanup;
4. construct adapters and lightweight services from those resources;
5. publish the typed runtime only after all required construction succeeds;
6. transfer the exit stack to the runtime; and
7. close the stack once, in reverse order, during shutdown.

Runtime publication is atomic from the framework's perspective. FastAPI state,
ARQ application context, or LiveKit process data must not expose a partially
constructed runtime.

Each runtime has an explicit asynchronous close operation. Repeated or
concurrent shutdown signals must not cause clients to be closed twice. Tests
prove normal shutdown, duplicate shutdown, cancellation, and partial-startup
cleanup.

## 5. API composition

`create_app()` remains the outer API executable boundary. Production may call
it without arguments and load default configuration once. Tests can supply
validated settings and explicit construction overrides without changing the
environment.

The FastAPI lifespan constructs `ApiRuntime`, publishes it on application
state only after successful startup, and closes it during shutdown. A typed
request accessor retrieves the runtime and raises a specific composition error
for missing or invalid state.

FastAPI request dependencies use the runtime to obtain the session factory and
process-owned clients. A request dependency still creates and closes its own
database session. Repositories and lightweight services may be constructed per
request because they are cheap and session-bound.

Business services receive the dependencies they require by name. They do not
receive `ApiRuntime` and cannot use it as a service locator. Existing provider
construction in webhook or router dependencies moves to explicit request
composition backed by process-owned clients.

After migration, cached global engine, session-factory, Redis, storage, and
provider accessors that no longer have a legitimate executable-boundary role
are deleted. Test fixtures no longer clear their caches.

## 6. ARQ worker composition

The call-lifecycle and background workers receive distinct runtime types. A
job registered to one worker cannot accidentally access providers or services
owned only by the other worker.

ARQ's `ctx` remains a framework dictionary. Application composition crosses
that untyped boundary exactly once:

```text
ctx[APPLICATION_RUNTIME_KEY] -> validated worker runtime
```

A small typed accessor verifies both presence and the expected runtime type.
A missing or incorrect runtime raises a dedicated worker-composition error. It
is not classified as a provider failure, business failure, or retryable job
failure.

ARQ-owned metadata such as `job_try`, `enqueue_time`, and the borrowed Redis
connection may still be read from `ctx`. Application dependencies, clocks,
providers, observability, handler registries, and session factories are not
looked up through arbitrary `ctx.get(...)` calls.

Job functions act as framework adapters:

1. validate the runtime;
2. read bounded ARQ metadata;
3. pass named dependencies to the underlying job/use-case function; and
4. preserve existing timeout, retry, cancellation, transaction, and
   observability behavior.

The background composition root constructs the explicit outbox handler
registry with its provider and infrastructure dependencies already bound.
Handlers no longer construct missing providers or settings on demand.

ARQ requires settings-class metadata before `on_startup`. The ARQ bootstrap is
therefore an allowed executable boundary: it may load one validated settings
object for class metadata and pass that same object into runtime construction.
This exception does not permit jobs or business modules to call
`get_settings()`.

## 7. Agent composition

The agent bootstrap loads and validates settings once and constructs an
`AgentProcessRuntime` through the LiveKit process lifecycle. Prewarmed assets
and reusable clients are owned by that runtime. Per-call entrypoints borrow
them while constructing isolated call state.

Pipeline selection and plugin construction receive explicit settings or a
small immutable configuration value. Pipeline functions do not repeatedly call
`get_settings()`.

`EventPublisher` receives a Redis client or publisher transport through its
constructor. It does not lazily create an unowned Redis client.
`AgentApiClient`, publishing resources, and any reusable provider clients have
one documented owner and one shutdown path. LiveKit shutdown callbacks close
the runtime exactly once, including entrypoint failure and cancellation paths.

Dynamic voice pipeline selection remains supported. Dynamic import is a
plugin-selection mechanism at the composition boundary, not a dependency
resolution mechanism inside business logic.

## 8. Required, fake, and disabled dependencies

Each root validates only configuration relevant to its process, but every
dependency required by the selected mode is mandatory.

- Missing database, Redis, authentication, or selected-provider configuration
  stops the affected process before it accepts work.
- A supported fake mode constructs a named fake adapter.
- A supported disabled mode constructs a named null adapter only when disabled
  behavior is a valid product state.
- Selecting a real provider and failing to construct it is a startup failure.
- No real provider silently falls back to fake or disabled behavior.
- Error messages identify the dependency or setting category without exposing
  credential values.

Lazy validation remains acceptable only for data that cannot be validated
until a customer operation supplies it. Static process configuration is not
deferred until a request or job.

## 9. Explicit parameters without dependency bags

Typed runtime objects contain process-owned resources and immutable process
configuration. They are framework-boundary objects, not general containers.

Business services and use cases receive their actual dependencies explicitly.
A longer truthful constructor is preferred to a short constructor that accepts
`ApiRuntime`, `WorkerRuntime`, an untyped dictionary, or a generic container.

Closely related immutable policy values may be grouped when they form a real
concept. Unrelated dependencies are not bundled merely to shorten signatures.
The implementation must not introduce automatic registration, reflection,
decorator discovery, or runtime resolution by type/name.

## 10. Test strategy

### Composition unit tests

Every runtime factory is tested for:

- successful construction from explicit settings and fakes;
- absence of environment reads and network calls;
- precise failure for missing required configuration;
- correct real, fake, and disabled adapter selection;
- normal close exactly once;
- repeated and concurrent close safety; and
- cleanup of every resource opened before each meaningful startup failure.

### Service and job tests

Tests inject dependencies directly and cover:

- missing and incorrectly typed worker runtimes;
- correct use of ARQ retry and enqueue metadata;
- success, retryable failure, terminal failure, cancellation, and timeout;
- transaction commit, rollback, and close behavior;
- complete deterministic outbox registration; and
- agent pipeline and event publication without global settings/client patches.

### Integration tests

- API lifespan startup and shutdown use real application wiring with fake
  external systems.
- Repository/service integrations exercise a real test database implementation
  rather than mocked repository behavior.
- Worker startup, job adapters, use cases, and transactions are exercised
  together.
- Agent composition, per-call construction, failure, cancellation, and
  shutdown run without contacting LiveKit or external providers.
- Existing API, worker, agent, and applicable local end-to-end tests remain
  passing.

### Static and architecture tests

- Ruff and mypy remain clean.
- Worker composition cannot import FastAPI routers or API application startup.
- Business/domain modules cannot import composition modules.
- Migrated runtime code cannot restore global settings, session, client, or
  provider fallbacks.
- Coverage must not regress from the last verified repository baseline, and
  every new branch requires a meaningful assertion.

Tests that previously clear caches or deeply patch globals are rewritten to
use explicit settings and fakes. Old globals are not retained solely to keep
those tests unchanged.

## 11. Implementation phases

### Phase 1: lifecycle foundation and characterization

Add characterization tests for current behavior and the minimum typed
lifecycle primitives. Share lifecycle helpers within `apps/api` where the API
and workers genuinely repeat ownership behavior. Do not create a premature
cross-application package to remove a few lines from `apps/agent`.

### Phase 2: API composition

Introduce `ApiRuntime`, migrate API lifespan and request dependencies, and
remove the API globals replaced by that runtime. The phase is complete only
when API startup, shutdown, partial failure, and request integration tests pass.

### Phase 3: worker composition

Introduce distinct call-lifecycle and background runtimes, cross ARQ `ctx`
once, bind outbox handlers explicitly, and remove worker dependency fallbacks.
Queue topology and job policies remain unchanged.

### Phase 4: agent composition

Introduce `AgentProcessRuntime`, make pipeline configuration explicit, and
give publisher/API/provider clients deterministic ownership and cleanup.

### Phase 5: cleanup and enforcement

Delete obsolete factories, fallback branches, cache-clearing fixtures, and
deep global patches. Remove dead worker code only after reference checks and
tests prove it unused. Add import and architecture guards.

### Phase 6: full verification

Run the complete API, worker, and agent test suites, applicable integration and
local end-to-end suites, Ruff, mypy, line coverage, and branch coverage. Inspect
the final diff for duplicated construction, unfinished compatibility paths,
resource leaks, and boundary violations.

Every phase leaves migrated paths complete and tested. Issue 6A is not complete
while any agreed runtime still depends on hidden construction fallbacks.

## 12. Tradeoffs and risks

### Manual wiring is more verbose

Constructors and job adapters will expose more parameters. This is accepted
because explicit requirements are easier to understand, type-check, and test
than a service locator. Closely related values are grouped only when they form
a genuine concept.

### Strict startup reveals existing configuration defects

Processes may stop earlier than they do today. This is intentional: invalid
composition should not first appear during a customer call or provider event.
Failure messages and mode-selection tests reduce diagnosis cost.

### The workers retain the API dependency set

Keeping source colocated means a worker-specific minimal package/image is not
delivered by this issue. Separate runtime processes still provide failure and
scaling isolation. A distinct image or source app remains available later if
deployment, credential, dependency, or ownership evidence justifies it.

### The change spans three runtimes

The total effort is medium-high and the migration risk is medium. Ordered,
independently verified phases keep failures attributable and avoid a single
uncontrolled rewrite. The expected result has high positive impact and lower
ongoing maintenance through explicit ownership and stronger tests.

## Completion criteria

Issue 6A is complete only when:

1. API, lifecycle worker, background worker, and agent each have one explicit
   composition root.
2. Process resources have one owner and deterministic partial-startup and
   shutdown cleanup.
3. Database sessions remain operation-scoped and close on every path.
4. Application dependencies no longer use arbitrary ARQ `ctx` keys or silent
   fallbacks.
5. Services and agent pipelines no longer read global settings or construct
   hidden external clients.
6. Real, fake, and disabled provider modes are explicit and tested.
7. Obsolete global factories and cache-clearing tests are removed.
8. Architecture rules prevent the old dependency direction from returning.
9. Full tests, integration tests, Ruff, mypy, line coverage, and branch coverage
   pass without reducing the verified coverage baseline.
10. No realtime, deployment, provider, queue-policy, database-contract, or
    unrelated feature behavior has changed.
