# Task 4 implementation report

Status: complete

Commit: `refactor(api): isolate phone outbox workflow` (this commit)

## Files

- Created `apps/api/app/workers/outbox/_account_lifecycle.py`
- Created `apps/api/app/workers/outbox/phone.py`
- Created `apps/api/app/workers/outbox/phone_provisioning.py`
- Deleted `apps/api/app/workers/jobs/phone_provisioning.py`
- Updated `apps/api/app/workers/jobs/outbox_topics.py`
- Retargeted the seven phone-related characterization test files named in the task brief.

## RED evidence

After retargeting tests and before creating the phone modules:

```
python -m pytest -q tests/workers/test_phone_routing_readiness.py \
  tests/workers/test_phone_provisioning_cleanup.py \
  tests/workers/test_individual_jobs.py
```

Collection failed as intended with:

```
ModuleNotFoundError: No module named 'app.workers.outbox.phone'
```

## GREEN and static evidence

- Phone-focused characterization subset: 40 passed.
- Complete required characterization set: 75 passed, 72 skipped in 12.09s. Skips are PostgreSQL-dependent integration cases without `TEST_DATABASE_URL`.
- Ruff: passed for the three new modules, the three worker test files, and the updated registry.
- Mypy: `Success: no issues found in 3 source files`.
- Obsolete-name/path scan passed: no `app.workers.jobs.phone_provisioning` or `phone_provisioning_job` Python references; old module file absent.
- `git diff --check` passed.

SQLite-backed pytest gates were executed outside the sandbox because the known aiosqlite worker-thread wakeup issue hangs in the sandbox.

## Self-review

The complete phone delivery family was moved intact. It retains lifecycle-generation validation/current-account checks, stale-running recovery admission, pending-provider non-exhaustible retries, durable provider operation keys, routing target persistence, compensation and projection ordering, transactions, provider-failure mapping, and exception chaining. The provisioning operation was moved without a facade and received only the approved public rename plus its two fixed safe log prefixes. The registry now imports the phone handlers from `outbox.phone`; shared lifecycle policy has no import cycle.

## Concerns

None. PostgreSQL-only tests were skipped because this environment did not provide `TEST_DATABASE_URL`; the required suite reported those skips explicitly.
