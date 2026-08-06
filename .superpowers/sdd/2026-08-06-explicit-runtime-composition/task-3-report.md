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
