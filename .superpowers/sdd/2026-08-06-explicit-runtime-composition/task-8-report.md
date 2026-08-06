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

## Implementation

- Replaced `DEFAULT_OUTBOX_HANDLERS` and handler `(ctx, event)` calls with
  `build_outbox_handlers(...)` and `Callable[[OutboxEvent], Awaitable[None]]`.
- Threaded explicit session, provider, policy, observability, token, agent, and
  clock dependencies through phone, account deactivation, provider cleanup,
  customer/verification dispatch, summary, and recording handlers.
- Constructed the recording reconciler from shared runtime-owned providers for
  each invocation without retaining a context fallback.
- Split `deliver_outbox_batch` and `reconcile_outbox` from thin ARQ wrappers.
- Used one runtime clock for claim, delivery/failure, and all reconciliation
  snapshots.
- Added import-cycle smoke coverage for runtime, composition, ARQ worker,
  registry, and delivery modules.
- Added composition coverage for selected provider outputs, immediate ownership,
  close-once reverse cleanup, every partial construction stage, and wrapper
  rejection of the lifecycle runtime before session access.

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

## Review

Standards review found the implementation aligned with the repository's typed
runtime, ownership, cancellation, validation, and test conventions. Spec review
confirmed exact topic coverage, one-argument bound handlers, explicit provider
construction, no fallback path, unchanged delivery classifications and retry
semantics, one-clock behavior, and queue isolation.
