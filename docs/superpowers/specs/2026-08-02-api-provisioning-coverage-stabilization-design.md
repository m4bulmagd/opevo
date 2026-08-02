# API Provisioning Coverage Stabilization Design

Date: 2026-08-02
Status: Approved design
Decision: 3A / 3A-1A

## Problem

The hermetic API test-environment branch passes all 2,397 tests consistently,
but aggregate branch coverage varies across otherwise identical clean runs. The
observed values were 77.57%, 77.63%, and 77.76% against a 77.63% branch
ratchet. Exact coverage-report comparisons isolated every changing line and
branch arc to `PhoneNumberProvisioningRepository`:

- `mark_running` can take the stable provider-operation-key conflict branch;
- `mark_succeeded` can create a missing provisioning row;
- `mark_pending` can create a missing provisioning row;
- `mark_failed` can create a missing provisioning row.

Those paths are currently covered only incidentally by asynchronous worker,
provider, cleanup, and account-lifecycle tests. The durable outcomes asserted
by those tests are deterministic, but task scheduling can change which
internal repository branches execute. The repository file is unmodified by
the hermeticity work, and all test runs have the same pass, skip, and warning
results. This is therefore a deterministic test-coverage gap rather than
evidence of a hermeticity behavior regression.

## Goals

- Cover each varying repository decision directly and deterministically.
- Assert the business state produced by every branch, not merely execute its
  lines for coverage.
- Preserve the existing PostgreSQL concurrency and lifecycle tests unchanged.
- Keep the tests fast and available without a PostgreSQL dependency by reusing
  the established SQLite `db_session` fixture.
- Make no production-code change unless a new test demonstrates a real defect;
  any such defect requires a separate user decision before implementation.
- Restore deterministic clean and poisoned full-suite coverage at or above the
  installed line and branch ratchets.

## Non-goals

- Changing provisioning state-machine behavior, locking, transaction
  boundaries, worker scheduling, or provider behavior.
- Replacing or weakening PostgreSQL concurrency tests.
- Lowering a coverage ratchet or retrying a flaky gate until it happens to
  pass.
- Enabling realtime or activation flow.
- Changing deployment, frontend, agent, dependency, or production
  configuration.

## Test boundary

Create
`apps/api/tests/repositories/test_phone_number_provisioning_repository.py`.
The module will use the existing function-scoped SQLite `db_session`, real ORM
models, and the real repository. It will not introduce a second database
engine or schema fixture.

A small helper will create and flush a uniquely identified `User`. Tests may
create the minimum additional real model required by a foreign key, such as a
`PhoneNumber` for the success case. State-transition assertions remain in four
explicit tests rather than a parameterized method-dispatch table; this keeps
failure output and expected fields readable while sharing only genuinely
repeated setup.

### Stable-key conflict

Seed and commit a provisioning row with an existing provider-operation key.
Call `mark_running` with a different non-null key and assert
`ProvisioningStateConflictError`. Roll back the failed unit of work, reload the
row, and assert that the durable key and prior state were not changed.

### Missing-row success fallback

Seed a user and its phone number without a provisioning row. Call
`mark_succeeded`, then assert that the returned and persisted provisioning row
is succeeded, references the phone number, has one attempt, is not retryable,
and has no error reason or payload.

### Missing-row pending fallback

Seed only a user. Call `mark_pending`, then assert that the returned and
persisted row is running, has one attempt, is not retryable, has no phone
reference, and preserves the supplied safe reason and payload.

### Missing-row failure fallback

Seed only a user. Call `mark_failed`, then assert that the returned and
persisted row is failed, has one attempt, preserves the supplied retry flag,
has no phone reference, and preserves the supplied safe reason and payload.

## Test-strength proof

The production behavior predates this work, so the new characterization tests
will initially pass rather than provide a conventional missing-feature RED.
To prove that each test protects its intended decision, perform controlled,
temporary mutation checks after the tests are written:

- disable or invert the stable-key conflict decision and prove its test fails;
- remove each missing-row fallback and prove the corresponding test fails for
  the expected behavioral reason.

Restore the production file byte-for-byte after every mutation. No mutation is
committed. Confirm the restored focused suite passes before continuing.

## Verification and acceptance

1. The new repository tests pass with zero skips.
2. Every temporary mutation produces the expected focused-test failure, and
   the fully restored source passes again.
3. The relevant focused repository, settings, collection, Clerk, and
   deployment-readiness tests pass together.
4. Ruff passes over `app` and `tests`; mypy passes over `app`; the lock check
   and `git diff --check` pass.
5. Against exact disposable PostgreSQL and Redis services, two identical clean
   full-suite coverage runs pass with zero skips and identical per-file
   executed/missing line and branch sets.
6. One identical full-suite run with the approved controlled conflicting
   `apps/api/.env` also passes with zero skips and matches the clean per-file
   coverage sets.
7. Both installed coverage ratchets pass. A ratchet may increase only to the
   two-decimal round-down floor shared by all three reports; neither ratchet
   may decrease.
8. The controlled dotenv, coverage reports, and exact disposable containers
   are removed, with absence verified. No broad Docker cleanup is permitted.
9. A scoped final review finds no unresolved correctness, test-quality,
   documentation, or cleanup issue.

## Failure handling

- If a direct test reveals a production defect, stop and present the defect
  and options before changing production code.
- If any full run fails behaviorally, diagnose that failure before proceeding.
- If coverage still varies after all four decisions are deterministic, compare
  per-file coverage sets again and stop for a new user decision rather than
  lowering the ratchet or adding retries.
- Always remove only the exact disposable resources created for verification,
  including after a failed gate.

## Approved blocker resolution: 4A test-app engine ownership

The first Task 2 verification attempt exposed a separate test-infrastructure
defect after the deterministic repository tests were committed. Clean run 1
passed 2,401 tests with the one known Starlette/httpx warning. Clean run 2 also
passed all 2,401 tests, but emitted an additional
`PytestUnhandledThreadExceptionWarning`: an aiosqlite worker tried to deliver a
result to an event loop that had already closed. The poison run and ratchet
decision were correctly withheld, all exact disposable resources were removed,
and no Task 2 tracked change was made.

Instrumented diagnosis traced the leaked connection to
`tests/conftest.py`'s `test_app` fixture. Its request dependency creates an
async engine, yields a session, and disposes the engine only after the yield.
When an expected route `HTTPException` is thrown into the dependency generator,
the session context unwinds but the unprotected disposal statement is skipped.
The unclosed connection can later be garbage-collected under another AnyIO
loop; aiosqlite's destructor queues shutdown work to that loop, and the worker
can respond after the loop closes. The later test named by pytest is therefore
not the producer.

Decision **4A** replaces request-level engine ownership with one explicit
fixture-level owner:

- `test_app` creates one async engine after resolving its unique database URL;
- the same engine creates the schema and one `async_sessionmaker`;
- the FastAPI dependency creates a fresh session per request from that factory
  and owns no engine;
- the outer `test_app` fixture disposes the engine in exception-safe teardown,
  after the application lifespan and request dependencies have unwound;
- the existing `app.state.test_database_url` contract remains unchanged;
- application routes, production database ownership, and production code are
  unchanged.

This removes the duplicate schema engine plus one engine/thread per request,
while retaining request-scoped sessions and a unique SQLite database per test
app. Multi-request tests continue to share only the fixture's engine/pool, not
ORM sessions or transactions.

Pytest configuration will also promote
`pytest.PytestUnhandledThreadExceptionWarning` to an error. This is strict
enforcement, not suppression: any future background-thread exception fails the
responsible test run. The existing known Starlette/httpx deprecation warning
remains unchanged.

### 4A test-first acceptance

1. Add the strict warning filter before changing fixture ownership and run the
   diagnosed minimal expected-409/call-completion sequence. It must fail with
   the reproduced aiosqlite event-loop-closed thread exception.
2. Implement only the fixture-owned engine/session-factory change and rerun the
   identical sequence. Every selected test must pass with no thread warning.
3. Run the complete agent-config and call-completion files with the strict
   filter, then the full focused/static/lock gates. All must pass cleanly.
4. Commit only `apps/api/tests/conftest.py` and `apps/api/pyproject.toml` for
   the independently reviewed 4A task. Do not change a dependency or lockfile.
5. Restart the original Task 2 verification from clean preconditions: two clean
   full coverage runs, one controlled-poison run, exact per-file coverage-set
   and totals equality, shared non-decreased ratchet handling, exact cleanup,
   and final review. Prior incomplete reports do not count toward acceptance.

If sharing one fixture-owned engine changes a legitimate test contract, stop
and present the concrete conflict rather than restoring request-level engine
churn, suppressing warnings, adding sleeps, forcing garbage collection, or
weakening the gate.
