# API Provisioning Coverage Stabilization Design

Date: 2026-08-02
Status: Implemented and verified
Decision: 3A / 3A-1A / 4A / 22A

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
- If coverage still varies after all approved decisions are deterministic, compare
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
5. Restart the full verification task from clean preconditions: two clean
   full coverage runs, one controlled-poison run, exact per-file coverage-set
   and totals equality, shared non-decreased ratchet handling, exact cleanup,
   and final review. Prior incomplete reports do not count toward acceptance.

If sharing one fixture-owned engine changes a legitimate test contract, stop
and present the concrete conflict rather than restoring request-level engine
churn, suppressing warnings, adding sleeps, forcing garbage collection, or
weakening the gate.

## Approved blocker resolution: 22A same-ID lifecycle coverage

The clean verification restart after 4A produced two identical reports: each
passed 2,401 tests with zero skips and the one known Starlette/httpx warning,
and each measured 90.03% line coverage and 77.73% branch coverage. The
controlled-poison run had the same pass, skip, warning, and branch results, but
its report alone executed
`SubscriptionRepository.upsert_by_stripe_subscription_id` line 107. That
single statement raised the poison report's line measurement to 90.04%, so the
three-report equality gate correctly failed. No run was retried, no ratchet or
completion document changed, and all exact disposable resources were removed.

Line 107 is a distinct lifecycle fence: when a stored subscription has the
same Stripe subscription ID as an incoming event but a different lifecycle
generation, the repository returns `None` before any mutation. Existing direct
tests protect the sibling different-ID generation fence but do not own this
same-ID decision. Test settings replace the poisoned values at collection and
per-test boundaries, test-mode settings omit dotenv loading, and 166 of 167
coverage files were identical. The poison file is therefore treated as a
timing perturbation that exposed incidental coverage, not as evidence of a
different business result.

Decision **22A** adds one explicit characterization test beside the existing
subscription repository lifecycle tests. It uses the established SQLite
`db_session`, a real user and subscription row, and the real repository. The
test seeds a generation-one subscription under a generation-two user, submits
the same Stripe subscription ID at generation two with deliberately different
mutable values, and asserts both a `None` result and a fully unchanged durable
row. It remains a standalone test rather than parameterizing the different-ID
case because the two predicates express separate business decisions and should
fail independently.

### 22A test-strength and acceptance

1. The unmodified production behavior passes the new characterization test.
2. Temporarily replacing only the line-107 `return None` with a fall-through
   mutation makes that exact test fail; restoring the source byte-for-byte
   makes it pass again. No production mutation is committed.
3. The complete subscription repository module, the earlier deterministic
   provisioning/hermeticity boundary, Ruff, mypy, lock, diff, and lockfile
   checks all pass.
4. A fresh task review confirms the test directly protects the same-ID
   lifecycle fence without changing production behavior or weakening existing
   integration coverage.
5. The full verification then restarts from clean resources. Two clean reports
   and one controlled-poison report must each pass 2,402 tests with zero skips,
   the same warning set, and identical per-file line/branch sets and totals.
   The prior failed reports do not count toward acceptance.

## Implementation evidence

The deterministic repository boundary now directly protects four provisioning
behaviors: changed stable-operation keys are rejected without durable mutation,
and missing rows are created and persisted by the succeeded, pending, and failed
transitions. A fifth direct test protects the same-subscription-ID lifecycle
fence and proves every durable field remains unchanged. Temporary mutations of
each of those five decisions made its exact test fail; every production source
line was then restored byte-for-byte and the focused tests returned green.

The 4A lifecycle work first reproduced the undisposed per-request engine as a
strict-warning RED (`3 passed, 1 error`) with the expected event-loop-closed
aiosqlite worker exception. Commit `24e4dc8` gives `test_app` one fixture-owned
engine and session factory, retains a fresh session per request, disposes the
engine in exception-safe outer teardown, and promotes
`PytestUnhandledThreadExceptionWarning` to an error. The identical diagnostic
boundary then passed all six selected tests with no leak markers or thread
warning. Focused gates passed 49 agent tests, 187 provisioning/hermeticity
tests, and 200 subscription/provisioning/hermeticity tests, all with zero
skips; Ruff, mypy over 169 source files, `uv lock --check`, diff checks, and
lockfile checks passed. The lifecycle repair and the same-ID test received
clean scoped reviews in commits `24e4dc8` and `a1da6ca` respectively.

Fresh final verification used only the named disposable PostgreSQL 17.8 and
Redis 7.4.7 services. Clean run 1, clean run 2, and the controlled-poison run
each passed exactly 2,402 tests with zero skips and the same single known
Starlette/httpx deprecation warning. All three reports had identical 167-file
key sets, identical normalized executed/missing line and branch sets for every
file, and identical totals: 10,957 of 12,169 statements and 2,504 of 3,220
branches covered. Exact coverage was 90.04026625030816007888898020% line and
77.76397515527950310559006211% branch, producing shared two-decimal
`ROUND_DOWN` floors of 90.04% and 77.76%. The line ratchet increased from
90.03% to 90.04% and the branch ratchet from 77.63% to 77.76%; neither
decreased, and all three retained reports passed separately against the new
floors.

After comparison and ratchet checks, the controlled ignored dotenv, local
coverage database and JSON, all three retained `/tmp` reports, and only the
two exact named containers were removed; absence was verified. No production
behavior, realtime or activation behavior, dependency or lockfile, deployment,
frontend, or agent boundary changed.
