# API Provider Failure Vocabulary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement approved review decision 7A by giving every active API third-party adapter one safe typed failure vocabulary and routing untranslated defects through a distinct, non-looping internal-defect path.

**Architecture:** Add one API-core `ProviderFailure` boundary type with bounded provider, operation, disposition, error-class, and context fields. Migrate one provider family at a time, preserving existing durable outbox/domain codes while removing legacy exception families. Finish by narrowing broad worker catches and making unknown exceptions terminal internal defects with safe high-severity diagnostics. Agent-to-API transport exceptions remain separate because they model HTTP delivery and acknowledgement semantics rather than third-party provider behavior.

**Tech Stack:** Python 3.13, FastAPI, ARQ, SQLAlchemy async, pytest/AnyIO, OpenTelemetry, Telnyx, Stripe, MinIO, LiveKit, Google GenAI, Ruff, mypy, uv.

## Global Constraints

- Implement approved decision **7A** exactly as recorded in `docs/engineering/2026-07-30-agent-api-review-decisions.md:451-514`.
- Keep realtime and activation-flow defaults disabled; do not enable, deploy, push, or open a PR.
- Do not change `apps/agent/agent/api_client.py` transport exception inheritance. Agent-to-API failures remain a separate boundary.
- Preserve durable outbox codes `provider_retryable` and `provider_terminal`; add `internal_defect` instead of rewriting historical rows or adding a database migration.
- Preserve workflow/domain exceptions: `TelephonyProvisioningPending`, `TelephonyProvisioningReviewRequired`, `UnresolvedProviderWorkError`, `StorageConfigurationError`, and `OutboxDeliveryError`.
- Preserve LiveKit recording `start_outcome` semantics using bounded safe context.
- Never serialize or log a raw provider exception message, response body, metadata, token, phone number, or cause. Retain the cause only through exception chaining.
- `asyncio.CancelledError` and other `BaseException` subclasses must propagate unchanged.
- A known translated retryable provider failure may use the existing bounded retry policy. Terminal provider failures and internal defects must not consume that retry loop.
- Do not add a generic “retry internal defects” extension point; no current operation has an approved internal-defect retry policy.
- Do not inspect developer `.env` files or credential-gated live-provider tests. Use controlled test environment variables.
- Use `UV_CACHE_DIR=/tmp/uv-cache` and the existing API environment through `UV_PROJECT_ENVIRONMENT=/home/mo/code/ai/bmad-opevo/apps/api/.venv` with `uv run --frozen --no-sync`.
- Do not inspect or modify `Opevo_frontend/` or `.worktrees/shadcn-activation-preview`.
- Every production behavior change follows RED → GREEN → REFACTOR. Record the expected failing assertion before implementation.
- Keep the solution explicit: shared bounded vocabulary and small mapping helpers are required; reflection, dynamic exception registries, and generic policy frameworks are not.

---

## File Responsibility Map

- `apps/api/app/core/provider_failures.py`: sole provider-boundary failure vocabulary, allow-lists, immutable safe context, and HTTP-status mapping.
- `apps/api/app/core/observability.py`: safe provider/internal-defect telemetry derived from the shared type; never raw exception content.
- `apps/api/app/core/logging.py`: bounded `provider` field for safe structured diagnostics.
- `apps/api/app/providers/**`: translate only known SDK/transport exceptions and malformed external responses into `ProviderFailure` exactly once.
- `apps/api/app/services/**`: validate provider-returned contracts, preserve domain errors, and consume the shared type without recreating taxonomies.
- `apps/api/app/workers/jobs/**`: map shared provider dispositions into existing durable codes; route all untranslated exceptions to `internal_defect` without retries.
- `apps/api/tests/providers/**`: table-driven adapter mappings, malformed-response/internal-defect separation, cause retention, cancellation, and privacy.
- `apps/api/tests/integration/test_outbox_delivery.py`: durable retry/terminal/internal-defect behavior.
- `docs/engineering/2026-07-30-agent-api-review-decisions.md`: mark Issue 7 implemented only after every verification gate passes.

---

### Task 1: Define the safe shared provider-failure boundary

**Files:**
- Create: `apps/api/app/core/provider_failures.py`
- Create: `apps/api/tests/providers/test_provider_failures.py`
- Modify: `apps/api/app/core/observability.py`
- Modify: `apps/api/app/core/logging.py`
- Modify: `apps/api/tests/test_observability.py`
- Modify: `apps/api/tests/test_redaction.py`

**Interfaces:**
- Produces: `ProviderFailure`, `ProviderFailureDisposition`, `ProviderFailureClass`, `ProviderName`, `ProviderOperation`, `provider_failure_from_http_status`.
- `ProviderFailure` exposes bounded `provider`, `operation`, `disposition`, `error_class`, immutable `context`, and derived `retryable` fields.
- `str(failure)`, `repr(failure)`, and `failure.args` contain only fixed/bounded values. Raw causes are retained only as `failure.__cause__` through explicit exception chaining.
- The only initial context key is `start_outcome`, with values `not_started` or `unknown`.

- [ ] **Step 1: Write contract tests before the module exists**

Add table-driven tests that name these breaks: accepting an unbounded disposition/class/provider/operation; leaking a raw cause; mutable context; accepting a token/phone/body as context; losing the chained cause; catching cancellation as a provider failure.

```python
@pytest.mark.parametrize("disposition", ["retryable", "terminal"])
@pytest.mark.parametrize(
    "error_class",
    [
        "timeout", "rate_limited", "unavailable", "authentication",
        "validation", "conflict", "not_found", "unknown",
    ],
)
def test_provider_failure_exposes_only_bounded_safe_fields(
    disposition: str,
    error_class: str,
) -> None:
    failure = ProviderFailure(
        provider="stripe",
        operation="cancel_subscription",
        disposition=disposition,
        error_class=error_class,
    )
    assert failure.disposition == disposition
    assert failure.error_class == error_class
    assert failure.retryable is (disposition == "retryable")
    assert "secret" not in str(failure).casefold()


def test_provider_failure_context_is_bounded_and_immutable() -> None:
    failure = ProviderFailure(
        provider="livekit",
        operation="start_recording",
        disposition="retryable",
        error_class="timeout",
        context={"start_outcome": "unknown"},
    )
    assert failure.context == {"start_outcome": "unknown"}
    with pytest.raises(TypeError):
        failure.context["start_outcome"] = "not_started"  # type: ignore[index]


def test_http_status_mapping_is_literal_and_provider_independent() -> None:
    expected = {
        408: ("retryable", "timeout"),
        429: ("retryable", "rate_limited"),
        503: ("retryable", "unavailable"),
        401: ("terminal", "authentication"),
        404: ("terminal", "not_found"),
        409: ("terminal", "conflict"),
        422: ("terminal", "validation"),
    }
    for status, want in expected.items():
        failure = provider_failure_from_http_status(
            provider="stripe",
            operation="cancel_subscription",
            status=status,
        )
        assert (failure.disposition, failure.error_class) == want
```

- [ ] **Step 2: Run the contract tests and verify RED**

Run:

```bash
APP_ENV=test \
CLERK_ISSUER=https://clerk.example.com \
CLERK_AUTHORIZED_PARTIES=https://app.example.com \
CLERK_JWKS_URL=https://clerk.example.com/.well-known/jwks.json \
UV_CACHE_DIR=/tmp/uv-cache \
UV_PROJECT_ENVIRONMENT=/home/mo/code/ai/bmad-opevo/apps/api/.venv \
uv run --frozen --no-sync python -m pytest -q \
  tests/providers/test_provider_failures.py \
  tests/test_observability.py \
  tests/test_redaction.py
```

Expected RED: import failure for `app.core.provider_failures` and then assertion failures for the new `not_found`/failure-kind telemetry.

- [ ] **Step 3: Implement the minimal bounded type and shared HTTP mapping**

Use explicit `Literal` aliases and frozen/validated fields. The implementation shape is:

```python
ProviderFailureDisposition = Literal["retryable", "terminal"]
ProviderFailureClass = Literal[
    "timeout", "rate_limited", "unavailable", "authentication",
    "validation", "conflict", "not_found", "unknown",
]

class ProviderFailure(RuntimeError):
    def __init__(
        self,
        *,
        provider: ProviderName,
        operation: ProviderOperation,
        disposition: ProviderFailureDisposition,
        error_class: ProviderFailureClass,
        context: Mapping[str, str] | None = None,
    ) -> None:
        # Validate every field against module-owned finite allow-lists.
        # Copy context into MappingProxyType after exact key/value validation.
        super().__init__("provider operation failed")

    @property
    def retryable(self) -> bool:
        return self.disposition == "retryable"
```

The allow-lists must cover only current active names and operations: Telnyx telephony/carrier operations, Stripe subscription/billing operations, S3 object/lifecycle operations, LiveKit dispatch/recording operations, Gemini summary generation, and local fake-provider validation. Do not accept arbitrary safe-looking strings.

`provider_failure_from_http_status` implements the literal test table, maps other `4xx` statuses to terminal `validation`, other `5xx` statuses to retryable `unavailable`, and `None`/unrecognized statuses to terminal `unknown`. Provider-specific semantic success such as idempotent 404 is handled before this helper is called.

- [ ] **Step 4: Extend the shared safe labels without changing runtime classification yet**

Add `not_found` to `SAFE_ERROR_CLASSES` so the shared type is represented without falling back to `unknown`. Do not add provider/internal classification to `provider_operation` in this task: legacy adapters still exist until Tasks 2-5, so early classification would mislabel them. Extend `report_safe_exception` with an optional allow-listed `provider` parameter; Task 6 uses it for the final worker alert. Never accept arbitrary metadata.

- [ ] **Step 5: Run GREEN, static checks, and mutation check**

Run the Step 2 command, then:

```bash
UV_CACHE_DIR=/tmp/uv-cache \
UV_PROJECT_ENVIRONMENT=/home/mo/code/ai/bmad-opevo/apps/api/.venv \
uv run --frozen --no-sync ruff check \
  app/core/provider_failures.py app/core/observability.py app/core/logging.py \
  tests/providers/test_provider_failures.py tests/test_observability.py tests/test_redaction.py

UV_CACHE_DIR=/tmp/uv-cache \
UV_PROJECT_ENVIRONMENT=/home/mo/code/ai/bmad-opevo/apps/api/.venv \
uv run --frozen --no-sync mypy app
```

Mutation check: replacing `retryable` with a constant, permitting an arbitrary context key, dropping `not_found`, or including `str(cause)` must fail at least one test.

- [ ] **Step 6: Commit the independently passing boundary**

```bash
git add apps/api/app/core/provider_failures.py apps/api/app/core/observability.py \
  apps/api/app/core/logging.py apps/api/tests/providers/test_provider_failures.py \
  apps/api/tests/test_observability.py apps/api/tests/test_redaction.py
git commit -m "refactor(api): define provider failure boundary"
```

---

### Task 2: Migrate Telnyx telephony and carrier lookup as one provider family

**Files:**
- Modify: `apps/api/app/providers/telephony/base.py`
- Modify: `apps/api/app/providers/telephony/fake.py`
- Modify: `apps/api/app/providers/telephony/telnyx.py`
- Modify: `apps/api/app/providers/carrier_lookup/base.py`
- Modify: `apps/api/app/providers/carrier_lookup/telnyx.py`
- Modify: `apps/api/app/providers/carrier_lookup/__init__.py`
- Modify: `apps/api/app/services/telephony_service.py`
- Modify: `apps/api/app/services/carrier_lookup_service.py`
- Modify: `apps/api/app/workers/jobs/phone_provisioning.py`
- Modify: `apps/api/app/workers/jobs/provider_cleanup.py`
- Modify: `apps/api/app/workers/jobs/account_deactivation.py`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py`
- Modify: `apps/api/tests/providers/test_fake_telephony_provider.py`
- Modify: `apps/api/tests/providers/test_carrier_lookup_providers.py`
- Modify: `apps/api/tests/telephony/test_telnyx_provider.py`
- Modify: `apps/api/tests/activation/test_activation_go_live_service.py`
- Modify: `apps/api/tests/services/test_safe_service_exceptions.py`
- Modify: `apps/api/tests/workers/test_phone_provisioning_cleanup.py`
- Modify: `apps/api/tests/workers/test_provider_cleanup.py`
- Modify: `apps/api/tests/workers/test_account_deactivation.py`
- Modify: `apps/api/tests/workers/test_individual_jobs.py`
- Modify: `apps/api/tests/integration/test_account_deactivation_concurrency.py`

**Interfaces:**
- Consumes: `ProviderFailure` from Task 1.
- Produces: all Telnyx/fake telephony and carrier operations raise the shared type for known provider failures and malformed external responses.
- Preserves: provisioning pending/review workflow exceptions and durable `provider_retryable`/`provider_terminal` codes.

- [ ] **Step 1: Add failing table-driven mapping and defect-separation tests**

Extend `tests/telephony/test_telnyx_provider.py` and `tests/providers/test_carrier_lookup_providers.py` so each known Telnyx exception/status maps to exact `(provider, operation, disposition, error_class)`. Cover connection retry flag true/false, timeout, rate limit, service unavailable, authentication, permission, resource not found, invalid request/parameters, conflict, other 4xx, 5xx, and unknown Telnyx SDK error.

Add separate tests proving:

```python
class DefectiveLookupResource:
    @staticmethod
    def retrieve(*_args: object, **_kwargs: object) -> object:
        raise TypeError("INTERNAL_SENTINEL")


provider = TelnyxCarrierLookupProvider(
    api_key="test-key",
    number_lookup_resource=DefectiveLookupResource,
)
with pytest.raises(TypeError, match="INTERNAL_SENTINEL"):
    await provider.lookup("+33612345678")


async def cancel_to_thread(*_args: object, **_kwargs: object) -> object:
    raise asyncio.CancelledError


monkeypatch.setattr(carrier_telnyx.asyncio, "to_thread", cancel_to_thread)
with pytest.raises(asyncio.CancelledError):
    await provider.lookup("+33612345678")
```

Malformed provider response fields remain terminal `validation`; an injected adapter `TypeError` is not translated.

- [ ] **Step 2: Verify RED with the focused Telnyx family suites**

Run the affected provider/service/worker tests. Expected RED: legacy exception types and weak carrier codes do not expose shared safe fields; phone provisioning still marks unexpected defects retryable.

- [ ] **Step 3: Migrate adapters and service contract validation**

- Remove `TelephonyProviderError` and `CarrierLookupError` definitions/exports.
- Translate known Telnyx SDK exceptions by constructing `ProviderFailure` and chaining the SDK exception as the cause; do not use `from None` for a real SDK cause.
- Translate only parsing of returned provider payloads to terminal `validation`; keep local/injected `TypeError` outside parsing blocks as internal defects.
- Make `TelephonyService` provider-return contract checks emit terminal `ProviderFailure` with the exact telephony operation.
- Update fake providers to emit terminal validation failures under the shared vocabulary.

- [ ] **Step 4: Migrate direct consumers without recreating taxonomy helpers**

Catch `ProviderFailure` in cleanup/deactivation only where durable compensation requires it. Map `failure.retryable` to the existing durable error code at that boundary. In phone provisioning, retain the actual failure object, persist only its bounded code/class, and re-raise it; for an untranslated exception persist `internal_defect` with `can_retry=False` and re-raise a safe chained internal error.

Do not add provider-family-specific worker exception classes.

- [ ] **Step 5: Run GREEN, static checks, and targeted mutation checks**

Run all modified Telnyx/carrier/service/worker tests. Confirm mutations of a Telnyx status mapping, response validation, phone-provisioning retryability, and cancellation propagation are caught. Run Ruff and mypy.

- [ ] **Step 6: Commit the passing Telnyx family migration**

```bash
git add apps/api/app/providers/telephony apps/api/app/providers/carrier_lookup \
  apps/api/app/services/telephony_service.py apps/api/app/services/carrier_lookup_service.py \
  apps/api/app/workers/jobs/phone_provisioning.py \
  apps/api/app/workers/jobs/provider_cleanup.py \
  apps/api/app/workers/jobs/account_deactivation.py \
  apps/api/app/workers/jobs/outbox_topics.py apps/api/tests
git commit -m "refactor(api): unify Telnyx provider failures"
```

---

### Task 3: Migrate Stripe subscription and hosted billing boundaries

**Files:**
- Modify: `apps/api/app/providers/subscriptions/base.py`
- Modify: `apps/api/app/providers/subscriptions/fake.py`
- Modify: `apps/api/app/providers/subscriptions/stripe.py`
- Modify: `apps/api/app/providers/subscriptions/__init__.py`
- Modify: `apps/api/app/services/billing_session_service.py`
- Modify: `apps/api/app/routers/billing.py`
- Modify: `apps/api/tests/providers/test_subscription_providers.py`
- Modify: `apps/api/tests/services/test_billing_session_service.py`
- Modify: `apps/api/tests/billing/test_billing_api.py`
- Modify: `apps/api/tests/workers/test_provider_cleanup.py`
- Modify: `apps/api/tests/workers/test_account_deactivation.py`
- Modify: `apps/api/tests/integration/test_account_deactivation_concurrency.py`
- Modify: `apps/api/tests/services/test_safe_service_exceptions.py`

**Interfaces:**
- Consumes: Task 1 shared boundary.
- Produces: both Stripe adapters use identical safe classes/dispositions for the same SDK/status condition without duplicating the status table.

- [ ] **Step 1: Write failing Stripe parity and internal-defect tests**

Table-drive both subscription cancellation and hosted-session operations through timeout, connection retry true/false, rate limit, authentication, permission, validation, not found, conflict, 5xx, base Stripe error, and malformed response. Assert identical shared fields for identical conditions.

Add tests proving an arbitrary `TypeError`/`RuntimeError` from an injected client propagates as an internal defect, `CancelledError` propagates, raw Stripe message/body/token sentinels are absent from failure text/logs, and missing subscription remains idempotent success.

- [ ] **Step 2: Verify RED**

Run `tests/providers/test_subscription_providers.py`, `tests/services/test_billing_session_service.py`, `tests/billing/test_billing_api.py`, cleanup/deactivation tests, and provider failure privacy tests. Expected RED: arbitrary exceptions still become retryable provider failures and two Stripe mappings are duplicated.

- [ ] **Step 3: Extract one explicit Stripe classifier and migrate both callers**

Place the SDK-specific mapping in `app/providers/subscriptions/stripe.py` as a pure function returning a `ProviderFailure` for a supplied operation. Reuse it from `BillingSessionService`; do not duplicate status logic. It returns `None` for exceptions outside the known Stripe/transport hierarchy so callers re-raise the original defect.

Replace `SubscriptionProviderError` and `BillingSessionProviderError` with the shared type. Preserve `BillingSessionStateError` and `BillingPortalReturnUrlError` as domain/configuration errors. Keep router HTTP status/body behavior safe and unchanged while catching `ProviderFailure`.

- [ ] **Step 4: Migrate cleanup/deactivation consumers and preserve durable policy**

Update imports/type annotations and map disposition/class to existing durable codes. Authentication and terminal contract failures retain their current attention-required behavior; retryable failures retain current bounded/non-exhausting domain policy only where already explicit.

- [ ] **Step 5: Run GREEN, parity mutation checks, Ruff, and mypy**

Mutating either Stripe caller to a local status table, converting an arbitrary runtime defect, or exposing a raw response must fail. Run the focused suites, Ruff, and mypy.

- [ ] **Step 6: Commit the passing Stripe family migration**

```bash
git add apps/api/app/providers/subscriptions apps/api/app/services/billing_session_service.py \
  apps/api/app/routers/billing.py apps/api/app/workers/jobs/account_deactivation.py \
  apps/api/app/workers/jobs/provider_cleanup.py apps/api/tests
git commit -m "refactor(api): unify Stripe provider failures"
```

---

### Task 4: Migrate storage and summary provider boundaries

**Files:**
- Modify: `apps/api/app/providers/storage/base.py`
- Modify: `apps/api/app/providers/storage/s3.py`
- Modify: `apps/api/app/providers/summaries/base.py`
- Modify: `apps/api/app/providers/summaries/gemini.py`
- Modify: `apps/api/app/services/summary_service.py`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py`
- Modify: `apps/api/app/workers/jobs/recording_reconciliation.py`
- Modify: `apps/api/tests/providers/test_s3_lifecycle.py`
- Modify: `apps/api/tests/providers/test_integrations.py`
- Modify: `apps/api/tests/providers/test_summary_gemini.py`
- Modify: `apps/api/tests/services/test_summary_service.py`
- Modify: `apps/api/tests/workers/test_post_call_outbox_handlers.py`
- Modify: `apps/api/tests/workers/test_individual_jobs.py`
- Modify: `apps/api/tests/workers/test_recording_reconciliation.py`

**Interfaces:**
- Consumes: Task 1 shared boundary and HTTP mapping.
- Produces: known MinIO/network and Google GenAI failures are typed; missing S3 objects keep semantic success where currently idempotent; arbitrary defects are never translated.

- [ ] **Step 1: Write failing hermetic S3 and Gemini mapping tables**

Move mapping coverage out of credential-gated behavior by using fake clients in hermetic tests. Cover S3 timeout, rate limit, unavailable, authentication, conflict, not found/idempotent success, malformed response, and unknown MinIO SDK errors. Cover Google GenAI client/server statuses, timeout, rate limit, authentication, conflict, malformed JSON/schema, missing credentials/import, arbitrary injected defects, and cancellation.

- [ ] **Step 2: Verify RED**

Expected RED: S3 maps generic defects to retryable/unknown; Gemini exposes raw SDK/parsing failures; summary handlers make every exception retryable.

- [ ] **Step 3: Migrate S3 with narrow exception scopes**

Remove `StorageProviderError`. Replace `_raise_provider_error` with a pure translator that handles only MinIO exception families, `TimeoutError`, connection errors, and response-parsing validation. Re-raise unrelated `TypeError`/`RuntimeError`. Handle semantic missing-object success before calling the generic HTTP helper. Preserve `StorageConfigurationError`.

- [ ] **Step 4: Migrate Gemini and summary consumers**

Translate known `google.genai.errors.APIError` status families and transport timeouts to `ProviderFailure`. Translate malformed provider responses to terminal validation. Keep missing local configuration/import as an explicit terminal configuration outcome without raw messages. Narrow summary worker/service catches to `ProviderFailure`; allow untranslated defects to reach the final internal-defect path.

- [ ] **Step 5: Run GREEN, privacy/mutation checks, Ruff, and mypy**

Prove mutations that catch all `Exception`, retry malformed summaries forever, or expose transcript/provider bodies fail tests. Run focused suites plus recording-reconciliation tests that use storage.

- [ ] **Step 6: Commit the passing storage/summary migration**

```bash
git add apps/api/app/providers/storage apps/api/app/providers/summaries \
  apps/api/app/services/summary_service.py apps/api/app/workers/jobs/outbox_topics.py \
  apps/api/app/workers/jobs/recording_reconciliation.py apps/api/tests
git commit -m "refactor(api): type storage and summary failures"
```

---

### Task 5: Migrate LiveKit recording and dispatch boundaries

**Files:**
- Modify: `apps/api/app/providers/livekit_recording/base.py`
- Modify: `apps/api/app/providers/livekit_recording/livekit.py`
- Modify: `apps/api/app/providers/livekit_dispatch/base.py`
- Modify: `apps/api/app/providers/livekit_dispatch/livekit.py`
- Modify: `apps/api/app/services/livekit_dispatch_service.py`
- Modify: `apps/api/app/services/livekit_recording_service.py`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py`
- Modify: `apps/api/app/workers/jobs/recording_reconciliation.py`
- Modify: `apps/api/tests/providers/test_livekit_recording_provider.py`
- Modify: `apps/api/tests/providers/test_livekit_dispatch_provider.py`
- Modify: `apps/api/tests/services/test_livekit_recording_service.py`
- Modify: `apps/api/tests/livekit/test_dispatch_service.py`
- Modify: `apps/api/tests/livekit/test_durable_dispatch_service.py`
- Modify: `apps/api/tests/workers/test_livekit_dispatch_outbox.py`
- Modify: `apps/api/tests/workers/test_forwarding_verification_dispatch_outbox.py`
- Modify: `apps/api/tests/workers/test_post_call_outbox_handlers.py`
- Modify: `apps/api/tests/workers/test_recording_reconciliation.py`

**Interfaces:**
- Consumes: shared failure and bounded `start_outcome` context.
- Produces: both LiveKit adapters translate known `TwirpError` codes/statuses and transport errors; malformed responses are terminal validation; arbitrary defects propagate.

- [ ] **Step 1: Write failing LiveKit mapping, reconciliation, defect, and cancellation tests**

Table-drive every `TwirpErrorCode`: deadline exceeded → retryable timeout; resource exhausted → retryable rate-limited; unavailable/internal → retryable unavailable; unauthenticated/permission denied → terminal authentication; already exists/aborted → terminal conflict; not found → terminal not-found; invalid argument/malformed/failed precondition/out of range/bad route/unimplemented → terminal validation; unknown → retryable unknown; canceled/data loss → terminal unknown.

For recording starts, assert `context == {"start_outcome": "not_started"}` before a provider acceptance boundary and `{"start_outcome": "unknown"}` after an ambiguous accepted/request boundary. Assert the context is preserved through dispatch lifecycle persistence.

Change tests that currently expect arbitrary `RuntimeError`, accessor `TypeError`, or invariant failures to become retryable provider failures: they must now propagate as defects. Add cancellation tests for list/create/start/stop.

- [ ] **Step 2: Verify RED**

Run LiveKit adapter/service/outbox/reconciliation suites. Expected RED: dispatch has no typed translation; recording catch-alls convert injected defects; legacy recording exception is concrete-adapter-local.

- [ ] **Step 3: Migrate adapters and preserve exact recovery semantics**

Remove `LiveKitRecordingProviderError`. Translate known LiveKit SDK/transport failures using a small shared LiveKit classifier. Wrap only SDK call sites and response parsing—not the whole method. Keep create-then-timeout list/reconcile behavior only for retryable/ambiguous `ProviderFailure`; terminal failures and untranslated defects bypass reconciliation retries.

- [ ] **Step 4: Migrate services/workers and narrow reconciliation catches**

Read `start_outcome` from bounded context. Catch `ProviderFailure` in provider-unavailable recovery paths. Let internal defects escape best-effort recording loops and the recording outbox wrapper; do not convert them to non-exhausting `recording_unresolved` retries. Preserve domain conflicts/identity reconciliation and current durable recording error codes.

- [ ] **Step 5: Run GREEN, mutation checks, Ruff, and mypy**

Mutate ambiguous start outcome, Twirp status mapping, create reconciliation gating, malformed response mapping, and an injected internal TypeError; each must fail a named test.

- [ ] **Step 6: Commit the passing LiveKit migration**

```bash
git add apps/api/app/providers/livekit_recording apps/api/app/providers/livekit_dispatch \
  apps/api/app/services/livekit_dispatch_service.py \
  apps/api/app/services/livekit_recording_service.py \
  apps/api/app/workers/jobs/outbox_topics.py \
  apps/api/app/workers/jobs/recording_reconciliation.py apps/api/tests
git commit -m "refactor(api): unify LiveKit provider failures"
```

---

### Task 6: Cut over the outbox internal-defect policy and close 7A

**Files:**
- Modify: `apps/api/app/workers/jobs/outbox_delivery.py`
- Modify: `apps/api/tests/integration/test_outbox_delivery.py`
- Modify: `apps/api/tests/integration/test_account_deactivation_concurrency.py`
- Modify: `apps/api/tests/integration/test_recording_egress_concurrency.py`
- Modify: `apps/api/tests/livekit/test_durable_dispatch_service.py`
- Modify: `apps/api/tests/services/test_safe_service_exceptions.py`
- Modify: `apps/api/tests/workers/test_individual_jobs.py`
- Modify: `apps/api/tests/workers/test_livekit_dispatch_outbox.py`
- Modify: `apps/api/tests/workers/test_post_call_outbox_handlers.py`
- Modify: `apps/api/tests/workers/test_phone_routing_readiness.py`
- Modify: `apps/api/tests/workers/test_provider_cleanup.py`
- Modify: `apps/api/tests/workers/test_account_deactivation.py`
- Modify: `apps/api/tests/workers/test_forwarding_verification_dispatch_outbox.py`
- Modify: `apps/api/tests/test_redaction.py`
- Modify: `apps/api/tests/test_observability.py`
- Modify: `docs/engineering/2026-07-30-agent-api-review-decisions.md`

**Interfaces:**
- Consumes: every active provider family migrated in Tasks 2-5.
- Produces: root classifier with four explicit branches: `OutboxDeliveryError`, `OutboxPayloadError`, `ProviderFailure`, and untranslated internal defect.

- [ ] **Step 1: Write failing durable retry and privacy tests**

Replace the raw-`RuntimeError` provider-retry test with three independent behaviors:

```python
async def retryable_handler(_ctx: dict, _event: OutboxEvent) -> None:
    raise ProviderFailure(
        provider="telnyx",
        operation="disable_number",
        disposition="retryable",
        error_class="unavailable",
    )


async def terminal_handler(_ctx: dict, _event: OutboxEvent) -> None:
    raise ProviderFailure(
        provider="telnyx",
        operation="disable_number",
        disposition="terminal",
        error_class="authentication",
    )


async def defective_handler(_ctx: dict, _event: OutboxEvent) -> None:
    raise TypeError("TOKEN_PHONE_BODY_SENTINEL")


async def cancelled_handler(_ctx: dict, _event: OutboxEvent) -> None:
    raise asyncio.CancelledError
```

Use the existing `_add_event` and `outbox_session_factory` fixtures around those exact handlers. The retryable handler is invoked six times and ends with `provider_retryable`; the terminal and defective handlers are invoked once and end with `provider_terminal` and `internal_defect`; cancellation propagates, writes no failure code, and the event is reclaimable after its existing processing lease expires.

Add end-to-end redaction sentinels for raw message, nested metadata, bearer/API token, French phone, and provider response body. Assert absence from exception display, logs, metrics/spans, alert fields, and persisted outbox fields.

- [ ] **Step 2: Verify RED**

Expected RED: `_classify_error` still maps untranslated exceptions to `provider_retryable`; `internal_defect` is not allow-listed; high-severity safe alert is absent.

- [ ] **Step 3: Implement the explicit root classifier**

```python
def _classify_error(error: Exception) -> tuple[str, bool, bool]:
    if isinstance(error, OutboxDeliveryError):
        return error.error_code, error.retryable, error.exhaustible
    if isinstance(error, OutboxPayloadError):
        return "invalid_payload", False, True
    if isinstance(error, ProviderFailure):
        return (
            "provider_retryable" if error.retryable else "provider_terminal",
            error.retryable,
            True,
        )
    return "internal_defect", False, True
```

Add `internal_defect` to safe durable/error-class maps. In the catch block, emit one safe `ERROR`/`CRITICAL` diagnostic for untranslated defects using fixed event/operation/provider labels and exception type only. Do not attach `exc_info`, cause, args, payload, or response. Do not add internal-defect retries.

Now that every active adapter is migrated, update `Observability.provider_operation` to label `ProviderFailure` as failure kind `provider` and any other caught `Exception` as `internal`. Treat `asyncio.CancelledError` as outcome `cancelled`, re-raise it unchanged, and emit neither an internal-defect alert nor a provider-error count for cancellation. Tests must assert the exact bounded metric/span labels and absence of private exception content.

- [ ] **Step 4: Prove the legacy vocabularies are gone and agent transport remains separate**

Run scoped searches:

```bash
rg -n "TelephonyProviderError|SubscriptionProviderError|StorageProviderError|CarrierLookupError|LiveKitRecordingProviderError|BillingSessionProviderError" \
  apps/api/app apps/api/tests

rg -n "TranscriptAppendRetryableError|TranscriptAppendPermanentError|CallCompletionRetryableError" \
  apps/agent/agent/api_client.py apps/agent/tests
```

Expected: first command has no results; second still shows the independent agent transport types/tests. Search every provider adapter for `except Exception`; inspect each remaining occurrence and keep only blocks that re-raise unchanged, isolate telemetry cleanup, or translate a precisely bounded response-parsing region.

- [ ] **Step 5: Run focused API verification**

Run all provider, billing, telephony, outbox, account-deactivation, cleanup, provisioning, recording, observability, logging, and redaction suites with the controlled environment. Run Ruff and mypy. Run `uv lock --check`; no dependency or lockfile change is expected.

- [ ] **Step 6: Run full API and agent regression gates**

Start only exact disposable PostgreSQL/Redis containers chosen for this plan. Run the complete API suite under controlled `APP_ENV=test`, database, Redis, Clerk, and uv cache variables. Run the complete agent suite separately with its existing environment. Realtime remains false throughout. Stop/remove only those exact containers and verify they are absent.

- [ ] **Step 7: Update the review ledger with measured evidence**

Change Issue 7 status to `Accepted; implemented`. Record:

- exact commits and touched provider families;
- full API/agent test counts;
- Ruff, mypy, and lockfile results;
- retryable/terminal/internal-defect/cancellation evidence;
- privacy/redaction evidence;
- confirmation that agent transport errors, realtime, deployment, dependencies, and database schema did not change.

- [ ] **Step 8: Commit the verified cutover**

```bash
git add apps/api/app apps/api/tests docs/engineering/2026-07-30-agent-api-review-decisions.md
git commit -m "fix(api): separate provider failures from internal defects"
```

---

## Final Acceptance Checklist

- [ ] One API provider failure vocabulary remains; no legacy provider exception family remains.
- [ ] Every active adapter has table-driven known-exception/status mapping tests.
- [ ] Malformed external responses are terminal validation failures.
- [ ] Arbitrary `TypeError`, invariant failure, and unrecognized local exception are internal defects, not provider failures.
- [ ] Cancellation propagates unchanged at adapter and outbox boundaries.
- [ ] Raw messages, metadata, tokens, phone numbers, and provider bodies never enter exception display, logs, telemetry, alerts, or durable error fields.
- [ ] Retryable provider failures use the bounded provider retry schedule.
- [ ] Terminal provider failures and internal defects stop on the first attempt.
- [ ] Existing explicitly non-exhausting domain retries remain explicit and cannot be reached by internal defects.
- [ ] Agent transport error types remain separate and fully tested.
- [ ] Full API and agent suites, Ruff, mypy, and lockfile validation pass.
- [ ] Realtime remains disabled; no deploy, push, PR, dependency, lockfile, or database migration change occurred.
