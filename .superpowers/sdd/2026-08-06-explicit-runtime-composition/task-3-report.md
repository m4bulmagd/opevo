# Task 3 report — typed API request sessions

## Status

Complete against base `99e477170d2299c4456eaa32042dbfb0546f9962`.

Task 2's owner-reviewed runtime resource accessors were preserved. Task 3 adds
the remaining operation-scoped request session boundary and migrates the
shared API test application to the real runtime composition path.

## RED evidence

Pre-change baseline:

```text
cd apps/api
.venv/bin/python -m pytest -q \
  tests/composition/test_api_composition.py \
  tests/agent/test_call_completion.py \
  tests/auth/test_jwt_auth.py
```

Result: `56 passed in 7.78s`.

After adding the missing-runtime, committed-persistence, generator-close,
handler-failure, and shared-runtime tests, before production or fixture
implementation:

```text
cd apps/api
.venv/bin/python -m pytest -q tests/composition/test_api_composition.py
```

Result: `5 failed, 9 passed in 2.73s`.

The four direct session tests failed with
`TypeError: get_session() takes 0 positional arguments but 1 was given`. The
shared-app test failed because the runtime retained the controlled PostgreSQL
default while requests were silently redirected to a separate SQLite session
override. These were the intended failures.

## Implementation summary

- Changed `get_session` to accept the FastAPI `Request`, retrieve the
  authoritative `ApiRuntime`, create one session from its process-owned
  session factory, and close it through the async session context manager.
- Added real SQLite tests proving:
  - a missing runtime fails with the composition error;
  - explicitly committed work persists after dependency closure;
  - generator closure rolls back uncommitted work and closes once; and
  - an exception injected at the dependency yield boundary rolls back
    uncommitted work and closes once.
- Changed the shared `test_app` fixture to create its database URL first,
  create schema through a temporary setup engine, dispose that engine, then
  pass the same URL in explicit settings to `create_app`.
- Removed the shared `get_session` dependency override and the database engine,
  session-factory, and Redis cache resets.
- Changed the shared `settings` fixture and rate-limiter configuration to use
  `Settings()` from the controlled pytest environment instead of
  `get_settings()`.
- Removed the test-only per-database application-state alias; callers now read
  the runtime's explicit database URL.

## Verification

Focused GREEN:

```text
cd apps/api
.venv/bin/python -m pytest -q \
  tests/composition/test_api_composition.py \
  tests/agent/test_call_completion.py \
  tests/auth/test_jwt_auth.py
```

Result: `61 passed in 8.10s`.

The prescribed request-boundary suite was split into terminal batches because
the command harness returns control after about 30 seconds:

```text
.venv/bin/python -m pytest -q tests/auth
# 247 passed, 1 skipped in 25.90s

.venv/bin/python -m pytest -q tests/agent/test_agent_config_api.py
# 33 passed in 10.43s

.venv/bin/python -m pytest -q \
  tests/agent/test_call_completion.py tests/agent/test_transcript_append.py
# 34 passed in 5.64s

.venv/bin/python -m pytest -q \
  tests/calls/test_call_finalization_state_machine.py \
  tests/calls/test_call_history_search.py \
  tests/calls/test_call_lifecycle.py \
  tests/calls/test_call_state_machine.py \
  tests/calls/test_call_summary_projection.py tests/dashboard
# 80 passed in 13.03s

.venv/bin/python -m pytest -q tests/calls/test_call_history_api.py
# 40 passed in 10.48s

.venv/bin/python -m pytest -q \
  tests/activation/test_development_api.py \
  tests/realtime/test_websocket_lifecycle.py \
  tests/livekit/test_dispatch_webhook.py \
  tests/test_collection_environment.py tests/test_readiness.py
# 42 passed, 1 pre-existing warning in 7.11s
```

Combined request-boundary result: `476 passed, 1 skipped`.

Static verification:

```text
.venv/bin/ruff check app tests/conftest.py \
  tests/composition/test_api_composition.py
# All checks passed!

.venv/bin/mypy app/composition app/core app/routers app/webhooks
# Success: no issues found in 37 source files
```

Full API suite, run outside the restricted sandbox as required for aiosqlite:

```text
.venv/bin/python -m pytest -q
```

Result: `2767 passed, 133 skipped, 1 warning in 347.83s`, exit code 0. The
warning is the pre-existing Starlette/httpx deprecation warning recorded in
the baseline.

Diff/static boundary checks:

```text
git diff --check 99e477170d2299c4456eaa32042dbfb0546f9962
# exit 0

rg -n "app\\.state\\.(settings|auth_provider|arq_pool|call_finalization_queue|observability|readiness_checks|realtime_service|livekit_webhook_receiver|storage_provider)" apps/api/app
# no matches

rg -n "get_engine\\.cache_clear|get_session_factory\\.cache_clear|get_redis_client\\.cache_clear|dependency_overrides\\[get_session\\]" apps/api/tests/conftest.py
# no matches
```

A non-gating repository-wide `ruff format --check` diagnostic still reports
the same existing formatter drift in many files, including the base revisions
of `core/database.py` and `tests/conftest.py`. The plan's required Ruff lint
gate is clean; Task 3 did not perform an unrelated repository-wide formatting
rewrite.

## Changed files

- `apps/api/app/core/database.py`
- `apps/api/tests/composition/test_api_composition.py`
- `apps/api/tests/conftest.py`
- `.superpowers/sdd/2026-08-06-explicit-runtime-composition/task-3-report.md`

## Self-review against base

- Public HTTP, authentication, provider, queue, and product behavior is
  unchanged.
- Existing commit ownership remains with handlers/services; the dependency
  neither auto-commits nor changes transaction boundaries.
- Both generator closure and handler exception paths are verified against a
  real SQLite engine for rollback, non-persistence, and one close.
- Successful explicit commit is verified to persist after dependency closure.
- `composition/runtime.py` remains construction-free and unchanged. Its
  database and auth annotations remain under `TYPE_CHECKING`; the allowed
  dependency direction is `core/database.py -> composition/runtime.py`.
- No per-resource production `app.state` aliases were added or retained.
- No dependency, lockfile, schema, migration, or unrelated runtime file was
  changed.
- Mutation check: changing `get_session` back to the cached global factory,
  omitting the runtime error, auto-committing, or failing to close/roll back
  would fail at least one direct Task 3 test.

## Concerns and deferred scope

No open Task 3 concern.

The legacy cached engine/session-factory functions remain only because current
worker jobs still import them. Removing those functions or migrating worker
callers here would cross the locked Task 3/P1A boundary into the later typed
worker-runtime tasks. They are no longer used by the FastAPI request session
dependency or the shared API test application.

## Fix round 1/5

### Findings addressed

- The shared `test_app` fixture no longer reads `CLIENT_TEST_DATABASE_URL`.
  It always uses `sqlite+aiosqlite:///<tmp_path>/test_client.db`, so every test
  gets an isolated database even if that environment variable names a
  persistent SQLite file or a network PostgreSQL database.
- The previous tautological runtime/database assertion was replaced with a
  test that poisons `CLIENT_TEST_DATABASE_URL` and independently derives the
  required tmp-path SQLite URL.
- Test schema initialization now disposes its setup engine on success and on
  every `BaseException`, including connection, schema, and cancellation
  failures. If disposal also fails, the original setup exception remains the
  primary exception.
- The three request-transaction tests now share one small async context manager
  for runtime/request/dependency ownership and teardown. Commit, rollback,
  handler-exception, close-count, and persistence assertions remain explicit
  in each test.
- Dedicated PostgreSQL integration fixtures were not modified.

### RED evidence

Before the fix implementation:

```text
cd apps/api
.venv/bin/python -m pytest -q tests/composition/test_api_composition.py \
  -k "shared_test_app_always or test_database_setup_disposes"
```

Result: `3 failed, 13 deselected in 1.18s`.

- The poisoned environment selected `persistent.db` instead of the required
  `test_client.db`.
- Both disposal parameter cases failed because
  `conftest._initialize_test_database` did not exist.

### GREEN and regression verification

```text
.venv/bin/python -m pytest -q tests/composition/test_api_composition.py \
  -k "shared_test_app_always or test_database_setup_disposes"
# 3 passed, 13 deselected in 1.20s

.venv/bin/python -m pytest -q tests/composition/test_api_composition.py
# 16 passed in 2.16s

.venv/bin/python -m pytest -q \
  tests/composition/test_api_composition.py \
  tests/agent/test_call_completion.py tests/auth/test_jwt_auth.py
# 63 passed in 6.33s

.venv/bin/ruff check app tests/conftest.py \
  tests/composition/test_api_composition.py
# All checks passed!

.venv/bin/mypy app/composition app/core app/routers app/webhooks
# Success: no issues found in 37 source files
```

Full API suite outside the restricted sandbox:

```text
.venv/bin/python -m pytest -q
```

Result: `2769 passed, 133 skipped, 1 warning in 250.61s`, exit code 0. The
warning remains the pre-existing Starlette/httpx deprecation warning.

### Fix-round self-review

- The isolated database URL is derived solely from the pytest-provided
  `tmp_path`; no environment or persistent path can override it.
- Setup-engine disposal is attempted exactly once on success and failure.
- A cleanup failure cannot replace the schema/connection/cancellation failure
  that triggered cleanup.
- The async test helper centralizes only resource ownership and teardown; it
  does not hide transaction behavior or expected database results.
- The fix-round delta is limited to the shared API fixture, its direct
  composition tests, and this report.

## Fix round 2/5

### Finding addressed

The direct SQLite session-test helpers now express nested ownership:

1. `_sqlite_api_runtime` is an async context manager that owns its engine from
   construction through schema setup and the complete caller body.
2. `_request_session_lifecycle` owns the request dependency inside that outer
   engine scope.

An engine is therefore disposed if schema setup fails and after dependency
close fails. If engine disposal fails at the same time, the original setup or
dependency-close exception remains primary and the disposal error is retained
as its cause. Transaction assertions remain in their individual tests.

### RED evidence

Before adding engine/dependency ownership:

```text
cd apps/api
.venv/bin/python -m pytest -q tests/composition/test_api_composition.py \
  -k "sqlite_runtime_disposes or lifecycle_disposes_engine"
```

Result: `4 failed, 16 deselected in 0.49s`.

Both setup-failure cases failed because `_sqlite_api_runtime` had no owning
context-manager/factory seam. Both dependency-close cases failed because
`_request_session_lifecycle` had no nested ownership/factory seam.

### GREEN and proportional verification

```text
.venv/bin/python -m pytest -q tests/composition/test_api_composition.py \
  -k "sqlite_runtime_disposes or lifecycle_disposes_engine"
# 4 passed, 16 deselected in 0.53s

.venv/bin/python -m pytest -q tests/composition/test_api_composition.py
# 20 passed in 2.28s

.venv/bin/python -m pytest -q tests/composition
# 25 passed in 2.69s

.venv/bin/ruff check tests/composition/test_api_composition.py
# All checks passed!

.venv/bin/mypy app/composition app/core app/routers app/webhooks
# Success: no issues found in 37 source files
```

This round changes only test-local ownership helpers and their direct tests;
it does not change the shared fixture or production code. The full composition
package is therefore the proportional broader regression gate. The full API
suite from Fix Round 1 remains `2769 passed, 133 skipped` on the unchanged
production/shared-fixture tree.

### Fix-round self-review

- Setup failure before runtime yield disposes the engine exactly once.
- Dependency-close failure disposes the outer engine exactly once.
- Both paths are tested with successful and failing disposal, and assert the
  exact primary failure object.
- Normal commit, generator-close rollback, and handler-failure transaction
  assertions remain visible and continue to pass against real SQLite.
- The delta from `45427f0` is limited to the direct composition test file and
  this report.

## Fix round 3/5

The two parameterized cleanup tests now bind the exact disposal exception and
assert it is the primary failure's `__cause__` when disposal fails. The same
assertion requires `__cause__ is None` when disposal succeeds.

Because diagnostic chaining already existed, the new assertions were verified
with an explicit mutation check. After temporarily replacing
`raise operation_error from cleanup_error` with `raise operation_error`:

```text
cd apps/api
.venv/bin/python -m pytest -q tests/composition/test_api_composition.py \
  -k "sqlite_runtime_disposes or lifecycle_disposes_engine"
```

Mutation RED result: `2 failed, 2 passed, 16 deselected in 0.60s`. Exactly the
two cleanup-failure cases failed because `__cause__` was `None`; both
successful-disposal cases continued to pass.

After restoring exception chaining:

```text
.venv/bin/python -m pytest -q tests/composition/test_api_composition.py \
  -k "sqlite_runtime_disposes or lifecycle_disposes_engine"
# 4 passed, 16 deselected in 0.64s

.venv/bin/ruff check tests/composition/test_api_composition.py
# All checks passed!
```

Self-review from `73f9ac4`: the final code change is limited to exact cause
assertions and named cleanup-error fixtures in the two direct failure tests;
production behavior and test helper behavior are unchanged.
