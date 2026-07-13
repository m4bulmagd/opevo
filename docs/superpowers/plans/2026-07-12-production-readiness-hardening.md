# Production Readiness Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing France-first inbound voice-assistant SaaS safe, durable, observable, compliant, and supportable for a controlled 5–10 customer production beta.

**Architecture:** Keep the FastAPI, PostgreSQL, Redis/ARQ, LiveKit agent, provider-adapter, and Next.js boundaries. Move all money and access decisions into locked PostgreSQL transactions, publish external work through a transactional outbox, persist call progress during the call, and enforce release gates before enabling production traffic.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, Redis 5, ARQ, Stripe, Telnyx, LiveKit Agents 1.4.4, S3-compatible storage, Next.js 16, React 19, TypeScript 5.9, Vitest, Pytest, GitHub Actions

## Global Constraints

- Preserve the France-only, inbound-only, one-user/one-agent, `starter`-only, `stt_llm_tts` launch scope.
- PostgreSQL is authoritative for subscription access, minute balance, call ownership, call state, transcript state, and provider-operation intent.
- Redis locks and job IDs improve efficiency but never replace database constraints or transactions.
- Every behavior change starts with a failing test, then minimal implementation, then the targeted suite, then the full affected application suite.
- Do not log prompts, knowledge-base contents, transcript text, full phone numbers, authorization headers, webhook signatures, or provider credentials.
- Do not perform Stripe, Telnyx, LiveKit, Firebase, or storage side effects before the local transaction that records the intent commits.
- Production must fail closed when required configuration is absent; development may use documented local defaults.
- Database migrations must be forward-only, data-safe, and tested against PostgreSQL before deployment.
- No public launch occurs until every gate in `docs/superpowers/specs/2026-07-12-production-readiness-hardening-design.md` passes.
- Legal text and legal-basis decisions require qualified French/EU counsel approval; engineering implements only approved copy and behavior.

---

## Program Sequence and Stop Gates

| Order | Workstream | Depends on | Exit gate |
|---:|---|---|---|
| 0 | Credential containment | None | All exposed credentials rotated and old values revoked |
| 1 | Security and production configuration | Gate 0 | Production starts fail-closed and logs contain no sensitive content |
| 2 | Billing, idempotency, and usage integrity | Gate 1 | Webhook replay and concurrent calls cannot duplicate or overspend |
| 3 | Durable calls and provider consistency | Gate 2 | Agent termination and provider retries do not lose or corrupt calls |
| 4 | Operations, CI, storage, and recovery | Gate 3 | Deployments, alerts, backup restore, and rollback are proven |
| 5 | Compliance and product completion | Gate 4 | French UX, data rights, legal surfaces, accessibility, and performance pass |
| 6 | Staging certification and controlled beta | Gate 5 | Three clean staging journeys and failure drills pass |

Stop at any failed gate. Do not begin public customer acquisition while a previous gate remains open.

## File Map

### Security and configuration

- Create: `docs/runbooks/credential-rotation.md` — exact credential inventory, owner, rotation evidence, and revocation checks.
- Create: `apps/api/app/core/runtime_validation.py` — process-specific production configuration validation.
- Create: `apps/agent/agent/runtime_validation.py` — voice-agent production configuration validation.
- Create: `apps/api/app/core/redaction.py` — structured redaction helpers and logging filter.
- Modify: `apps/api/app/core/config.py`, `apps/agent/agent/config.py`, `apps/web/src/lib/auth/clerk-config.ts` — fail-closed production settings.
- Modify: `apps/agent/agent/pipeline_factory.py`, `apps/api/app/webhooks/livekit.py` — remove sensitive logs.

### Database integrity and outbox

- Create: `apps/api/alembic/versions/0007_add_production_integrity_constraints.py` — unique and check constraints.
- Create: `apps/api/alembic/versions/0008_add_outbox_and_call_lifecycle.py` — outbox and durable call state.
- Create: `apps/api/app/models/outbox_event.py` — durable provider-operation intent.
- Create: `apps/api/app/repositories/outbox_repository.py` — claim, complete, and retry outbox records.
- Create: `apps/api/app/services/usage_accounting_service.py` — authoritative grants and call debits.
- Create: `apps/api/app/services/subscription_access_policy.py` — one access decision used by billing, onboarding, and dispatch.
- Create: `apps/api/app/services/outbox_service.py` — add events inside caller transactions.
- Create: `apps/api/app/workers/jobs/outbox_delivery.py` — deliver committed provider operations.

### Durable voice lifecycle

- Create: `apps/api/app/schemas/agent_runtime.py` — transcript append and completion contracts without caller-supplied balance authority.
- Create: `apps/api/app/services/transcript_service.py` — append-only transcript persistence.
- Create: `apps/api/app/services/call_reconciliation_service.py` — find and recover expired call states.
- Create: `apps/api/app/workers/jobs/call_reconciliation.py` — scheduled reconciliation execution.
- Modify: `apps/agent/agent/api_client.py`, `apps/agent/agent/session_runtime.py` — incremental transcript flush and retry-safe completion.
- Modify: `apps/api/app/services/call_lifecycle_service.py` — locked, state-driven finalization.

### Operations and release

- Create: `.github/workflows/ci.yml` — API, agent, web, migration, build, and scan gates.
- Create: `apps/api/app/routers/readiness.py` — dependency-aware readiness endpoint.
- Create: `docs/runbooks/deploy.md`, `rollback.md`, `backup-restore.md`, `provider-outage.md`, `incident-response.md` — operator procedures.
- Create: `docs/architecture/production-deployment.md` — approved hosting topology and data residency.
- Modify: `apps/api/Dockerfile`, `apps/agent/Dockerfile`, `apps/web/Dockerfile` — non-root production images.

### Compliance and product

- Create: `apps/api/alembic/versions/0009_add_data_lifecycle_records.py` — deletion/export requests and access audit.
- Create: `apps/api/app/models/data_request.py`, `recording_access_event.py` — auditable privacy operations.
- Create: `apps/api/app/services/data_lifecycle_service.py` — export and purge orchestration.
- Create: `apps/web/src/app/privacy/page.tsx`, `terms/page.tsx`, `legal/page.tsx`, `support/page.tsx` — approved legal and support surfaces.
- Create: `apps/web/src/i18n/fr.ts` — French product copy source.
- Modify: call, billing, agent, onboarding, navigation, and layout components to complete the customer workflow.

## Workstream 0: Credential Containment

### Task 1: Rotate and prove revocation of exposed credentials

**Files:**
- Create: `docs/runbooks/credential-rotation.md`
- Modify locally, never commit: `apps/api/.env`, `apps/agent/.env`, `apps/web/.env`
- Verify: provider dashboards and local startup

**Interfaces:**
- Consumes: current provider accounts and the local environment-variable names.
- Produces: a runbook containing credential name, rotation date, operator, revocation evidence, and smoke-test result without secret values.

- [ ] **Step 1: Record the credential inventory without values**

Create the runbook table with these exact rows: Stripe API, Stripe webhook, Clerk publishable, Clerk secret, Clerk webhook, LiveKit key/secret, Telnyx API, Gemini, Speechmatics, ElevenLabs, Mistral, S3 access/secret, agent internal token, dispatch JWT secret.

- [ ] **Step 2: Rotate each credential at its provider**

For every row, issue a new value, place it only in the approved secret store or local ignored `.env`, restart the affected process, run its smoke check, revoke the old value, and record `rotated`, `verified`, and `revoked` timestamps.

- [ ] **Step 3: Restrict local secret files**

Run:

```bash
chmod 600 apps/api/.env apps/agent/.env apps/web/.env
stat -c '%a %n' apps/api/.env apps/agent/.env apps/web/.env
```

Expected: every file reports mode `600`.

- [ ] **Step 4: Confirm secret files are ignored and absent from history**

Run:

```bash
git check-ignore apps/api/.env apps/agent/.env apps/web/.env
git log --all -- apps/api/.env apps/agent/.env apps/web/.env
```

Expected: all three paths are ignored and the history command prints no commits.

- [ ] **Step 5: Commit only the value-free runbook**

```bash
git add docs/runbooks/credential-rotation.md
git commit -m "docs: add credential rotation runbook"
```

Gate 0 passes only after every old credential is revoked, not merely after new values are generated.

## Workstream 1: Security and Production Configuration

### Task 2: Add fail-closed process-specific configuration validation

**Files:**
- Create: `apps/api/app/core/runtime_validation.py`
- Create: `apps/agent/agent/runtime_validation.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/agent/agent/main.py`
- Modify: `apps/web/src/lib/auth/clerk-config.ts`
- Modify: `apps/web/src/proxy.ts`
- Test: `apps/api/tests/test_deployment_readiness.py`
- Create: `apps/agent/tests/test_runtime_validation.py`
- Create: `apps/web/tests/lib/clerk-config.test.ts`

**Interfaces:**
- Produces: `validate_api_runtime(settings: Settings) -> None`.
- Produces: `validate_agent_runtime(settings: AgentSettings) -> None`.
- Both raise `RuntimeError` listing missing variable names, never their values.

- [ ] **Step 1: Write failing API validation tests**

Add tests asserting that production requires database, Redis, Clerk, Stripe webhook, LiveKit, Telnyx, storage, dispatch-JWT, and summary-provider configuration, while development accepts fake providers.

```python
def test_production_rejects_missing_dispatch_secret(base_settings):
    settings = base_settings.model_copy(
        update={"app_env": "production", "agent_dispatch_jwt_secret": None}
    )
    with pytest.raises(RuntimeError, match="AGENT_DISPATCH_JWT_SECRET"):
        validate_api_runtime(settings)
```

- [ ] **Step 2: Write failing agent and web validation tests**

```python
def test_agent_production_rejects_debug_streams(agent_settings):
    settings = agent_settings.model_copy(
        update={"app_env": "production", "agent_debug_streams": True}
    )
    with pytest.raises(RuntimeError, match="AGENT_DEBUG_STREAMS"):
        validate_agent_runtime(settings)
```

```ts
it("throws when production Clerk configuration is absent", () => {
  expect(() => requireProductionClerkConfig({ nodeEnv: "production", publishableKey: "" }))
    .toThrow("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY");
});
```

- [ ] **Step 3: Run the targeted tests and verify failure**

```bash
cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/test_deployment_readiness.py -v
cd apps/agent && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/test_runtime_validation.py -v
cd apps/web && npm run test -- --run tests/lib/clerk-config.test.ts
```

Expected: failure because the validators do not exist.

- [ ] **Step 4: Implement the validators and startup hooks**

Use a shared pattern that accumulates every missing name:

```python
from collections.abc import Sequence


def _require(settings, names: Sequence[str]) -> None:
    missing = [name.upper() for name in names if not getattr(settings, name)]
    if missing:
        raise RuntimeError(f"Missing required production settings: {', '.join(missing)}")
```

Call validation before the API or agent opens network listeners. In the web application, throw during server initialization when production Clerk or backend configuration is absent; remove the production pass-through behavior from `proxy.ts`.

- [ ] **Step 5: Run targeted and full tests**

```bash
cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -q
cd apps/agent && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -q
cd apps/web && npm run test -- --run && npm run build
```

Expected: all tests and the production build pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/core/runtime_validation.py apps/api/app/main.py apps/api/tests/test_deployment_readiness.py apps/agent/agent/runtime_validation.py apps/agent/agent/main.py apps/agent/tests/test_runtime_validation.py apps/web/src/lib/auth/clerk-config.ts apps/web/src/proxy.ts apps/web/tests/lib/clerk-config.test.ts
git commit -m "security: fail closed on invalid production configuration"
```

### Task 3: Remove sensitive logs and add deterministic redaction

**Files:**
- Create: `apps/api/app/core/redaction.py`
- Modify: `apps/api/app/core/logging.py`
- Modify: `apps/api/app/webhooks/livekit.py`
- Modify: `apps/api/app/services/livekit_dispatch_service.py`
- Modify: `apps/agent/agent/pipeline_factory.py`
- Modify: `apps/agent/agent/session_runtime.py`
- Create: `apps/api/tests/test_redaction.py`
- Modify: `apps/agent/tests/test_debug_streams.py`

**Interfaces:**
- Produces: `redact_phone(value: str | None) -> str | None`, returning `+33******12`-style output.
- Produces: `SafeExtraFilter(logging.Filter)` that removes forbidden keys from structured log records.

- [ ] **Step 1: Write failing redaction tests**

```python
def test_redact_phone_keeps_country_and_last_two_digits():
    assert redact_phone("+33612345678") == "+33******78"

def test_redact_phone_handles_missing_value():
    assert redact_phone(None) is None
```

Add a log-capture test proving that `system_prompt`, `knowledge_base`, `transcript`, `authorization`, `signature`, and raw SIP attributes do not appear.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/test_redaction.py -v`

Expected: failure because redaction helpers do not exist.

- [ ] **Step 3: Implement redaction and delete raw content output**

Delete the prompt print in `pipeline_factory.py`. Log event names, opaque call IDs, redacted numbers, latency, status, and provider request IDs only. Do not log full webhook payloads or SIP attribute dictionaries.

- [ ] **Step 4: Verify forbidden-content absence**

Run:

```bash
rg -n 'print\(|system_prompt|knowledge_base|transcript|attributes=%s' apps/api/app apps/agent/agent
```

Expected: no output statement logs the value of these fields; schema and business references remain allowed.

- [ ] **Step 5: Run API and agent tests, then commit**

```bash
cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -q
cd ../agent && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -q
git add apps/api/app/core/redaction.py apps/api/app/core/logging.py apps/api/app/webhooks/livekit.py apps/api/app/services/livekit_dispatch_service.py apps/api/tests/test_redaction.py apps/agent/agent/pipeline_factory.py apps/agent/agent/session_runtime.py apps/agent/tests/test_debug_streams.py
git commit -m "security: redact sensitive call and provider logs"
```

## Workstream 2: Database, Billing, and Usage Integrity

### Task 4: Add database-enforced idempotency and ownership constraints

**Files:**
- Create: `apps/api/alembic/versions/0007_add_production_integrity_constraints.py`
- Modify: `apps/api/app/models/webhook_event.py`
- Modify: `apps/api/app/models/usage_ledger.py`
- Modify: `apps/api/app/models/call_message.py`
- Modify: `apps/api/app/models/call.py`
- Modify: `apps/api/app/models/subscription.py`
- Modify: `apps/api/app/repositories/webhook_event_repository.py`
- Create: `apps/api/tests/integration/test_integrity_constraints.py`
- Modify: `apps/api/tests/test_migration_revision_ids.py`

**Interfaces:**
- Produces unique constraints/indexes `uq_webhook_events_provider_external_event_id`, `uq_usage_ledgers_call_event_type`, `uq_usage_ledgers_event_source`, `uq_call_messages_call_sequence`, `uq_calls_user_active`, and `uq_subscriptions_user_id`.
- `UsageLedger.source_id: str | None` identifies an invoice, adjustment, or other external grant source.
- `uq_calls_user_active` permits at most one `pending`, `connected`, `ending`, or `finalizing` call per user during the MVP.
- `WebhookEventRepository.record_if_new(provider: str, external_event_id: str, event_type: str, payload: dict) -> bool` uses insert-and-catch semantics, not check-then-insert.

- [ ] **Step 1: Add PostgreSQL integration tests that race duplicate inserts**

The test opens two independent sessions, inserts the same provider event and call debit, commits concurrently, and asserts exactly one durable row for each identity.

```python
assert await count_rows(WebhookEvent, provider="stripe", external_event_id="evt_same") == 1
assert await count_rows(UsageLedger, call_id=call.id, event_type="call_completed") == 1
```

Add a test that creates two active calls for one user and asserts the second insert fails, while completed calls do not block the next inbound call.

- [ ] **Step 2: Run against PostgreSQL and verify failure**

Run: `cd apps/api && TEST_DATABASE_URL="$DATABASE_URL" UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/integration/test_integrity_constraints.py -v`

Expected: duplicates can currently be inserted or the test reports missing constraints.

- [ ] **Step 3: Add a data preflight to the migration**

Before each unique constraint, query duplicates and abort with a message naming only duplicate IDs/counts. Do not silently delete financial or transcript data.

- [ ] **Step 4: Add constraints and repository conflict handling**

The migration must include:

```python
op.create_unique_constraint(
    "uq_webhook_events_provider_external_event_id",
    "webhook_events",
    ["provider", "external_event_id"],
)
op.create_index(
    "uq_usage_ledgers_call_event_type",
    "usage_ledgers",
    ["call_id", "event_type"],
    unique=True,
    postgresql_where=sa.text("call_id IS NOT NULL"),
)
op.create_index(
    "uq_usage_ledgers_event_source",
    "usage_ledgers",
    ["event_type", "source_id"],
    unique=True,
    postgresql_where=sa.text("source_id IS NOT NULL"),
)
op.create_unique_constraint(
    "uq_call_messages_call_sequence",
    "call_messages",
    ["call_id", "sequence_number"],
)
op.create_unique_constraint("uq_subscriptions_user_id", "subscriptions", ["user_id"])
op.create_index(
    "uq_calls_user_active",
    "calls",
    ["user_id"],
    unique=True,
    postgresql_where=sa.text(
        "status IN ('pending', 'connected', 'ending', 'finalizing')"
    ),
)
```

Add nonnegative checks for `allocated_minutes`, `duration_seconds`, and `minutes_charged`. Add exact allowed-state checks for subscription, call, outbox, provisioning, notification, and data-request statuses when their owning task introduces the final state set.

- [ ] **Step 5: Test upgrade and downgrade on a disposable PostgreSQL database**

```bash
cd apps/api
DATABASE_URL="$TEST_DATABASE_URL" UV_CACHE_DIR=/tmp/uv-cache uv run alembic upgrade head
DATABASE_URL="$TEST_DATABASE_URL" UV_CACHE_DIR=/tmp/uv-cache uv run alembic downgrade 0006
DATABASE_URL="$TEST_DATABASE_URL" UV_CACHE_DIR=/tmp/uv-cache uv run alembic upgrade head
```

Expected: all commands succeed on clean and representative staging snapshots.

- [ ] **Step 6: Commit**

```bash
git add apps/api/alembic/versions/0007_add_production_integrity_constraints.py apps/api/app/models apps/api/app/repositories/webhook_event_repository.py apps/api/tests/integration/test_integrity_constraints.py apps/api/tests/test_migration_revision_ids.py
git commit -m "fix: enforce billing and webhook idempotency in postgres"
```

### Task 5: Centralize subscription access and implement the complete Stripe lifecycle

**Files:**
- Create: `apps/api/app/services/subscription_access_policy.py`
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/app/core/webhook_verifier.py`
- Modify: `apps/api/app/webhooks/stripe.py`
- Modify: `apps/api/app/webhooks/clerk.py`
- Modify: `apps/api/app/services/billing_service.py`
- Modify: `apps/api/app/services/billing_session_service.py`
- Modify: `apps/api/app/services/onboarding_service.py`
- Modify: `apps/api/app/repositories/subscription_repository.py`
- Modify: `apps/api/tests/billing/test_stripe_webhooks.py`
- Modify: `apps/api/tests/services/test_onboarding_service.py`
- Create: `apps/api/tests/services/test_subscription_access_policy.py`
- Create: `apps/api/tests/test_webhook_verifier.py`
- Modify: `apps/api/tests/services/test_billing_session_service.py`

**Interfaces:**
- Produces: `SubscriptionAccessPolicy.can_route(status: str, period_end: datetime | None) -> bool`.
- Produces: `SubscriptionAccessPolicy.should_grant_invoice(invoice_status: str, paid: bool) -> bool`.
- Recognized statuses: `trialing`, `active`, `past_due`, `unpaid`, `canceled`, `incomplete`, `incomplete_expired`, `paused`.

- [ ] **Step 1: Write policy and webhook matrix tests**

```python
@pytest.mark.parametrize("status,allowed", [
    ("active", True),
    ("trialing", True),
    ("past_due", False),
    ("unpaid", False),
    ("canceled", False),
    ("incomplete", False),
    ("incomplete_expired", False),
    ("paused", False),
])
def test_can_route_matrix(status, allowed):
    assert SubscriptionAccessPolicy.can_route(status, None) is allowed
```

Add webhook tests for `customer.subscription.created`, `updated`, `deleted`, `invoice.paid`, `invoice.payment_failed`, and repeated delivery of every event ID.

Add signature tests for a valid event, a timestamp older than five minutes, a future timestamp beyond five minutes, a malformed signature component, and a wrong secret. Every invalid case returns `400`, never `500`.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/services/test_subscription_access_policy.py tests/billing/test_stripe_webhooks.py -v`

Expected: unsupported lifecycle events fail.

- [ ] **Step 3: Implement explicit event dispatch**

Replace the two-branch handler with a constant handler map. Unknown Stripe events are recorded and acknowledged without changing state. Reject lookup keys other than `starter`; remove `standard` from `PLAN_MINUTES` and production examples.

Use Stripe's SDK verifier for Stripe events and Svix's verifier for Clerk events. Keep LiveKit's SDK `WebhookReceiver`. Remove custom parsing after the provider verifiers cover every webhook.

- [ ] **Step 4: Disable local access on non-routing statuses**

Persist the Stripe status and create a durable `disable_phone_routing` intent when a subscription becomes non-routing. Do not call Telnyx inside the webhook transaction; Task 7 delivers the intent through the outbox.

Configure one server-owned billing-portal return URL. Reject caller-supplied return URLs outside the configured application origin; the API must not operate as an open redirect.

- [ ] **Step 5: Remove same-session `asyncio.gather` calls**

Run repository operations sequentially in `BillingQueryService` and `OnboardingService`, or replace them with one joined read query. Add a PostgreSQL test that calls both services with a real `AsyncSession`.

- [ ] **Step 6: Run tests and commit**

```bash
cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -q
git add apps/api/pyproject.toml apps/api/uv.lock apps/api/app/services/subscription_access_policy.py apps/api/app/core/webhook_verifier.py apps/api/app/webhooks/stripe.py apps/api/app/webhooks/clerk.py apps/api/app/services/billing_service.py apps/api/app/services/billing_session_service.py apps/api/app/services/billing_query_service.py apps/api/app/services/onboarding_service.py apps/api/app/repositories/subscription_repository.py apps/api/tests/billing/test_stripe_webhooks.py apps/api/tests/services apps/api/tests/test_webhook_verifier.py
git commit -m "fix: enforce complete subscription access lifecycle"
```

### Task 6: Make minute grants and call debits transactionally authoritative

**Files:**
- Create: `apps/api/app/services/usage_accounting_service.py`
- Modify: `apps/api/app/repositories/usage_repository.py`
- Modify: `apps/api/app/repositories/call_repository.py`
- Modify: `apps/api/app/services/billing_service.py`
- Modify: `apps/api/app/services/call_lifecycle_service.py`
- Modify: `apps/api/app/schemas/calls.py`
- Modify: `apps/api/app/routers/agent.py`
- Create: `apps/api/tests/integration/test_usage_concurrency.py`
- Modify: `apps/api/tests/calls/test_call_lifecycle.py`
- Modify: `apps/api/tests/agent/test_call_completion.py`

**Interfaces:**
- Produces: `UsageAccountingService.grant_invoice(user_id: UUID, invoice_id: str, minutes: int) -> UsageLedger`, persisting `source_id=invoice_id`.
- Produces: `UsageAccountingService.debit_call(call_id: UUID, duration_seconds: int) -> UsageDebitResult`.
- `UsageDebitResult` contains `user_id`, `minutes_charged`, `balance_before`, `balance_after`, and `already_debited`.
- Completion requests contain `duration_seconds`, transcript metadata, and recording metadata; they do not contain authoritative `user_id` or `minutes_remaining`.

- [ ] **Step 1: Write ownership and concurrent-debit tests**

Create one customer with two simultaneous calls and a two-minute balance. Finalize both in independent PostgreSQL transactions and assert:

```python
assert sorted([first.balance_after, second.balance_after]) == [0, 1]
assert await usage_repository.get_current_balance(user_id=user.id) == 0
assert await count_call_debits(call_ids=[call_a.id, call_b.id]) == 2
```

Add a test that submits another customer’s `user_id`; the field must be rejected by the schema or ignored, and the call owner must be charged.

Add a grant test that processes two distinct Stripe events for the same invoice ID and asserts one ledger grant through `uq_usage_ledgers_event_source`.

- [ ] **Step 2: Run against PostgreSQL and verify failure**

Run: `cd apps/api && TEST_DATABASE_URL="$DATABASE_URL" UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/integration/test_usage_concurrency.py tests/calls/test_call_lifecycle.py -v`

Expected: stale-balance or ownership assertions fail.

- [ ] **Step 3: Implement locked balance access**

Add repository methods using `with_for_update()` and deterministic ordering by `created_at DESC, id DESC`. Lock the subscription/user accounting scope before reading the latest balance and writing the next ledger entry.

- [ ] **Step 4: Implement idempotent debit behavior**

Within one transaction:

1. Lock the call.
2. Return its existing debit when `call_completed` already exists.
3. Derive `user_id` from the call.
4. Calculate `max(1, ceil(duration_seconds / 60))`.
5. Cap the debit at the available balance.
6. Insert the ledger row.
7. Return the authoritative result.

- [ ] **Step 5: Remove agent-supplied accounting fields**

Change `AgentCallCompletionRequest` and `AgentApiClient.complete_call()` so the JSON body no longer includes `user_id` or `minutes_remaining`. Retain the metadata balance snapshot only for in-call messaging.

- [ ] **Step 6: Run concurrency tests ten times**

```bash
cd apps/api
for run in $(seq 1 10); do TEST_DATABASE_URL="$DATABASE_URL" UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/integration/test_usage_concurrency.py -q || exit 1; done
```

Expected: all ten runs pass with identical ledger totals.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/services/usage_accounting_service.py apps/api/app/repositories/usage_repository.py apps/api/app/repositories/call_repository.py apps/api/app/services/billing_service.py apps/api/app/services/call_lifecycle_service.py apps/api/app/schemas/calls.py apps/api/app/routers/agent.py apps/api/tests/integration/test_usage_concurrency.py apps/api/tests/calls/test_call_lifecycle.py apps/api/tests/agent/test_call_completion.py
git commit -m "fix: make usage accounting authoritative and concurrency safe"
```

### Task 7: Add a transactional outbox for provider operations

**Files:**
- Create: `apps/api/alembic/versions/0008_add_outbox_and_call_lifecycle.py`
- Create: `apps/api/app/models/outbox_event.py`
- Modify: `apps/api/app/models/__init__.py`
- Create: `apps/api/app/repositories/outbox_repository.py`
- Create: `apps/api/app/services/outbox_service.py`
- Create: `apps/api/app/workers/jobs/outbox_delivery.py`
- Modify: `apps/api/app/workers/arq_worker.py`
- Modify: `apps/api/app/services/billing_service.py`
- Modify: `apps/api/app/workers/jobs/phone_provisioning.py`
- Modify: `apps/api/app/services/livekit_dispatch_service.py`
- Create: `apps/api/tests/integration/test_outbox_delivery.py`
- Modify: `apps/api/tests/workers/test_arq_worker.py`

**Interfaces:**
- Produces: `OutboxService.add(topic: str, aggregate_type: str, aggregate_id: UUID, idempotency_key: str, payload: dict) -> OutboxEvent`.
- Produces: `OutboxRepository.claim_batch(limit: int, now: datetime) -> list[OutboxEvent]` using `FOR UPDATE SKIP LOCKED`.
- Supported launch topics: `phone.provision`, `phone.enable`, `phone.disable`, `livekit.dispatch`, `recording.start`, `notification.send`.

- [ ] **Step 1: Write rollback and duplicate-delivery tests**

```python
async def test_outbox_event_rolls_back_with_business_transaction(session):
    await outbox.add(
        topic="phone.disable",
        aggregate_type="subscription",
        aggregate_id=subscription.id,
        idempotency_key="stripe:sub_updated:evt_123",
        payload={"user_id": str(user.id)},
    )
    await session.rollback()
    assert await outbox_repository.count() == 0
```

Add two-worker tests showing one event is claimed once and repeated delivery is safe.

- [ ] **Step 2: Run integration tests and verify failure**

Run: `cd apps/api && TEST_DATABASE_URL="$DATABASE_URL" TEST_REDIS_URL="$REDIS_URL" UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/integration/test_outbox_delivery.py -v`

Expected: failure because the outbox does not exist.

- [ ] **Step 3: Add the outbox schema**

The table contains:

```python
idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
topic: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
aggregate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
payload: Mapped[dict] = mapped_column(JSON, nullable=False)
status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 4: Implement claim, exponential retry, and terminal failure**

Retry at 10 seconds, 1 minute, 5 minutes, 30 minutes, and 2 hours. After the fifth failure, mark the event `failed` and emit an error metric. Never store raw provider error bodies in `last_error_code`.

- [ ] **Step 5: Replace pre-commit queue and provider calls**

Write outbox events inside the Stripe, provisioning, routing, dispatch, recording, and notification transactions. Schedule the generic delivery job only after commit. The scheduled reconciliation poller must also pick up pending events, so a process crash between commit and enqueue does not lose work.

- [ ] **Step 6: Run the API suite and commit**

```bash
cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -q
git add apps/api/alembic/versions/0008_add_outbox_and_call_lifecycle.py apps/api/app/models/outbox_event.py apps/api/app/models/__init__.py apps/api/app/repositories/outbox_repository.py apps/api/app/services/outbox_service.py apps/api/app/workers/jobs/outbox_delivery.py apps/api/app/workers/arq_worker.py apps/api/app/services/billing_service.py apps/api/app/workers/jobs/phone_provisioning.py apps/api/app/services/livekit_dispatch_service.py apps/api/tests/integration/test_outbox_delivery.py apps/api/tests/workers/test_arq_worker.py
git commit -m "feat: deliver provider operations through transactional outbox"
```

### Task 8: Enforce dispatch eligibility and call-scoped agent authentication

**Files:**
- Create: `apps/api/app/services/dispatch_eligibility_policy.py`
- Modify: `apps/api/app/core/dispatch_token.py`
- Modify: `apps/api/app/services/livekit_dispatch_service.py`
- Modify: `apps/api/app/routers/agent.py`
- Modify: `apps/api/app/schemas/livekit.py`
- Modify: `apps/agent/agent/api_client.py`
- Modify: `apps/agent/agent/schemas.py`
- Modify: `apps/api/tests/livekit/test_dispatch_service.py`
- Create: `apps/api/tests/services/test_dispatch_eligibility_policy.py`
- Modify: `apps/api/tests/auth/test_jwt_auth.py`
- Modify: `apps/api/tests/agent/test_call_completion.py`
- Modify: `apps/agent/tests/test_api_client.py`

**Interfaces:**
- Produces: `DispatchEligibilityPolicy.can_dispatch(subscription_status: str, balance: int, phone_active: bool, agent_enabled: bool, setup_complete: bool) -> bool`.
- Produces: `create_dispatch_token(call_id: str, user_id: str, agent_config_id: str) -> str`.
- Produces: `verify_dispatch_token(token: str, expected_call_id: str, expected_user_id: str | None = None) -> dict`.
- Dispatch eligibility requires active subscription, balance greater than zero, active assigned phone number, enabled and complete agent configuration, and matching called number.
- Dispatch rejects a second active call for the same user with a controlled busy response; the database partial unique index is the final race-safe guard.
- Recording starts only for the expected dispatched agent participant, never for an arbitrary non-SIP observer.

- [ ] **Step 1: Write a dispatch eligibility matrix**

```python
@pytest.mark.parametrize("subscription,balance,phone_active,agent_enabled,allowed", [
    ("active", 10, True, True, True),
    ("past_due", 10, True, True, False),
    ("active", 0, True, True, False),
    ("active", 10, False, True, False),
    ("active", 10, True, False, False),
])
def test_dispatch_requires_all_gates(
    subscription: str,
    balance: int,
    phone_active: bool,
    agent_enabled: bool,
    allowed: bool,
):
    result = DispatchEligibilityPolicy.can_dispatch(
        subscription_status=subscription,
        balance=balance,
        phone_active=phone_active,
        agent_enabled=agent_enabled,
        setup_complete=True,
    )
    assert result is allowed
```

Implement the test with repository fakes already used by `test_dispatch_service.py`; assert no call or outbox dispatch intent is created when denied.

Add a PostgreSQL race test that handles two inbound joins for the same user concurrently and asserts one pending call plus one controlled rejection. Add a webhook test proving an observer participant cannot start recording.

- [ ] **Step 2: Write JWT claim and fallback tests**

Assert that staging/production rejects the static token, a token for call A cannot complete call B, and a token with user B cannot append transcript or complete a call owned by user A.

- [ ] **Step 3: Run targeted tests and verify failure**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/services/test_dispatch_eligibility_policy.py tests/livekit/test_dispatch_service.py tests/auth/test_jwt_auth.py tests/agent/test_call_completion.py -v`

Expected: current fallback and missing eligibility checks fail.

- [ ] **Step 4: Implement strict token claims**

JWT payload:

```python
payload = {
    "call_id": call_id,
    "user_id": user_id,
    "agent_config_id": agent_config_id,
    "iat": now,
    "exp": now + settings.agent_dispatch_jwt_ttl_seconds,
}
```

Use `hmac.compare_digest` only for development static-token compatibility. In staging and production, require JWT configuration and reject fallback authentication.

- [ ] **Step 5: Commit dispatch intent after the call row**

Create the pending call and `livekit.dispatch` outbox event in one transaction. If delivery fails, set the call failure code through the outbox handler; never leave a silent pending call.

Record LiveKit event IDs in `webhook_events` before dispatch or recording logic. Identify the agent from the expected dispatch/participant identity and LiveKit participant kind, not merely from “not SIP.”

- [ ] **Step 6: Run API and agent suites, then commit**

```bash
cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -q
cd ../agent && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -q
git add apps/api/app/services/dispatch_eligibility_policy.py apps/api/app/core/dispatch_token.py apps/api/app/services/livekit_dispatch_service.py apps/api/app/routers/agent.py apps/api/app/schemas/livekit.py apps/api/tests/services/test_dispatch_eligibility_policy.py apps/api/tests/livekit/test_dispatch_service.py apps/api/tests/auth/test_jwt_auth.py apps/api/tests/agent/test_call_completion.py apps/agent/agent/api_client.py apps/agent/agent/schemas.py apps/agent/tests/test_api_client.py
git commit -m "security: scope agent access and enforce call dispatch gates"
```

## Workstream 3: Durable Calls and Provider Consistency

### Task 9: Persist transcript segments incrementally and idempotently

**Files:**
- Create: `apps/api/app/schemas/agent_runtime.py`
- Create: `apps/api/app/services/transcript_service.py`
- Modify: `apps/api/app/routers/agent.py`
- Modify: `apps/api/app/repositories/message_repository.py`
- Modify: `apps/agent/agent/api_client.py`
- Modify: `apps/agent/agent/session_runtime.py`
- Create: `apps/api/tests/agent/test_transcript_append.py`
- Modify: `apps/agent/tests/test_session_runtime.py`
- Modify: `apps/agent/tests/test_api_client.py`

**Interfaces:**
- Produces endpoint `POST /api/agent/calls/{call_id}/transcript`.
- Request: `{ "sequence_number": int, "speaker": "CALLER" | "AGENT", "text": str }`.
- Response: `{ "status": "stored" | "duplicate", "sequence_number": int }`.
- Produces: `AgentApiClient.append_transcript(call_id, dispatch_token, item) -> dict`.

- [ ] **Step 1: Write API idempotency and ownership tests**

Append sequence 1 twice and assert one row. Append sequence 2 with the wrong call token and assert `401`. Append text exceeding 4,000 Unicode code points and assert `422`.

- [ ] **Step 2: Write agent buffer tests**

Assert each accepted caller/agent segment receives a monotonically increasing sequence number, is sent by a background flusher, and remains in the bounded pending queue until acknowledged.

- [ ] **Step 3: Run targeted tests and verify failure**

```bash
cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/agent/test_transcript_append.py -v
cd ../agent && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/test_session_runtime.py tests/test_api_client.py -v
```

Expected: transcript endpoint and flusher behavior do not exist.

- [ ] **Step 4: Implement append-only persistence**

Validate the dispatch token against the call row, normalize the speaker enum, reject empty text, and insert by `(call_id, sequence_number)`. Convert the unique conflict into a `duplicate` response without modifying existing text.

- [ ] **Step 5: Implement the bounded agent flusher**

Use one background task per call, a maximum pending queue of 200 items, sequential delivery, and exponential retry capped at 10 seconds. Never block audio callbacks on the HTTP request. On finalization, wait up to five seconds for acknowledged flush, then send the remaining bounded transcript in the completion payload as a recovery path.

- [ ] **Step 6: Prove crash survival**

In the agent test, append three segments, acknowledge two, cancel the runtime task, and assert the API test database contains the two acknowledged segments. Restart finalization with all three segments and assert the unique constraint produces exactly three rows.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/schemas/agent_runtime.py apps/api/app/services/transcript_service.py apps/api/app/routers/agent.py apps/api/app/repositories/message_repository.py apps/api/tests/agent/test_transcript_append.py apps/agent/agent/api_client.py apps/agent/agent/session_runtime.py apps/agent/tests/test_session_runtime.py apps/agent/tests/test_api_client.py
git commit -m "feat: persist live transcripts incrementally"
```

### Task 10: Introduce a durable call state machine and reconciliation worker

**Files:**
- Modify: `apps/api/alembic/versions/0008_add_outbox_and_call_lifecycle.py`
- Modify: `apps/api/app/models/call.py`
- Modify: `apps/api/app/repositories/call_repository.py`
- Create: `apps/api/app/services/call_reconciliation_service.py`
- Create: `apps/api/app/workers/jobs/call_reconciliation.py`
- Modify: `apps/api/app/workers/arq_worker.py`
- Modify: `apps/api/app/services/call_lifecycle_service.py`
- Create: `apps/api/tests/services/test_call_reconciliation_service.py`
- Modify: `apps/api/tests/workers/test_lifecycle_edge_cases.py`
- Modify: `apps/api/tests/calls/test_call_lifecycle.py`

**Interfaces:**
- Call states: `pending`, `connected`, `ending`, `finalizing`, `completed`, `failed`.
- Produces: `CallRepository.transition(call_id, from_states: set[str], to_state: str, failure_code: str | None = None) -> Call` with a locked row.
- Produces: `CallReconciliationService.reconcile(now: datetime, limit: int = 100) -> ReconciliationResult`.

- [ ] **Step 1: Write legal-transition tests**

```python
@pytest.mark.parametrize("source,target", [
    ("pending", "connected"),
    ("connected", "ending"),
    ("ending", "finalizing"),
    ("finalizing", "completed"),
])
async def test_legal_call_transition(session, user, source: str, target: str):
    call = Call(user_id=user.id, status=source)
    session.add(call)
    await session.flush()

    result = await CallRepository(session).transition(
        call.id,
        from_states={source},
        to_state=target,
    )

    assert result.status == target
```

Assert `completed -> finalizing` and `failed -> connected` are rejected.

- [ ] **Step 2: Write stale-state reconciliation tests**

Cover pending dispatch older than two minutes, connected call without participant older than maximum duration plus two minutes, finalizing call older than five minutes, and completed call. Assert retries for recoverable states, explicit failure for terminal states, and no mutation of completed calls.

- [ ] **Step 3: Run tests and verify failure**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/services/test_call_reconciliation_service.py tests/workers/test_lifecycle_edge_cases.py -v`

Expected: state transitions and reconciliation do not exist.

- [ ] **Step 4: Add lifecycle fields**

Add `state_changed_at`, `finalization_attempt_count`, `last_reconciled_at`, and `failure_code`. Backfill existing `pending` and `completed` rows from their timestamps.

- [ ] **Step 5: Make finalization a short database transaction**

Transition to `finalizing`, debit usage, persist transcript recovery items, and enqueue summary/recording/notification outbox work. Do not hold a database or Redis lock while calling Gemini, S3, Firebase, Telnyx, or LiveKit.

- [ ] **Step 6: Register periodic reconciliation**

Run the job every minute. Claim at most 100 calls per execution with `SKIP LOCKED`, record metrics for recovered and failed calls, and cap per-call automated recovery at five attempts.

- [ ] **Step 7: Run API tests and commit**

```bash
cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -q
git add apps/api/alembic/versions/0008_add_outbox_and_call_lifecycle.py apps/api/app/models/call.py apps/api/app/repositories/call_repository.py apps/api/app/services/call_reconciliation_service.py apps/api/app/workers/jobs/call_reconciliation.py apps/api/app/workers/arq_worker.py apps/api/app/services/call_lifecycle_service.py apps/api/tests/services/test_call_reconciliation_service.py apps/api/tests/workers/test_lifecycle_edge_cases.py apps/api/tests/calls/test_call_lifecycle.py
git commit -m "feat: reconcile calls through a durable state machine"
```

### Task 11: Enforce call duration and remaining-minute limits

**Files:**
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/app/schemas/livekit.py`
- Modify: `apps/api/app/services/livekit_dispatch_service.py`
- Modify: `apps/agent/agent/schemas.py`
- Modify: `apps/agent/agent/session_runtime.py`
- Modify: `apps/agent/agent/main.py`
- Modify: `apps/api/tests/livekit/test_dispatch_service.py`
- Create: `apps/agent/tests/test_call_limits.py`

**Interfaces:**
- Adds `max_call_duration_seconds` with a production default of `3600`.
- Dispatch metadata contains `allowed_duration_seconds`, calculated as `min(max_call_duration_seconds, minutes_remaining * 60)`.
- Produces: `SessionRuntime.enforce_call_limit(metadata, disconnect) -> None`.

- [ ] **Step 1: Write limit-calculation tests**

```python
assert calculate_allowed_duration(minutes_remaining=1, maximum=3600) == 60
assert calculate_allowed_duration(minutes_remaining=120, maximum=3600) == 3600
```

Add agent tests that warn at 60 seconds remaining when the total allowance exceeds 90 seconds, end at the limit, and cancel the timer on normal disconnection.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd apps/agent && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/test_call_limits.py -v`

Expected: limit enforcement does not exist.

- [ ] **Step 3: Implement server-derived limits**

Calculate the limit at dispatch from authoritative balance. The agent must not accept a larger duration from caller-controlled SIP attributes. At expiry, play the configured French closing message and disconnect the room participant.

- [ ] **Step 4: Run API and agent tests, then commit**

```bash
cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/livekit/test_dispatch_service.py -v
cd ../agent && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -q
git add apps/api/app/core/config.py apps/api/app/schemas/livekit.py apps/api/app/services/livekit_dispatch_service.py apps/api/tests/livekit/test_dispatch_service.py apps/agent/agent/schemas.py apps/agent/agent/session_runtime.py apps/agent/agent/main.py apps/agent/tests/test_call_limits.py
git commit -m "feat: enforce per-call minute and duration limits"
```

### Task 12: Harden telephony, storage, and provider adapters

**Files:**
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/app/providers/telephony/telnyx.py`
- Modify: `apps/api/app/services/telephony_service.py`
- Modify: `apps/api/app/providers/storage/s3.py`
- Modify: `apps/api/app/services/recording_service.py`
- Modify: `apps/api/app/services/billing_session_service.py`
- Modify: `apps/api/app/providers/notifications/firebase.py`
- Modify: `apps/api/app/services/notification_service.py`
- Modify: `apps/api/tests/telephony/test_telnyx_provider.py`
- Modify: `apps/api/tests/providers/test_integrations.py`
- Create: `apps/api/tests/providers/test_s3_lifecycle.py`
- Create: `apps/api/tests/providers/test_notification_privacy.py`

**Interfaces:**
- Produces: `normalize_french_number(value: str) -> str` using `phonenumbers` with region `FR`.
- `StorageProvider.get_download_url(object_key: str) -> str | None` returns `None` when the object is absent.
- All synchronous provider SDK operations execute through `asyncio.to_thread` or an async SDK adapter.
- Launch notifications contain only an opaque call ID and event type; transcript and summary content are fetched through authenticated API routes.

- [ ] **Step 1: Write phone-normalization tests**

```python
@pytest.mark.parametrize("raw,expected", [
    ("06 12 34 56 78", "+33612345678"),
    ("0033 6 12 34 56 78", "+33612345678"),
    ("+33 6 12 34 56 78", "+33612345678"),
])
def test_normalize_french_number(raw, expected):
    assert normalize_french_number(raw) == expected
```

Reject valid non-French numbers in the France-only provisioning path.

- [ ] **Step 2: Write storage existence tests**

Assert missing object returns `None`, existing object returns a presigned URL, bucket-not-found in production raises a configuration error, and read paths never call `make_bucket`.

Add notification tests proving predictable Firebase topics never receive a summary, caller number, transcript, recording URL, or customer identity. Disable push delivery until server-owned device-token registration exists; dashboard notifications continue through authenticated reads.

- [ ] **Step 3: Write event-loop responsiveness tests**

Use a blocking fake Stripe/Telnyx/MinIO client and an asyncio heartbeat. Assert the heartbeat progresses while the provider call is in flight.

- [ ] **Step 4: Run targeted tests and verify failure**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/telephony/test_telnyx_provider.py tests/providers/test_integrations.py tests/providers/test_s3_lifecycle.py tests/providers/test_notification_privacy.py -v`

Expected: normalization, missing-object behavior, or event-loop assertions fail.

- [ ] **Step 5: Implement adapter hardening**

Add `phonenumbers`, wrap blocking SDK calls, set explicit connection/read timeouts, map retryable versus terminal provider errors, call `stat_object` before signing, and restrict bucket creation to a development initialization command.

Derive `routing_enabled` only when both the agent configuration and provider-backed phone projection are active. Add reconciliation that repairs or reports disagreement rather than treating either flag as sufficient.

- [ ] **Step 6: Add object lifecycle verification**

Document and provision a 30-day recording lifecycle rule in the selected storage infrastructure. The integration test uploads an object with a lifecycle test tag, verifies private access, and verifies the rule exists through the provider API.

- [ ] **Step 7: Run API tests and commit**

```bash
cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -q
git add apps/api/pyproject.toml apps/api/uv.lock apps/api/app/providers/telephony/telnyx.py apps/api/app/services/telephony_service.py apps/api/app/providers/storage/s3.py apps/api/app/services/recording_service.py apps/api/app/services/billing_session_service.py apps/api/app/providers/notifications/firebase.py apps/api/app/services/notification_service.py apps/api/tests/telephony/test_telnyx_provider.py apps/api/tests/providers/test_integrations.py apps/api/tests/providers/test_s3_lifecycle.py apps/api/tests/providers/test_notification_privacy.py
git commit -m "fix: harden telephony billing and recording providers"
```

### Task 13: Remove realtime delivery from the launch-critical path

**Files:**
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/services/livekit_dispatch_service.py`
- Modify: `apps/api/app/routers/websocket.py`
- Modify: `apps/api/tests/realtime/test_websocket_auth.py`
- Modify: `apps/api/tests/realtime/test_websocket_lifecycle.py`
- Modify: `apps/web/src/config/app-config.ts`
- Modify: `docs/architecture/backend-context.md`

**Interfaces:**
- Adds `realtime_enabled: bool = False`.
- Production returns `404` for the WebSocket route while the flag is false.
- Dashboard correctness relies on server reads and `revalidatePath`, not Pub/Sub delivery.

- [ ] **Step 1: Write disabled-route tests**

Assert production with `REALTIME_ENABLED=false` does not register or accept the route and that call dispatch/finalization succeeds without a realtime service.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/realtime tests/livekit/test_dispatch_service.py -v`

Expected: route and service are currently always wired.

- [ ] **Step 3: Make realtime optional and remove business dependencies**

Publish realtime events only as best-effort observers behind the flag. Do not make billing, calls, recording, provisioning, or onboarding depend on Pub/Sub success.

- [ ] **Step 4: Ensure server actions revalidate authoritative pages**

After agent configuration, archive, billing portal return, and provisioning retry actions, revalidate `/dashboard`, `/dashboard/agent`, `/dashboard/billing`, and `/dashboard/calls` according to the mutated resource.

- [ ] **Step 5: Run API and web suites, then commit**

```bash
cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -q
cd ../web && npm run test -- --run && npm run build
git add apps/api/app/core/config.py apps/api/app/main.py apps/api/app/services/livekit_dispatch_service.py apps/api/app/routers/websocket.py apps/api/tests/realtime apps/api/tests/livekit/test_dispatch_service.py apps/web/src/config/app-config.ts apps/web/src/app docs/architecture/backend-context.md
git commit -m "refactor: remove realtime from the launch-critical path"
```

## Workstream 4: Operations, CI, Storage, and Recovery

### Task 14: Add liveness, readiness, metrics, traces, and error reporting

**Files:**
- Modify: `apps/api/pyproject.toml`
- Create: `apps/api/app/core/observability.py`
- Create: `apps/api/app/routers/readiness.py`
- Modify: `apps/api/app/routers/health.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/workers/arq_worker.py`
- Modify: `apps/agent/pyproject.toml`
- Modify: `apps/agent/agent/main.py`
- Create: `apps/api/tests/test_readiness.py`
- Create: `apps/api/tests/test_observability.py`

**Interfaces:**
- `GET /healthz` is process liveness and never contacts dependencies.
- `GET /readyz` checks PostgreSQL and Redis with a two-second total deadline and returns `503` when either is unavailable.
- Metrics include request latency/status, webhook outcomes, outbox depth/failures, queue latency, calls by state, reconciliation outcomes, and provider latency/error class.
- Traces propagate `trace_id`, `call_id`, and provider request ID without customer content.

- [ ] **Step 1: Write readiness tests**

```python
async def test_readyz_reports_database_failure(client, failing_database):
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {"database": "unavailable", "redis": "ok"},
    }
```

Add the symmetric Redis failure and all-healthy cases. Assert `/healthz` remains `200` during dependency failure.

- [ ] **Step 2: Write metric and redaction tests**

Execute one successful webhook and one provider failure. Assert counters increment and that phone numbers, prompts, transcript text, and credentials do not appear in labels or spans.

- [ ] **Step 3: Run tests and verify failure**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/test_readiness.py tests/test_observability.py -v`

Expected: readiness and metrics do not exist.

- [ ] **Step 4: Implement OpenTelemetry-compatible instrumentation**

Use OpenTelemetry APIs and OTLP exporters configured by environment. Instrument FastAPI, SQLAlchemy, Redis, HTTPX, workers, and provider adapters. Use low-cardinality metric labels; never use `user_id`, `call_id`, or phone numbers as metric labels.

- [ ] **Step 5: Define beta alerts**

Document these alert thresholds in `docs/runbooks/incident-response.md`:

- readiness failure for 2 consecutive minutes;
- webhook failure rate above 2% for 5 minutes;
- any outbox terminal failure;
- more than 5 calls stuck beyond lifecycle deadlines;
- queue oldest-job age above 2 minutes;
- provider error rate above 10% for 5 minutes;
- recording upload failures above 5% for 10 minutes;
- no successful backup within 24 hours.

- [ ] **Step 6: Run full tests and commit**

```bash
cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -q
cd ../agent && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -q
git add apps/api/pyproject.toml apps/api/uv.lock apps/api/app/core/observability.py apps/api/app/routers/readiness.py apps/api/app/routers/health.py apps/api/app/main.py apps/api/app/workers/arq_worker.py apps/api/tests/test_readiness.py apps/api/tests/test_observability.py apps/agent/pyproject.toml apps/agent/uv.lock apps/agent/agent/main.py docs/runbooks/incident-response.md
git commit -m "feat: add production health and observability signals"
```

### Task 15: Add enforced CI and supply-chain checks

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/dependabot.yml`
- Create: `.gitleaks.toml`
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/agent/pyproject.toml`
- Modify: `apps/web/package.json`
- Modify: `README.md`

**Interfaces:**
- Every pull request runs API tests on PostgreSQL, agent tests, web tests, Biome check, TypeScript/Next build, Alembic upgrade, dependency audits, secret scan, and container scan.
- Main-branch protection requires every CI job before merge.

- [ ] **Step 1: Add deterministic local quality commands**

Add API/agent development dependencies for `ruff`, `mypy`, and `pip-audit`. Add scripts or documented commands so CI and developers run the same checks.

```bash
cd apps/api && uv run ruff check app tests && uv run mypy app
cd apps/agent && uv run ruff check agent tests && uv run mypy agent
cd apps/web && npm run check && npm run test -- --run && npm run build
```

- [ ] **Step 2: Fix the existing web formatting/import-order failures**

Run `npm run check:fix`, inspect every diff, then run `npm run check`. Do not mix behavioral frontend changes into this formatting commit.

- [ ] **Step 3: Create the GitHub Actions workflow**

Use PostgreSQL 17 and Redis 7 service containers. Separate jobs for API, agent, web, migrations, dependency audits, gitleaks, and Trivy image scanning. Pin actions to immutable major versions and grant read-only repository permissions except where an action explicitly requires more.

- [ ] **Step 4: Verify the workflow locally**

Run every workflow command in the repository. Push a test branch and confirm every required job passes. Intentionally introduce a formatting error and a fake gitleaks test credential in an ignored fixture to confirm the appropriate jobs fail, then remove them.

- [ ] **Step 5: Configure branch protection**

Require one review, dismissal of stale approvals, linear history, signed status checks, and the complete CI job set. Do not allow administrators to bypass production migration and security jobs during the beta.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml .github/dependabot.yml .gitleaks.toml apps/api/pyproject.toml apps/api/uv.lock apps/agent/pyproject.toml apps/agent/uv.lock apps/web/package.json apps/web/package-lock.json apps/web README.md
git commit -m "ci: enforce tests builds and security scans"
```

### Task 16: Harden containers and separate migrations from application startup

**Files:**
- Modify: `apps/api/Dockerfile`
- Modify: `apps/agent/Dockerfile`
- Modify: `apps/web/Dockerfile`
- Modify: `apps/api/app/main.py`
- Modify: `compose.yaml`
- Modify: `compose.dev.yaml`
- Create: `docs/architecture/production-deployment.md`
- Create: `docs/runbooks/deploy.md`
- Create: `docs/runbooks/rollback.md`
- Modify: `apps/api/tests/test_deployment_readiness.py`

**Interfaces:**
- API startup never runs Alembic.
- A one-shot release command runs `alembic upgrade head` before new application replicas receive traffic.
- Containers run as numeric non-root users, expose only required ports, and have explicit health checks.

- [ ] **Step 1: Write deployment-readiness assertions**

Assert the API startup code contains no migration invocation. Build each image and inspect its configured user:

```bash
docker inspect --format '{{.Config.User}}' presvo-api:test
docker inspect --format '{{.Config.User}}' presvo-agent:test
docker inspect --format '{{.Config.User}}' presvo-web:test
```

Expected: each output is a nonzero numeric UID.

- [ ] **Step 2: Build minimal multi-stage images**

Pin the package installer version, copy only locked production dependencies, use Next.js standalone output, remove compilers and development dependencies from runtime layers, and set read-only-compatible working directories with a writable `/tmp` only.

- [ ] **Step 3: Separate local and production concerns**

Keep PostgreSQL, Redis, and MinIO in `compose.dev.yaml`. Keep `compose.yaml` free of active credentials and development database passwords. Use `${VARIABLE:?required}` syntax for values required by a composed staging smoke run.

- [ ] **Step 4: Create the hosting decision record**

In `production-deployment.md`, compare at least:

- AWS Paris (`eu-west-3`) managed services;
- Scaleway Paris managed services;
- an EU-region managed application platform.

Score data residency, managed PostgreSQL PITR, managed Redis TLS, private networking, secret management, object lifecycle/KMS, worker support, static egress IP, operational effort, and monthly beta cost. Select one target and obtain user approval before writing vendor-specific infrastructure-as-code. The selected target receives a separate implementation plan with exact Terraform resources.

- [ ] **Step 5: Document release and rollback sequences**

Release order: backup verification → migration job → worker/agent deployment → API deployment → readiness pass → web deployment → smoke test. Rollback must distinguish backward-compatible application rollback from irreversible data migration and specify when forward-fix is required.

- [ ] **Step 6: Verify images and commit**

```bash
docker build -t presvo-api:test apps/api
docker build -t presvo-agent:test apps/agent
docker build -t presvo-web:test apps/web
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
curl --fail http://localhost:8000/healthz
```

```bash
git add apps/api/Dockerfile apps/agent/Dockerfile apps/web/Dockerfile apps/api/app/main.py compose.yaml compose.dev.yaml docs/architecture/production-deployment.md docs/runbooks/deploy.md docs/runbooks/rollback.md apps/api/tests/test_deployment_readiness.py
git commit -m "ops: harden containers and release migrations separately"
```

### Task 17: Prove backup, restore, retention, and incident recovery

**Files:**
- Create: `docs/runbooks/backup-restore.md`
- Create: `docs/runbooks/provider-outage.md`
- Create: `docs/runbooks/incident-response.md`
- Create: `scripts/verify-backup-restore.sh`
- Create: `scripts/verify-recording-lifecycle.sh`
- Modify: `docs/architecture/staging-smoke-runbook.md`

**Interfaces:**
- Beta recovery objectives: database RPO at most 15 minutes and RTO at most 4 hours.
- Recording retention: private storage and automatic deletion at 30 days unless counsel approves another value.
- Runbooks name the operator action, evidence, stop condition, escalation, and customer-communication trigger.

- [ ] **Step 1: Configure managed PostgreSQL PITR and daily backup verification**

Enable provider-managed continuous backups. Create a restricted restore-test database, restore the latest backup, and verify row counts plus referential integrity without exposing customer content.

- [ ] **Step 2: Write the automated restore verifier**

The script accepts `RESTORED_DATABASE_URL`, runs migrations in check mode, verifies required tables and constraints, samples call/subscription counts, and exits nonzero on failure. It must never print connection credentials.

- [ ] **Step 3: Prove object lifecycle and private access**

Upload a canary recording, assert anonymous HTTP access returns `403`, assert a short-lived signed URL works, assert the lifecycle rule is attached, then delete the canary.

- [ ] **Step 4: Conduct one tabletop incident**

Simulate database unavailability, Redis unavailability, Stripe webhook backlog, Telnyx outage, LiveKit outage, and leaked credential. For each, follow the runbook and record detection time, mitigation time, missing telemetry, and owner.

- [ ] **Step 5: Commit value-free automation and evidence templates**

```bash
git add docs/runbooks/backup-restore.md docs/runbooks/provider-outage.md docs/runbooks/incident-response.md scripts/verify-backup-restore.sh scripts/verify-recording-lifecycle.sh docs/architecture/staging-smoke-runbook.md
git commit -m "ops: add tested backup retention and incident runbooks"
```

## Workstream 5: Voice Quality, Compliance, and Product Completion

### Task 18: Add behavioral voice-agent evaluations

**Files:**
- Create: `apps/agent/tests/evals/test_disclosure.py`
- Create: `apps/agent/tests/evals/test_grounding.py`
- Create: `apps/agent/tests/evals/test_safety.py`
- Create: `apps/agent/tests/evals/test_conversation_failures.py`
- Create: `apps/agent/tests/evals/fixtures.py`
- Modify: `apps/agent/pyproject.toml`
- Modify: `apps/agent/agent/prompt_builder.py`
- Modify: `apps/agent/agent/pipeline_factory.py`
- Modify: `apps/api/app/schemas/agent.py`
- Modify: `apps/api/app/routers/agent.py`
- Modify: `apps/api/tests/agent/test_agent_config_api.py`

**Interfaces:**
- Evaluations assert greeting/disclosure, French behavior, grounding, refusal boundaries, prompt-injection resistance, silence, interruption, provider error, and long-call behavior.
- Production prompt limits: `system_prompt` 8,000 characters, `knowledge_base` 100,000 characters, transcript segment 4,000 characters.

- [ ] **Step 1: Add deterministic prompt-limit unit tests**

Assert empty prompt plus knowledge base still receives system guardrails, over-limit fields are rejected at the API boundary, and guardrails cannot be removed by customer prompt text.

Use Pydantic's `model_fields_set` rather than `exclude_none=True` for patch semantics so `owner_context: null` explicitly clears the value while omitted fields remain unchanged.

- [ ] **Step 2: Add disclosure evaluations**

Verify the first assistant turn clearly states it is an AI assistant and accurately states whether the call is being recorded. Approved legal wording is supplied by Task 20; until then, use the current wording only in non-production evaluation fixtures.

- [ ] **Step 3: Add grounding and misuse evaluations**

Test known-answer, unknown-answer, conflicting knowledge, indirect prompt injection in the knowledge base, requests to reveal the system prompt, and caller attempts to change the agent’s identity or recording disclosure.

- [ ] **Step 4: Add conversation-failure evaluations**

Test 15 seconds of silence, rapid interruption, STT timeout, LLM timeout, TTS timeout, caller hangup during speech, transcript API outage, and maximum-duration expiry. Assert a bounded fallback response or clean termination without leaking internal errors.

- [ ] **Step 5: Run evaluations in CI**

Run deterministic mocked evaluations on every pull request and a provider-backed evaluation suite nightly with cost and duration caps. Store scores and regress the build when disclosure or safety tests fail.

- [ ] **Step 6: Commit**

```bash
git add apps/agent/tests/evals apps/agent/pyproject.toml apps/agent/uv.lock apps/agent/agent/prompt_builder.py apps/agent/agent/pipeline_factory.py apps/api/app/schemas/agent.py apps/api/app/routers/agent.py apps/api/tests/agent/test_agent_config_api.py
git commit -m "test: add behavioral voice agent evaluations"
```

### Task 19: Implement auditable data export, deletion, and recording access

**Files:**
- Create: `apps/api/alembic/versions/0009_add_data_lifecycle_records.py`
- Create: `apps/api/app/models/data_request.py`
- Create: `apps/api/app/models/recording_access_event.py`
- Modify: `apps/api/app/models/__init__.py`
- Create: `apps/api/app/repositories/data_request_repository.py`
- Create: `apps/api/app/services/data_lifecycle_service.py`
- Create: `apps/api/app/routers/account.py`
- Modify: `apps/api/app/services/call_history_service.py`
- Create: `apps/api/app/workers/jobs/data_lifecycle.py`
- Modify: `apps/api/app/workers/arq_worker.py`
- Create: `apps/api/tests/account/test_data_lifecycle.py`
- Modify: `apps/api/tests/calls/test_call_history_api.py`

**Interfaces:**
- `POST /api/account/export` creates an authenticated export request.
- `DELETE /api/account` creates a confirmed deletion request and immediately disables routing.
- Every signed recording URL records user, call, purpose `customer_playback`, timestamp, and outcome without storing the URL.
- Purge execution is idempotent and produces an operator-visible completion record.

- [ ] **Step 1: Write export and deletion contract tests**

Assert cross-tenant denial, duplicate-request idempotency, immediate routing disable, recording deletion, transcript deletion, and a completed request record. Financial records follow the counsel-approved retention policy and are pseudonymized when deletion is legally constrained.

- [ ] **Step 2: Write recording-access audit tests**

Assert every successful and missing-object recording request creates an access event for the authenticated owner, and another user cannot create or retrieve a URL.

- [ ] **Step 3: Run tests and verify failure**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/account/test_data_lifecycle.py tests/calls/test_call_history_api.py -v`

Expected: account lifecycle and access audit do not exist.

- [ ] **Step 4: Add data request and audit models**

Use states `requested`, `processing`, `completed`, `failed`, with unique active request per user/type. Store timestamps, failure code, completion counts, and export object key; never store signed URLs.

- [ ] **Step 5: Implement purge order**

Disable phone routing → cancel provider operations → delete recordings → delete notifications/transcripts/calls/config → pseudonymize or delete identity according to approved retention → mark request complete. Make every step retryable and idempotent.

- [ ] **Step 6: Run migration and API suites, then commit**

```bash
cd apps/api && DATABASE_URL="$TEST_DATABASE_URL" UV_CACHE_DIR=/tmp/uv-cache uv run alembic upgrade head
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -q
git add apps/api/alembic/versions/0009_add_data_lifecycle_records.py apps/api/app/models apps/api/app/repositories/data_request_repository.py apps/api/app/services/data_lifecycle_service.py apps/api/app/routers/account.py apps/api/app/services/call_history_service.py apps/api/app/workers/jobs/data_lifecycle.py apps/api/app/workers/arq_worker.py apps/api/tests/account/test_data_lifecycle.py apps/api/tests/calls/test_call_history_api.py
git commit -m "feat: add auditable account data lifecycle"
```

### Task 20: Add approved French legal, recording, and support surfaces

**Files:**
- Create: `docs/compliance/caller-disclosure.md`
- Create: `docs/compliance/data-retention.md`
- Create: `docs/compliance/subprocessors.md`
- Create: `docs/compliance/legal-approval-record.md`
- Create: `apps/web/src/app/privacy/page.tsx`
- Create: `apps/web/src/app/terms/page.tsx`
- Create: `apps/web/src/app/legal/page.tsx`
- Create: `apps/web/src/app/support/page.tsx`
- Modify: `apps/web/src/app/layout.tsx`
- Modify: `apps/web/src/app/page.tsx`
- Modify: `apps/agent/agent/prompt_builder.py`
- Create: `apps/web/tests/app/legal-pages.test.tsx`
- Modify: `apps/agent/tests/evals/test_disclosure.py`

**Interfaces:**
- Legal approval record contains document version, counsel name, approval date, applicable countries, recording mode, lawful basis, retention period, and approved disclosure copy.
- The agent disclosure and web privacy text share the same version identifier.

- [ ] **Step 1: Obtain legal decisions before production copy is merged**

Counsel must approve controller identity, processing purposes, lawful bases, recording default/opt-out behavior, retention, data-subject contact, complaint route, subprocessors, international transfers, billing terms, cancellation terms, and AI disclosure.

- [ ] **Step 2: Write exact French and English policy pages from approved text**

Pages must include effective date, version, company identity/address/registration, support contact, privacy contact, retention table, rights process, CNIL complaint route, subprocessors, and recording behavior. Do not invent legal facts in code.

- [ ] **Step 3: Implement the approved caller disclosure**

The first assistant turn uses the approved French copy verbatim and states recording status accurately. If recording is optional, the call flow must honor the approved opt-out mechanism before starting or continuing recording.

- [ ] **Step 4: Add navigation and metadata**

Link privacy, terms, legal notice, and support from the landing-page footer, authenticated layout, checkout context, and account menu. Set French metadata and `lang="fr"` for the France launch.

- [ ] **Step 5: Test and commit**

```bash
cd apps/web && npm run test -- --run tests/app/legal-pages.test.tsx && npm run check && npm run build
cd ../agent && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/evals/test_disclosure.py -v
git add docs/compliance apps/web/src/app/privacy apps/web/src/app/terms apps/web/src/app/legal apps/web/src/app/support apps/web/src/app/layout.tsx apps/web/src/app/page.tsx apps/web/tests/app/legal-pages.test.tsx apps/agent/agent/prompt_builder.py apps/agent/tests/evals/test_disclosure.py
git commit -m "feat: add approved French legal and caller disclosures"
```

### Task 21: Localize and complete the core customer workflows

**Files:**
- Create: `apps/web/src/i18n/fr.ts`
- Create: `apps/web/src/components/account/account-menu.tsx`
- Create: `apps/web/src/components/calls/calls-pagination.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/layout.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/page.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/calls/page.tsx`
- Modify: `apps/web/src/components/calls/calls-table.tsx`
- Modify: `apps/web/src/components/calls/call-detail-card.tsx`
- Modify: `apps/web/src/components/calls/recording-panel.tsx`
- Modify: `apps/web/src/components/calls/transcript-panel.tsx`
- Modify: `apps/web/src/components/agent/agent-settings-form.tsx`
- Modify: `apps/web/src/components/billing/billing-actions-card.tsx`
- Modify: `apps/web/src/lib/api/backend-client.ts`
- Modify: `apps/web/src/lib/formatters.ts`
- Create: `apps/web/tests/app/account-menu.test.tsx`
- Modify: `apps/web/tests/app/calls-page.test.tsx`
- Modify: `apps/web/tests/app/agent-page.test.tsx`
- Modify: `apps/web/tests/app/billing-page.test.tsx`

**Interfaces:**
- All launch UI copy comes from `fr.ts`; no customer-facing English remains in launch paths.
- Backend requests use an `AbortSignal.timeout(10_000)` default.
- Calls list accepts `page` and renders accessible previous/next controls.
- Recording playback uses an inline `<audio controls preload="none">` element with a freshly resolved URL.

- [ ] **Step 1: Write customer-workflow tests in French**

Cover account menu/sign-out, onboarding statuses, guarded routing, field validation, pricing/tax/renewal copy, call pagination, archive confirmation, empty transcript, structured summary sections, and recording playback.

- [ ] **Step 2: Run web tests and verify failure**

Run: `cd apps/web && npm run test -- --run`

Expected: the new French copy and controls are absent.

- [ ] **Step 3: Add a single-locale copy boundary**

Use a typed nested object rather than adding a localization framework for one locale. Format dates with `Intl.DateTimeFormat("fr-FR", { dateStyle: "medium", timeStyle: "short", timeZone })` and prices with `Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR" })`.

- [ ] **Step 4: Complete account and call actions**

Add account/sign-out controls, archive confirmation, URL-driven pagination, inline player, intent/action-item/follow-up summary sections, and useful empty states. Revalidate authoritative pages after mutations.

- [ ] **Step 5: Harden agent settings**

Use a semantic `<form>`, field names, labels, `maxLength`, inline errors, character counts, clearable nullable `owner_context`, unsaved-change warning, and a disabled routing switch with an explicit list of unmet prerequisites.

- [ ] **Step 6: Add backend request deadlines**

Apply a ten-second default timeout, map timeout to a controlled service-unavailable result, and never retry non-idempotent requests automatically in the web layer.

- [ ] **Step 7: Run web checks and commit**

```bash
cd apps/web && npm run check && npm run test -- --run && npm run build
git add apps/web/src apps/web/tests
git commit -m "feat: complete and localize the France customer workflow"
```

### Task 22: Pass accessibility and frontend performance gates

**Files:**
- Modify: `apps/web/src/app/globals.css`
- Modify: `apps/web/src/lib/fonts/registry.ts`
- Modify: `apps/web/src/components/ui/sonic-waveform.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/_components/sidebar/app-sidebar.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/layout.tsx`
- Create: `apps/web/tests/a11y/dashboard.a11y.test.tsx`
- Create: `apps/web/tests/e2e/core-workflows.spec.ts`
- Modify: `apps/web/package.json`

**Interfaces:**
- Zero serious or critical automated accessibility violations on landing, dashboard, agent, billing, calls list, and call detail.
- Landing mobile Lighthouse targets: performance ≥ 85, accessibility ≥ 95, best practices ≥ 95, SEO ≥ 90.
- Dashboard interaction targets: no continuous animation when hidden or reduced motion is requested.

- [ ] **Step 1: Add axe and Playwright checks**

Test keyboard-only navigation, visible focus, skip link, dialog focus trapping, switch labels, form error association, table/pagination semantics, and reduced-motion behavior.

- [ ] **Step 2: Run checks and capture the failing baseline**

```bash
cd apps/web
npm run test -- --run tests/a11y
npx playwright test tests/e2e/core-workflows.spec.ts
```

- [ ] **Step 3: Reduce fonts and canvas work**

Keep at most two production font families. Pause waveform rendering when the document is hidden, cap device-pixel ratio at 2, reduce segment density based on viewport width, and render a static state for `prefers-reduced-motion: reduce`.

- [ ] **Step 4: Fix navigation semantics**

Add a skip link, correct sidebar prop behavior, accessible names for every icon-only control, deterministic heading levels, focus restoration, and removal of broad `transition-all` rules.

- [ ] **Step 5: Run accessibility, build, and Lighthouse checks**

All automated thresholds must pass twice on a clean production build. Record the Lighthouse JSON as CI artifacts rather than committing generated reports.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src apps/web/tests apps/web/package.json apps/web/package-lock.json
git commit -m "fix: meet launch accessibility and frontend performance gates"
```

## Workstream 6: Staging Certification and Controlled Beta

### Task 23: Automate the real-provider staging journey

**Files:**
- Create: `scripts/staging/create-test-customer.sh`
- Create: `scripts/staging/replay-webhook.sh`
- Create: `scripts/staging/assert-call-result.sh`
- Modify: `docs/architecture/staging-smoke-runbook.md`
- Create: `docs/runbooks/staging-certification.md`

**Interfaces:**
- A certification run uses a unique test customer, unique Stripe test subscription, unique French staging number, and unique call ID.
- Evidence records timestamps and opaque provider IDs without credentials, transcript text, or full phone numbers.

- [ ] **Step 1: Define the clean journey**

Execute: sign up → Stripe Checkout → `invoice.paid` → provisioning → setup → routing enable → inbound French call → transcript → summary → recording → minute debit → archive → portal access → cancellation → routing disabled.

- [ ] **Step 2: Add replay checks**

Replay Stripe, Clerk, and LiveKit events at least three times each. Assert one subscription, one invoice grant, one provisioning operation, one call, one recording start, and one call debit.

- [ ] **Step 3: Add cancellation and payment-failure checks**

Move the Stripe test subscription through `past_due`, recovery to `active`, and cancellation. Assert local access and Telnyx routing match the policy after reconciliation.

- [ ] **Step 4: Perform three independent certification runs**

Use three newly created customers. No operator may edit the database or manually mark provisioning/calls complete. A failed run restarts the three-run count after its fix is deployed.

- [ ] **Step 5: Commit scripts and value-free evidence template**

```bash
git add scripts/staging docs/architecture/staging-smoke-runbook.md docs/runbooks/staging-certification.md
git commit -m "test: automate end to end staging certification"
```

### Task 24: Run concurrency, load, outage, and recovery drills

**Files:**
- Create: `tests/load/call_webhook_load.js`
- Create: `tests/load/dashboard_load.js`
- Create: `docs/runbooks/failure-drill-results.md`
- Modify: `docs/runbooks/provider-outage.md`
- Modify: `docs/runbooks/incident-response.md`

**Interfaces:**
- Beta load target: 10 simultaneous calls, 50 authenticated dashboard users, and 20 webhook deliveries per second for 15 minutes.
- Acceptance: zero cross-tenant responses, zero duplicate ledgers, zero negative balances, zero lost acknowledged transcript segments, API p95 below 750 ms excluding provider latency, and all stuck work reconciled within 10 minutes after recovery.

- [ ] **Step 1: Generate synthetic tenant-safe test data**

Use isolated staging customers and non-production phone numbers. Never replay production payloads or customer transcripts.

- [ ] **Step 2: Run baseline load**

Measure API latency, PostgreSQL pool saturation, Redis latency, worker queue age, agent CPU/memory, provider error rates, and outbox backlog.

- [ ] **Step 3: Inject controlled failures**

During separate runs, block Redis, terminate one agent process, return provider `429/500`, delay S3, delay Gemini, duplicate webhooks, and terminate a worker during finalization.

- [ ] **Step 4: Verify recovery invariants**

For every drill, query database invariants, wait for reconciliation, confirm alerts fired, confirm runbooks led to recovery, and record elapsed detection/recovery times.

- [ ] **Step 5: Fix and repeat until all acceptance criteria pass**

Each discovered defect receives its own failing regression test and focused commit. Do not weaken thresholds to make a failing run pass.

- [ ] **Step 6: Commit load definitions and sanitized results**

```bash
git add tests/load docs/runbooks/failure-drill-results.md docs/runbooks/provider-outage.md docs/runbooks/incident-response.md
git commit -m "test: certify load and failure recovery behavior"
```

### Task 25: Launch and observe the controlled design-partner beta

**Files:**
- Create: `docs/runbooks/beta-onboarding.md`
- Create: `docs/runbooks/beta-support.md`
- Create: `docs/runbooks/go-no-go-checklist.md`
- Modify: `README.md`
- Modify: `docs/architecture/backend-context.md`

**Interfaces:**
- Beta capacity is capped at 10 customers.
- Every customer has an owner, onboarding date, consented support route, and incident communication contact.
- Public self-service acquisition remains disabled during the observation period.
- Beta SLOs are API availability at least 99.5%, successful eligible-call connection at least 95%, completed-call finalization within 10 minutes at least 99%, and zero cross-tenant or incorrect-accounting incidents.

- [ ] **Step 1: Hold the go/no-go review**

Review every design gate, open security finding, legal approval, staging certification, restore result, load result, alert, on-call owner, provider quota, cost cap, and rollback procedure. Every item is pass/fail with named evidence.

- [ ] **Step 2: Onboard the first two design partners**

Observe checkout, provisioning, setup, first call, billing display, cancellation understanding, and support contact. Do not onboard the remaining partners until both complete a real call and no severity-one or severity-two defect remains open.

- [ ] **Step 3: Expand gradually to five, then ten customers**

Review reliability, call completion, provider spend, support burden, transcript/summary quality, and privacy requests at each capacity step.

- [ ] **Step 4: Use explicit beta stop conditions**

Pause new onboarding for any cross-tenant access, incorrect charge, negative balance, unrevoked secret leak, material recording/disclosure failure, unrecoverable call loss, or inability to restore the database.

- [ ] **Step 5: Complete the observation review**

After the agreed beta observation period, compare actual SLOs, support volume, gross margin, call quality, conversion, churn, and legal obligations against launch targets. Create a separate public-launch plan from measured beta evidence.

- [ ] **Step 6: Commit the operating model**

```bash
git add docs/runbooks/beta-onboarding.md docs/runbooks/beta-support.md docs/runbooks/go-no-go-checklist.md README.md docs/architecture/backend-context.md
git commit -m "docs: define controlled beta operating model"
```

## Final Verification Matrix

Run this matrix after Task 25 code is complete and before the beta go/no-go meeting:

```bash
cd apps/api
DATABASE_URL="$TEST_DATABASE_URL" UV_CACHE_DIR=/tmp/uv-cache uv run alembic upgrade head
TEST_DATABASE_URL="$TEST_DATABASE_URL" TEST_REDIS_URL="$TEST_REDIS_URL" UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -q
uv run ruff check app tests
uv run mypy app

cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -q
uv run ruff check agent tests
uv run mypy agent

cd ../web
npm run check
npm run test -- --run
npm run build
npx playwright test

cd ../..
docker build -t presvo-api:release-candidate apps/api
docker build -t presvo-agent:release-candidate apps/agent
docker build -t presvo-web:release-candidate apps/web
```

Expected:

- Every command exits zero.
- PostgreSQL integration tests run against PostgreSQL, not SQLite.
- No high or critical dependency/container vulnerability is accepted without a dated, owner-approved risk exception.
- Three staging certification journeys pass.
- Backup restoration and provider failure drills pass.
- Counsel approval record matches the deployed legal and caller-disclosure version.

## Definition of Done

This plan is complete only when:

- all 25 tasks are checked;
- every gate has linked evidence;
- the controlled beta is operating under documented capacity and stop conditions;
- no launch-critical behavior depends on manual database edits;
- the repository documentation describes deployed reality rather than intended future behavior.
