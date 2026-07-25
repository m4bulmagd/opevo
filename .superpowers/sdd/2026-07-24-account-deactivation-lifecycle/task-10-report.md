# Task 10 Report: Provider-free deactivation restart acceptance

## Status and commit

- Status: complete.
- Starting HEAD: `204d566221fd00540651639c32c8cc8e23382f95`
- Commit: included in `test: cover account deactivation restart journey`
  (final SHA is returned in the parent handoff).
- Scope: Task 10 development fixtures, API integration coverage, phased browser
  acceptance, disposable runner, and the narrowly approved correction that
  preserves confirmed profile/carrier state across number-cycle cleanup. No
  production-provider or Task 11 documentation work was added.

## Files

- Added authenticated development-only start/finish call-drain fixtures through
  the real call repository and `CallLifecycleService`.
- Corrected `CustomerActivationRepository.reset_number_cycle` so it clears only
  number-cycle state and no longer locks or mutates the retained
  `BusinessProfile` carrier projection.
- Extended local API integration coverage for route registration, fake-mode
  enforcement, authentication, ownership, call history, charging, notification,
  summary-event behavior, and generation-matched direct-to-consent reactivation.
- Updated the worker and PostgreSQL data-preservation contracts to prove the
  exact confirmed carrier projection remains while phone/provisioning rows and
  all provisioning, verification, forwarding, go-live, and failure fields are
  cleared.
- Split the browser journey into activation, pre-restart deactivation, and
  post-restart drain/reactivation phases.
- Updated the local runner to use a private `mktemp` state directory, restart
  only API and worker while retaining data volumes, and clean everything on
  success, failure, or signal.

## RED evidence

The prescribed `uv run` command and a direct virtualenv run could not complete
inside the restricted process-stream sandbox. The same direct focused command
in the authorized normal environment produced the intended RED:

```text
3 failed, 2 passed in 2.24s
```

The missing start/finish routes returned `404` where the new tests required
authenticated/fake-mode behavior. A test-only UUID typing mistake found in that
first run was corrected before implementation.

Self-review then caught that the earlier number-cycle reset conflicted with the
authoritative reactivation design. The new preservation and reactivation tests
produced a second intended RED:

```text
2 failed in 1.53s
```

The worker test found `detected_carrier=None` instead of the retained original
value, and the provider-free API journey returned `profile_required` instead
of `provisioning_consent_required`. Removing only the profile-carrier mutation
made both tests green without changing the existing number-cycle clearing
assertions or provider/transaction ordering.

The browser journey then found and localized real RSC/hydration test boundaries
while preserving product behavior: pre-hydration deactivation and provisioning
review clicks plus a stale server-rendered deactivation progress view. Bounded
visible-state retries and an explicit reload after authoritative API
convergence made those transitions deterministic. Every failed full run
executed the same cleanup trap and removed its private state and named volumes.

## Development endpoint matrix

| Route | Environment and provider gate | Authentication/ownership | Mutation and response |
|---|---|---|---|
| `POST /api/development/call-drain-fixture/start` | Router is registered only for `APP_ENV=development`; fake telephony is required | local bearer identity is required; created call belongs to that internal owner | creates pending then connected call with owner phone/config references when present; emits no LiveKit/Telnyx work; returns only `call_id` |
| `POST /api/development/call-drain-fixture/finish` | same development registration and fake-mode gate | local bearer identity is required; foreign call IDs are hidden with `404` | uses agent-end, finalization claim, and real lifecycle completion; does not mutate account state directly; returns only `call_id` |

The integration test proves the connected call is visible in owner history,
contains no LiveKit room or dispatch identifiers, and finishes as `completed`
with the real one-minute charge, notification, and summary outbox event.
Foreign calls remain connected and unmodified. Both routes are absent when the
application environment is not development.

## Browser phase and restart proof

1. Activation provisions and records the first fake number into a Node-only
   JSON file.
2. The server-side Playwright request context starts one connected call; the
   browser proves the stable call ID is visible in history.
3. Exact `DEACTIVATE` moves the account immediately to non-serving state,
   blocks a new routing mutation with the stable lifecycle conflict, redirects
   activation back to Account, and remains at `draining_call`.
4. The runner restarts only API and worker. PostgreSQL, Redis, MinIO, web, and
   all named volumes remain in place.
5. The post-restart request context finishes the same call ID. The restarted
   worker resumes persisted cleanup and Account converges to `inactive`.
6. Calls still shows the same stable ID as `completed`.
7. Generation-matched local billing reactivation lands directly on fresh
   provisioning consent with the confirmed business/receptionist profile
   retained, provisions a second fake number, and asserts it differs from the
   first.

## Token and state privacy

- The auth value is consumed only from a server-side environment variable by
  Playwright's Node request context.
- It is never put in a URL, browser storage, client-prefixed variable, JSON
  state, assertion message, or test output.
- Tracing is disabled in both specs that use authenticated request headers.
- The state file contains only the old fake number and stable historical call
  ID, is created with mode `0600`, and lives under a `mktemp -d` directory.
- The cleanup trap removes the temporary directory on success, failure, HUP,
  INT, and TERM. The token is not printed by the runner.

## GREEN and verification evidence

Authoritative full acceptance under project Node 22.23.1:

```bash
PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:$PATH \
  bash scripts/run-local-e2e.sh
```

Result: exit `0`.

```text
activation.spec.ts          1 passed (30.3s; test 29.7s)
deactivation-start.spec.ts  1 passed (12.3s; test 11.7s)
restart-resume.spec.ts      1 passed (19.5s; test 18.8s)
```

The successful trap removed every project container, network, all five named
volumes, and the temporary state directory. A read-only post-run Docker
inspection showed no remaining project containers or named volumes.

The complete integration suite ran against disposable PostgreSQL 17 and Redis
with all environment-gated tests enabled:

```text
114 passed in 34.44s; 0 skipped
```

The exact PostgreSQL Task 8 preservation regression passed independently.
Affected worker, local billing, provisioning, readiness, call-history, and call
lifecycle regressions then passed **84/84**. The focused post-correction
preservation/direct-consent pair passed **2/2**. Disposable PostgreSQL/Redis
containers, network, and volumes were removed after verification.

Web and static gates:

- Complete Vitest suite: **30 files passed, 257 tests passed**.
- Biome: **156 files checked**, no fixes or errors.
- TypeScript: exit `0`.
- Ruff: all checks passed.
- Mypy: no issues in the development router or activation repository.
- Shell syntax and `git diff --check`: exit `0`.

## Concerns

- No unresolved concern remains. The earlier carrier/profile conflict was
  resolved against the authoritative design: customer profile confirmation and
  existing-line carrier data remain, reactivation resumes directly at fresh
  number consent, and every Presvo number-cycle artifact is still cleared.
- Fixture isolation, provider transaction boundaries, restart durability,
  history preservation, token handling, temporary-state cleanup, and
  disposable Docker teardown all have passing regression or acceptance proof.

## Fix round 1/5

Review base: `6c403678ec266bb5f743d4b83db44aa94277a6f6`.

### Finding and correction

The development router and fake-telephony gate alone did not prevent a
development deployment using Clerk auth from exposing the call-drain fixture
to authenticated customers. Both fixture routes now share one pre-repository
boundary that requires:

- `APP_ENV=development` through existing router registration;
- `AUTH_MODE=local`;
- `TELEPHONY_MODE=fake`;
- an authenticated local identity through the existing dependency.

Any non-local or non-fake registered configuration receives the same bounded
`local_telephony_disabled` conflict. The response does not reveal whether auth
mode or telephony mode made the fixture unavailable. The gate runs before call,
phone, agent, or lifecycle repository access.

### RED and GREEN evidence

The new Clerk-authenticated regression represented a synced Clerk owner through
the route's authenticated-identity dependency. Before the fix, the start route
returned `200` and the start/finish sequence exercised real lifecycle
mutations. The isolated RED failed on expected `409` versus actual `200`.

After centralizing the local fixture gate:

- focused fixture integration: **6 passed in 1.86s**;
- fixture, call history/lifecycle, and deactivation worker regressions:
  **63 passed in 8.12s**;
- Ruff: all checks passed;
- Mypy: no issues in the development router;
- shell syntax and `git diff --check`: exit `0`.

Coverage proves both Clerk-mode routes return the same safe conflict and leave
the pre-existing connected call unchanged; local-mode start/finish still use
the real lifecycle; missing auth remains `401`; non-fake local mode is blocked;
foreign calls remain hidden; and both routes remain absent outside
development.

### Full restart acceptance and privacy

The complete Node 22.23.1 runner remained green with its Compose API
configuration explicitly setting local auth:

```text
activation.spec.ts          1 passed (31.9s; test 31.3s)
deactivation-start.spec.ts  1 passed (14.9s; test 14.3s)
restart-resume.spec.ts      1 passed (59.8s; test 59.2s)
```

The secured fixtures therefore remain available only to the intended local
runner. The bearer value remained in the server-side request context and was
not written to URLs, browser storage, traces, screenshots, state JSON, or
output. The success trap removed every E2E container, network, all five named
volumes, and private temporary state.
