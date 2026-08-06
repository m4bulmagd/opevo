# Task 7 Report: Explicit lifecycle jobs and dead-job removal

## Result

The three lifecycle job bodies are now pure asynchronous functions with the
approved keyword-only dependency signatures. Their ARQ entrypoints validate
exactly one concrete typed runtime and forward named fields without a context
or global fallback.

The unregistered `notifications_job` module and its three job-only tests were
deleted after the required production scan proved there was no registration or
caller. Notification domain services, repositories, models, and their
independent worker behavior remain unchanged.

## RED evidence

Tests were changed before production code.

- `test_call_finalization_worker.py`: `4 failed`; the direct and adapter tests
  failed because `finalize_call` did not exist, while missing/wrong runtimes
  still reached the legacy global database construction path.
- `test_call_reconciliation_wakeup.py`: `7 failed, 2 passed`; every new direct
  and adapter case failed because `reconcile_calls` did not exist.
- The prescribed three-file collection failed because
  `expire_verification_windows` could not be imported.

These are the expected failures for the three missing use-case seams and the
legacy untyped wrapper behavior.

## Implementation

- `finalize_call(payload, *, session_factory)` retains payload validation,
  claim/generation handling, session scope, and result mapping.
- `reconcile_calls(*, session_factory, arq_pool, observability, settings, now)`
  retains the reconciliation limit, background outbox wakeup, safe logging,
  outcome metrics, optional snapshot telemetry, and failure isolation.
- `expire_verification_windows(*, session_factory, now, batch_size=100)`
  retains the operation-scoped session, expiry service call, batch behavior,
  logging, and result shape.
- `call_finalization_job` and `call_reconciliation_job` require
  `CallLifecycleWorkerRuntime`.
- `verification_expiry_job` requires `BackgroundWorkerRuntime`, matching its
  registered cron owner.

The public ARQ function names and registrations, queues, policies, timeouts,
retries, result retention, transaction behavior, provider behavior, and
observability behavior were not changed.

## Dead-job proof

Before deletion, the required scan was:

```text
rg -n "notifications_job|app\.workers\.jobs\.notifications" \
  apps/api/app apps/agent libs docs -g '*.py' -g '*.md'
```

It returned only:

- the implementation at `apps/api/app/workers/jobs/notifications.py`; and
- historical Task 7 plan text.

There was no production registration or call. After deletion, the same scan
returns only the historical plan text, and the API test tree has no remaining
notification-job reference.

## Verification

Focused GREEN:

```text
22 passed in 1.93s
```

Prescribed full worker gate:

```text
360 passed in 33.60s
```

The count is exactly three lower because the three proved-dead job tests were
deleted.

Static checks:

```text
ruff check app/workers tests/workers
All checks passed!

mypy app/workers/jobs app/composition/workers.py
Success: no issues found in 4 source files
```

Signature inspection confirmed the three exact approved use-case signatures
and thin ARQ adapter signatures.

## Review

The independent standards review found no documented-standard violation. It
noted only a low, judgement-call duplication in local runtime test scaffolding;
the helpers remain local so each adapter test shows its complete concrete
runtime and does not depend on shared hidden fixture state.

The independent spec review found no missing behavior, scope creep, or
incorrect implementation. It noted that the Task 7 file list names
`test_outbox_architecture.py` although no Task 7 step defines a change there.
That existing file already asserts the exact outbox registry, which excludes
notifications. No source-text or removed-symbol change-detector test was added.
