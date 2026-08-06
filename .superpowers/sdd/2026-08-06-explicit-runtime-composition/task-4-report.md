# Task 4 report — explicit API domain policy dependencies

## Status

Complete against base `6cb1987b66a19f94bc3220b7f7d318e179c883f4`.

Task 4 removes process-global policy and provider construction from the locked
token and domain-service interfaces. API routers and current worker/outbox
boundaries now derive settings once and pass immutable policy or concrete
providers explicitly.

## RED evidence

The token tests were changed first to import and pass the planned immutable
`DispatchTokenConfig`:

```text
cd apps/api
env PYTHONPATH=. .venv/bin/pytest -q \
  tests/auth/test_jwt_auth.py tests/auth/test_verification_token.py
```

Collection failed with two `ImportError` errors because
`DispatchTokenConfig` did not exist. No production token code had been changed.

After the token slice was green, constructor-policy and explicit-precedence
tests were added before the service migration. The direct policy slice failed
11 tests: the eight locked dependencies were absent or optional, the LiveKit
caller could not pass `activation_flow_enabled`, and reconciliation did not
pass settings into the service. These failures were the intended old-interface
observations.

During the broad gate, 30 agent route tests exposed bare test applications
whose runtime still used `settings=object()`, and one observability test mocked
the old reconciliation constructor. The test composition roots were migrated
to install explicit token settings and the mock was migrated to the locked
signature. The regression slice then passed 35 tests.

## Implementation summary

- Added frozen, slotted `DispatchTokenConfig(secret, ttl_seconds)` and
  `dispatch_token_config(settings)`. Secret and dispatch TTL validation now
  happens at the boundary; dispatch and verification token helpers require an
  explicit config with no fallback.
- Changed agent and forwarding-verification authentication to derive token
  config from the authoritative API runtime settings.
- Changed current dispatch outbox boundaries to derive settings/token config
  once and pass them through snapshot construction.
- Made activation policy required in `AgentConfigService`,
  `CustomerReadinessService`, `LiveKitDispatchService`, `OnboardingService`,
  and `AccountLifecycleService`, and propagated it through router, billing,
  webhook, and nested-service construction.
- Made reconciliation settings and session-factory types explicit, passing one
  settings object to both reconciliation policy and the observability snapshot.
- Made carrier and telephony providers required constructor dependencies;
  provider factories remain at the current router/worker boundaries.
- Removed `LiveKitDispatchService`'s unused `dispatch_client` compatibility
  argument and removed the obsolete fake collaborator from its tests.
- Migrated every direct production, unit, worker, and integration caller found
  by the repository-wide caller sweep.

## Verification

Focused token GREEN:

```text
env PYTHONPATH=. .venv/bin/pytest -q \
  tests/auth/test_jwt_auth.py tests/auth/test_verification_token.py
```

Result: `62 passed`.

Prescribed Task 4 GREEN:

```text
env PYTHONPATH=. .venv/bin/pytest -q \
  tests/auth tests/activation tests/agent \
  tests/services/test_onboarding_service.py \
  tests/livekit/test_durable_dispatch_service.py \
  tests/workers/test_call_reconciliation_wakeup.py \
  tests/workers/test_livekit_dispatch_outbox.py
```

Result: `197 passed in 23.42s`.

Broader direct-caller gate: `372 passed, 31 skipped in 33.79s`.

The final stable-tree API suite was run outside the restricted sandbox because
aiosqlite requires thread wakeups:

```text
env PYTHONPATH=. .venv/bin/pytest -q
```

Result: `2783 passed, 133 skipped, 1 warning in 226.85s`, exit code 0. The
warning is the pre-existing Starlette/httpx deprecation warning.

Static and boundary verification:

```text
env PYTHONPATH=. .venv/bin/ruff check app tests
# All checks passed!

env PYTHONPATH=. .venv/bin/mypy app
# Success: no issues found in 187 source files

! rg -n "get_settings\(" \
  app/core/dispatch_token.py app/core/verification_token.py \
  app/services/agent_config_service.py \
  app/services/customer_readiness_service.py \
  app/services/call_reconciliation_service.py \
  app/services/carrier_lookup_service.py \
  app/services/telephony_service.py \
  app/services/livekit_dispatch_service.py
# exit 0; no matches

rg -n "dispatch_client" app tests -g '*.py'
# no matches

git diff --check 6cb1987
# exit 0
```

## Changed files

- Token policy: `apps/api/app/core/dispatch_token.py`,
  `apps/api/app/core/verification_token.py`.
- API boundaries: account, activation, agent, and onboarding routers plus the
  LiveKit webhook.
- Domain services: account lifecycle, agent config, billing, reconciliation,
  carrier lookup, customer readiness, LiveKit dispatch, onboarding, and
  telephony.
- Worker boundaries: call reconciliation, customer dispatch, and verification
  dispatch.
- Directly affected auth, activation, agent, LiveKit, onboarding, service,
  worker, observability, and integration tests.
- Test policy helpers:
  `apps/api/tests/dispatch_token_config.py`,
  `apps/api/tests/reconciliation_settings.py`, and
  `apps/api/tests/services/test_explicit_policy_dependencies.py`.
- This report.

## Self-review against base

- All locked signatures require their policy/provider dependencies; none has a
  default that can silently reintroduce global lookup or construction.
- The eight forbidden modules contain no `get_settings()` calls, and the token
  modules no longer expose or call `require_dispatch_secret`.
- Explicit values win over the controlled environment in token, activation,
  readiness, LiveKit, reconciliation, and provider-backed tests.
- Runtime settings are read from the authoritative API runtime at request
  boundaries. Current worker globals remain only at the plan-authorized outer
  boundaries that Tasks 7 and 8 will replace with captured worker runtime.
- Repository and transaction ownership is unchanged; repositories remain
  operation-scoped and no commit/rollback boundary moved.
- Provider semantics, token claims, verification-token lifetime rules, HTTP
  contracts, and dispatch/readiness behavior are unchanged.
- No dependency, lockfile, migration, schema, or generated contract changed.

## Concerns and deferred scope

No open Task 4 concern.

Task 5 intentionally adds observability to provider factory boundaries. Tasks
7 and 8 intentionally replace the current job/outbox settings derivation with
captured worker runtime. Task 4 leaves those documented seams ready without
pulling later composition work into this commit.
