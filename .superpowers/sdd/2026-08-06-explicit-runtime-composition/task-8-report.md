# Task 8 Report: Explicit outbox runtime composition

## Result

Every outbox topic is now a one-argument callable bound by worker composition.
Topic handlers accept an `OutboxEvent` plus explicit keyword-only dependencies;
they no longer inspect ARQ context, construct providers, or read global settings,
sessions, observability, handlers, or clocks.

The background runtime constructs and owns LiveKit, Gemini, and S3 resources,
binds the complete registry, and closes resources exactly once in reverse order.
The delivery engine is a pure named-dependency seam; its ARQ wrappers accept only
`BackgroundWorkerRuntime`.

## Contract correction 6A-C2A

The owner approved a narrow correction required to preserve existing dispatch
behavior:

- `build_outbox_handlers` additionally receives
  `max_call_duration_seconds: int`;
- customer dispatch additionally receives `activation_flow_enabled: bool` and
  `max_call_duration_seconds: int`; and
- verification dispatch additionally receives explicit `DispatchTokenConfig`.

The main implementation plan and Task 8 brief now contain the corrected locked
signatures and registry bindings.

## RED evidence

The initial architecture test failed because `build_outbox_handlers` did not
exist. The prescribed topic-family baseline remained green before production
changes:

```text
152 passed in 19.16s
```

This established that the new registry/signature expectation, rather than an
unrelated behavior regression, was the missing seam.

Fix Round 1 added independent regression coverage before each correction:

- three runtime-composition cases failed because invalid dispatch-token
  configuration still acquired external resources before validation;
- the focused post-call suite failed collection because the requested
  `reconcile_recording_operation` seam did not exist;
- the first complete PostgreSQL delivery run exposed 12 stale expectation and
  clock failures, followed by one remaining normalized-metric expectation; and
- the first complete API run exposed four remaining legacy calls (two dispatch
  snapshot calls and two parametrized post-call calls).

Each failure was reproduced at its narrowest seam before the corresponding
production or caller migration was made.

## Implementation

- Replaced `DEFAULT_OUTBOX_HANDLERS` and handler `(ctx, event)` calls with
  `build_outbox_handlers(...)` and `Callable[[OutboxEvent], Awaitable[None]]`.
- Threaded explicit session, provider, policy, observability, token, agent, and
  clock dependencies through phone, account deactivation, provider cleanup,
  customer/verification dispatch, summary, and recording handlers.
- Constructed the recording reconciler from shared runtime-owned providers for
  each invocation without retaining a context fallback.
- Validated and normalized dispatch-token configuration before entering the
  runtime resource stack, so invalid configuration cannot acquire LiveKit,
  Gemini, or S3 resources and cannot disclose the raw secret-validation cause.
- Added a narrow `reconcile_recording_operation` test seam; the production
  handler still validates the event and constructs its reconciler from explicit
  providers on every invocation.
- Split `deliver_outbox_batch` and `reconcile_outbox` from thin ARQ wrappers.
- Used one runtime clock for claim, delivery/failure, and all reconciliation
  snapshots.
- Added import-cycle smoke coverage for runtime, composition, ARQ worker,
  registry, and delivery modules.
- Added composition coverage for selected provider outputs, immediate ownership,
  close-once reverse cleanup, every partial construction stage, and wrapper
  rejection of the lifecycle runtime before session access.
- Migrated every remaining API and PostgreSQL caller to the explicit delivery,
  reconciliation, handler-registry, and dispatch-snapshot contracts.
- Replaced legacy test dependency dictionaries with named arguments or focused
  frozen dependency fixtures. Remaining `dict[str, object]` values represent
  event payload/metadata, not runtime dependency bags.
- Removed the obsolete ambient-settings fixture from PostgreSQL delivery tests
  and removed post-call tests that mutated a production module global.

## Verification

Focused composition and architecture contract suite:

```text
75 passed in 7.15s
```

Recording and post-call handler slice:

```text
83 passed in 8.91s
```

Prescribed complete worker phase gate:

```text
346 passed, 54 skipped in 38.64s
```

The required production fallback scan returned no matches. Static checks:

```text
ruff check app tests/workers
All checks passed!

mypy app/workers app/composition
Success: no issues found in 30 source files
```

`git diff --check` also passed.

### Fix Round 1 final gate

The isolated PostgreSQL regression slice, including all 54 delivery integration
tests and the affected dispatch/post-call parametrizations, passed:

```text
58 passed in 19.74s
```

The final API suite ran with branch coverage enabled:

```text
3035 passed, 1 warning in 428.93s (0:07:08)
```

The repository coverage ratchet passed:

```text
coverage line=91.90% (minimum 90.04%), branch=80.32% (minimum 77.76%)
```

The final test-adapter cleanup passed its focused slice:

```text
31 passed in 4.81s
11 PostgreSQL cases passed, 43 deselected in 5.08s
```

Dependency and static gates also passed:

```text
uv lock --check
Resolved 134 packages

ruff check app tests
All checks passed!

mypy app
Success: no issues found in 189 source files
```

Final scans found no `DEFAULT_OUTBOX_HANDLERS` references, no legacy delivery or
reconciliation job invocations, no ambient settings/dependency bags in the
reviewed worker adapters, and no post-call production-global mutation. The only
job-symbol matches are the two intended ARQ wrapper definitions.

PostgreSQL and Redis were launched under the isolated Compose project
`opevo_issue6a_test`. `docker compose down --volumes --remove-orphans` removed
only that project's containers, network, and volumes. The original
`bmad-opevo-*` web, API, worker, agent, PostgreSQL, Redis, and MinIO services
remained running (stateful services healthy) after cleanup.

## Review

Standards review found the implementation aligned with the repository's typed
runtime, ownership, cancellation, validation, and test conventions. Spec review
confirmed exact topic coverage, one-argument bound handlers, explicit provider
construction, no fallback path, unchanged delivery classifications and retry
semantics, one-clock behavior, and queue isolation.
