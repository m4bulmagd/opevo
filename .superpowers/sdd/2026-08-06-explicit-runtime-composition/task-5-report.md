# Task 5 report — explicit API provider boundaries

## Status

Complete against base `d9c7093eff19616a1ac24115cf77367c98944237`.

Task 5 removes API provider, configuration, and observability fallbacks from the
locked constructors. API composition and current worker boundaries now pass
configuration and telemetry explicitly, and every operation-owned LiveKit,
Gemini, and S3 client has deterministic cleanup without closing borrowed
collaborators.

## RED evidence

Provider-boundary and lifecycle tests were added before production changes.
The initial focused run produced `6 failed, 50 passed`: settings remained
optional, carrier and telephony factories rejected explicit observability, and
Gemini did not close an owned client once while preserving borrowed clients.
The new S3 concurrency and borrowed-client assertions were already green,
confirming the Task 2 ownership behavior remained intact.

Worker ownership tests were then added before the worker migration:

- LiveKit dispatch ownership tests failed because the old provider attempted
  its own fallback construction and did not expose operation ownership.
- Summary cleanup failed because the old handler constructed Gemini without
  explicit configuration.
- API recording composition failed its construction-window test because the
  LiveKit API could not be registered for cleanup before later construction
  failed.
- Recording reconciliation tests failed before both LiveKit and S3 ownership
  were adopted at the worker boundary.

These failures observed the old interfaces and ownership behavior before each
production slice was changed.

## Implementation summary

- Locked carrier, telephony, Stripe, Gemini, S3, LiveKit dispatch, and LiveKit
  recording constructors to explicit configuration and observability inputs.
- Kept fake selection only in the named carrier, telephony, and subscription
  factories. Missing credentials in a selected real carrier/telephony mode now
  raise the existing safe terminal authentication failure and never fall back
  to a fake.
- Removed `get_s3_storage` and `get_telephony_provider`, along with all provider
  and service lookups of global settings or observability.
- Made billing session/service, summary, and LiveKit recording dependencies
  explicit and migrated API routers, webhooks, tests, and direct callers.
- Bound the API recording provider in `build_api_runtime`. The operation-owned
  LiveKit API is registered on the runtime cleanup stack immediately after its
  constructor succeeds, before provider or service construction can fail.
- Added idempotent, concurrency-safe Gemini cleanup. Internally constructed
  Google clients close once; injected clients remain borrowed.
- Added a worker operation-resource scope that closes adopted resources in
  reverse order, attempts every cleanup, safely reports cleanup failures, and
  preserves a primary delivery failure.
- Migrated existing worker call sites to resolve their current settings and
  telemetry once per outer handler, explicitly construct providers, close only
  operation-owned LiveKit/Gemini/S3 resources, and leave injected providers and
  storage borrowed. Task 8 remains responsible for moving this construction to
  the worker composition root.

## Verification

Prescribed focused provider/service gate:

```text
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/providers tests/billing/test_stripe_webhooks.py \
  tests/services/test_livekit_recording_service.py \
  tests/services/test_summary_service.py \
  tests/services/test_safe_service_exceptions.py
```

Result: `528 passed in 41.38s`.

Focused worker/outbox gate:

```text
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_account_deactivation.py \
  tests/workers/test_provider_cleanup.py \
  tests/workers/test_phone_provisioning_cleanup.py \
  tests/workers/test_phone_routing_readiness.py \
  tests/workers/test_livekit_dispatch_outbox.py \
  tests/workers/test_forwarding_verification_dispatch_outbox.py \
  tests/workers/test_post_call_outbox_handlers.py \
  tests/workers/test_recording_reconciliation.py \
  tests/integration/test_outbox_delivery.py
```

Result: `200 passed, 52 skipped in 27.92s`.

The first full coverage run exposed 36 instances of one stale webhook test
helper patching `LiveKitRecordingProvider` through the service module that no
longer imports or owns it. The obsolete hook was removed while guards against
webhook-level service, S3, and raw LiveKit API construction were retained. The
entire affected file then passed: `138 passed in 9.41s`.

Final complete API phase gate:

```text
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
# All checks passed!

UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
# Success: no issues found in 188 source files

UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  --cov=app --cov-branch --cov-report=term-missing
# 2796 passed, 133 skipped, 1 warning in 339.28s
# TOTAL 12780 statements, 3338 branches, 87.85% combined coverage
```

Coverage increased from the recorded `87.70%` baseline to `87.85%`. The sole
warning is the pre-existing Starlette/httpx deprecation warning. The suite used
only injected/fake providers and contacted no real provider.

Forbidden fallback sweep:

```text
! rg -n "get_settings\(|get_observability\(|get_s3_storage|get_telephony_provider" \
  app/providers app/services app/routers app/webhooks
# exit 0; no matches
```

`git diff --check d9c7093eff19616a1ac24115cf77367c98944237` also exits 0.

## Changed files

- Provider boundaries: carrier lookup, telephony, Stripe subscriptions,
  Gemini summaries, S3 storage, LiveKit dispatch, and LiveKit recording.
- Services: billing, billing sessions, summaries, and LiveKit recording.
- API boundaries and composition: activation and billing routers, Stripe
  webhook, and API runtime construction.
- Worker boundaries: account deactivation, customer/verification dispatch,
  phone routing/provisioning, provider cleanup, summary generation, and
  recording reconciliation, plus the operation-owned resource helper.
- Direct provider, service, API composition, webhook, worker, and integration
  tests, including the locked-signature test module.
- This report.

## Self-review against base

- Every locked constructor requires the planned configuration/provider and
  observability dependencies; no optional compatibility argument reintroduces
  global lookup or construction.
- API provider/service/router/webhook modules have zero forbidden fallback
  matches. `get_request_observability` remains runtime-backed and unchanged.
- Provider instrumentation, safe failure classification, fake-mode semantics,
  storage normalization, transaction boundaries, and HTTP contracts remain
  unchanged.
- Owned resources close once and in reverse construction order. Borrowed
  providers/clients are never registered for cleanup. Cleanup failure cannot
  replace the primary delivery error and is logged through safe exception
  reporting without secret-bearing messages.
- Runtime construction registers the LiveKit API before any later recording
  provider/service failure window. Partial API composition therefore unwinds
  the client through the existing runtime stack.
- Current worker-global resolution is limited to the plan-authorized outer
  handler bridge; Task 8 will replace it with captured worker runtime.
- No dependency, lockfile, migration, schema, generated contract, frontend, or
  protected CI/deployment path changed.

## Concerns and deferred scope

No open Task 5 concern.

Tasks 6–8 intentionally introduce typed worker runtimes and move the temporary
handler-side construction into process-owned worker composition. Task 5 does
not alter the outbox registry or delivery dependency model ahead of that work.
