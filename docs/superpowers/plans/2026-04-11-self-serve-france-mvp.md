# Self-Serve France MVP Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a true self-serve MVP for France where a new customer can subscribe to the `starter` plan, get a French number automatically, complete required setup, enable routing, and handle a real inbound call without staff intervention.

**Architecture:** Keep the current backend and worker foundation, but tighten the Stripe billing state machine, add a durable phone-provisioning record plus onboarding read model, bootstrap agent config for first-run users, and then adapt the dashboard to expose one-plan, one-pipeline, onboarding-first UX. Implement backend state and contracts first, then wire the web app to those contracts, then finish with targeted staging verification.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, ARQ, Stripe webhooks, Telnyx, LiveKit, Next.js App Router, TypeScript, Vitest, Pytest

---

## File Map

- Create: `apps/api/alembic/versions/0006_add_phone_number_provisionings.py`
  - Add durable provisioning-attempt persistence.
- Create: `apps/api/app/models/phone_number_provisioning.py`
  - Persist provisioning status, attempts, retryability, and failure reason.
- Create: `apps/api/app/repositories/phone_number_provisioning_repository.py`
  - Encapsulate provisioning-record reads and writes.
- Create: `apps/api/app/schemas/onboarding.py`
  - Define onboarding status read and retry response contracts.
- Create: `apps/api/app/services/onboarding_service.py`
  - Derive `overall_status`, assigned number state, retryability, and setup completeness.
- Create: `apps/api/app/routers/onboarding.py`
  - Expose onboarding read and retry APIs.
- Create: `apps/api/tests/onboarding/test_onboarding_api.py`
  - Cover onboarding status precedence and retry behavior.
- Create: `apps/api/tests/services/test_onboarding_service.py`
  - Cover state derivation and setup-completeness rules.
- Modify: `apps/api/app/models/__init__.py`
  - Register the new provisioning model.
- Modify: `apps/api/app/services/billing_service.py`
  - Move provisioning trigger to the first fresh `invoice.paid` path and prevent double credits.
- Modify: `apps/api/app/schemas/billing_api.py`
  - Narrow checkout contract to `starter` only.
- Modify: `apps/api/app/services/billing_session_service.py`
  - Enforce `starter` as the only launch plan.
- Modify: `apps/api/app/routers/billing.py`
  - Keep API behavior aligned with one-plan launch scope.
- Modify: `apps/api/app/workers/jobs/phone_provisioning.py`
  - Enforce `FR`, write provisioning status, and support retry-safe execution.
- Modify: `apps/api/app/services/auth_service.py`
  - Bootstrap a default `agent_configs` row for new Clerk users.
- Modify: `apps/api/app/repositories/user_repository.py`
  - Support creation with launch country defaults if needed.
- Modify: `apps/api/app/repositories/agent_config_repository.py`
  - Support get-or-create default config semantics.
- Modify: `apps/api/app/services/agent_config_service.py`
  - Enforce backend readiness gates before enabling routing.
- Modify: `apps/api/app/routers/agent.py`
  - Keep config reads first-run safe and return clearer readiness failures.
- Modify: `apps/api/app/services/onboarding_service.py`
  - Provide readiness inputs for routing-gate enforcement and shared setup-completeness rules.
- Modify: `apps/api/app/main.py`
  - Register the onboarding router.
- Modify: `apps/api/tests/billing/test_stripe_webhooks.py`
  - Rework webhook expectations to match the new state machine.
- Modify: `apps/api/tests/services/test_billing_session_service.py`
  - Cover one-plan launch restrictions.
- Modify: `apps/api/tests/agent/test_agent_config_api.py`
  - Cover first-run config bootstrap and readiness gate failures.
- Modify: `apps/api/tests/workers/test_individual_jobs.py`
  - Cover provisioning persistence and retry-safe behavior.
- Modify: `apps/api/tests/auth/test_clerk_sync.py`
  - Cover default agent-config creation on first user sync.
- Create: `apps/web/src/lib/types/onboarding.ts`
  - Add frontend onboarding types.
- Create: `apps/web/src/lib/api/onboarding.ts`
  - Add onboarding read and retry API helpers.
- Create: `apps/web/src/components/dashboard/onboarding-status-card.tsx`
  - Show onboarding status, assigned number, provisioning state, and retry action.
- Modify: `apps/web/src/app/(app)/dashboard/page.tsx`
  - Make onboarding state the main dashboard decision surface.
- Modify: `apps/web/src/components/dashboard/setup-checklist.tsx`
  - Align setup copy and completion logic with backend rules.
- Modify: `apps/web/src/components/dashboard/status-summary-cards.tsx`
  - Remove stale inferred readiness assumptions.
- Modify: `apps/web/src/components/agent/agent-settings-form.tsx`
  - Hide `sts`, guard routing, and explain readiness requirements.
- Modify: `apps/web/src/app/(app)/dashboard/agent/page.tsx`
  - Remove the local default-config fallback and rely on persisted bootstrap state.
- Modify: `apps/web/src/app/(app)/dashboard/agent/actions.ts`
  - Translate new readiness gate failures into product copy.
- Modify: `apps/web/src/components/billing/billing-actions-card.tsx`
  - Keep only the single `starter` subscribe action.
- Modify: `apps/web/src/app/(app)/dashboard/billing/actions.ts`
  - Narrow the shared action boundary to `starter` only.
- Modify: `apps/web/src/lib/api/billing.ts`
  - Narrow the shared frontend API boundary to `starter` only.
- Modify: `apps/web/src/lib/types/billing.ts`
  - Narrow billing types to launch contract where appropriate.
- Modify: `apps/web/tests/app/agent-page.test.tsx`
  - Cover one-pipeline UI and guarded routing behavior.
- Modify: `apps/web/tests/app/billing-page.test.tsx`
  - Cover one-plan launch UI.
- Modify: `apps/web/tests/app/home-page.test.tsx`
  - Cover onboarding-first dashboard rendering if needed.
- Create: `apps/web/tests/app/dashboard-onboarding.test.tsx`
  - Cover onboarding status card, assigned number display, and retry CTA.
- Modify: `README.md`
  - Document the self-serve launch scope and verification path.
- Modify: `docs/architecture/backend-context.md`
  - Update staging status once implementation and smoke verification land.

## Chunk 1: Billing And Provisioning State Machine

### Task 1: Move automatic provisioning to the first qualifying `invoice.paid`

**Files:**
- Modify: `apps/api/app/services/billing_service.py`
- Modify: `apps/api/tests/billing/test_stripe_webhooks.py`
- Test: `apps/api/tests/billing/test_stripe_webhooks.py`

- [ ] **Step 1: Write the failing webhook tests**
  Add coverage for:
  - `customer.subscription.created` upserts subscription state but does not allocate minutes
  - first fresh `invoice.paid` bootstraps or reconciles the subscription, allocates starter minutes, and enqueues provisioning
  - later fresh `invoice.paid` resets minutes without re-enqueuing provisioning when the user already has a successful number assignment or a successful provisioning record

- [ ] **Step 2: Run the webhook test file to verify failure**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/billing/test_stripe_webhooks.py -v`
Expected: FAIL on the current provisioning and minute-allocation behavior.

- [ ] **Step 3: Implement the minimal billing state machine change**
  Update `BillingService` so:
  - `customer.subscription.created` persists subscription shell data only
  - the first fresh `invoice.paid` creates or reconciles the local subscription row if needed
  - the first fresh `invoice.paid` allocates starter minutes through the activation path
  - provisioning is enqueued only when the plan is `starter`, the subscription is truly paid, and neither a successful number assignment nor a successful provisioning record already exists
  - later `invoice.paid` events only perform the period reset path

- [ ] **Step 4: Re-run the webhook test file**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/billing/test_stripe_webhooks.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/billing_service.py apps/api/tests/billing/test_stripe_webhooks.py
git commit -m "feat: move self-serve provisioning to paid invoice activation"
```

### Task 2: Restrict launch billing to the `starter` plan

**Files:**
- Modify: `apps/api/app/schemas/billing_api.py`
- Modify: `apps/api/app/services/billing_session_service.py`
- Modify: `apps/api/app/routers/billing.py`
- Modify: `apps/api/tests/services/test_billing_session_service.py`
- Modify: `apps/api/tests/billing/test_billing_api.py`
- Test: `apps/api/tests/services/test_billing_session_service.py`
- Test: `apps/api/tests/billing/test_billing_api.py`

- [ ] **Step 1: Write the failing billing-session and API tests**
  Add coverage for:
  - checkout accepts `starter`
  - checkout rejects `standard`
  - the hosted session metadata always writes `starter`

- [ ] **Step 2: Run the targeted billing tests to verify failure**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/services/test_billing_session_service.py tests/billing/test_billing_api.py -v`
Expected: FAIL because `standard` is still accepted.

- [ ] **Step 3: Implement the launch-plan restriction**
  Change the schema and service so:
  - the public request contract only allows `starter`
  - any non-`starter` plan tier is rejected clearly
  - router and service behavior stay aligned with the narrowed contract

- [ ] **Step 4: Re-run the targeted billing tests**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/services/test_billing_session_service.py tests/billing/test_billing_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/schemas/billing_api.py apps/api/app/services/billing_session_service.py apps/api/app/routers/billing.py apps/api/tests/services/test_billing_session_service.py apps/api/tests/billing/test_billing_api.py
git commit -m "feat: restrict launch billing to starter plan"
```

## Chunk 2: Durable Provisioning Status And Onboarding Read Model

### Task 3: Add persisted phone-provisioning state

**Files:**
- Create: `apps/api/alembic/versions/0006_add_phone_number_provisionings.py`
- Create: `apps/api/app/models/phone_number_provisioning.py`
- Create: `apps/api/app/repositories/phone_number_provisioning_repository.py`
- Modify: `apps/api/app/models/__init__.py`
- Modify: `apps/api/app/workers/jobs/phone_provisioning.py`
- Modify: `apps/api/tests/workers/test_individual_jobs.py`
- Test: `apps/api/tests/workers/test_individual_jobs.py`

- [ ] **Step 1: Write the failing provisioning-job tests**
  Add coverage for:
  - provisioning writes a `queued/running/succeeded` or equivalent durable status
  - provisioning failure writes a retryable failed record with the failure reason
  - provisioning success links the assigned `phone_numbers` row
  - provisioning uses `FR` and does not silently fall back to `US`

- [ ] **Step 2: Run the provisioning-job tests to verify failure**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/workers/test_individual_jobs.py -v`
Expected: FAIL because durable provisioning state does not exist yet.

- [ ] **Step 3: Implement the provisioning persistence layer**
  Add the new table, model, repository, and job updates so:
  - every provisioning attempt has a durable row
  - the latest row is the source of truth before a real number exists
  - launch country is forced or validated as `FR`
  - retryability and failure reasons are persisted

- [ ] **Step 4: Re-run the provisioning-job tests**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/workers/test_individual_jobs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/alembic/versions/0006_add_phone_number_provisionings.py apps/api/app/models/phone_number_provisioning.py apps/api/app/repositories/phone_number_provisioning_repository.py apps/api/app/models/__init__.py apps/api/app/workers/jobs/phone_provisioning.py apps/api/tests/workers/test_individual_jobs.py
git commit -m "feat: persist phone provisioning status for self-serve onboarding"
```

### Task 4: Expose onboarding status and retry APIs

**Files:**
- Create: `apps/api/app/schemas/onboarding.py`
- Create: `apps/api/app/services/onboarding_service.py`
- Create: `apps/api/app/routers/onboarding.py`
- Modify: `apps/api/app/main.py`
- Create: `apps/api/tests/services/test_onboarding_service.py`
- Create: `apps/api/tests/onboarding/test_onboarding_api.py`
- Test: `apps/api/tests/services/test_onboarding_service.py`
- Test: `apps/api/tests/onboarding/test_onboarding_api.py`

- [ ] **Step 1: Write the failing onboarding service and API tests**
  Cover:
  - `overall_status` precedence: `live`, `provisioning_failed`, `provisioning_number`, `setup_required`, `ready_to_enable`, `subscription_active`, `not_subscribed`
  - assigned number visibility after provisioning success
  - onboarding response contains `subscription_status`, `plan_tier`, `minutes_remaining`, `phone_number`, `phone_number_status`, `routing_enabled`, `agent_setup_complete`, `overall_status`, and `can_retry_provisioning`
  - retry availability only for retryable failed provisioning records
  - retry endpoint re-enqueues provisioning only when allowed

- [ ] **Step 2: Run the onboarding tests to verify failure**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/services/test_onboarding_service.py tests/onboarding/test_onboarding_api.py -v`
Expected: FAIL because the onboarding read model and retry route do not exist.

- [ ] **Step 3: Implement the onboarding contract**
  Add:
  - onboarding response schemas with all spec-required fields
  - onboarding service that joins subscription, usage, provisioning, number, routing, and config completeness
  - read endpoint for current onboarding state
  - retry endpoint that only works for active `starter` subscribers in a retryable failed provisioning state

- [ ] **Step 4: Re-run the onboarding tests**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/services/test_onboarding_service.py tests/onboarding/test_onboarding_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/schemas/onboarding.py apps/api/app/services/onboarding_service.py apps/api/app/routers/onboarding.py apps/api/app/main.py apps/api/tests/services/test_onboarding_service.py apps/api/tests/onboarding/test_onboarding_api.py
git commit -m "feat: add onboarding status and retry api"
```

## Chunk 3: First-Run Bootstrap And Backend Routing Gates

### Task 5: Create default agent config for new users

**Files:**
- Modify: `apps/api/app/services/auth_service.py`
- Modify: `apps/api/app/repositories/agent_config_repository.py`
- Modify: `apps/api/app/repositories/user_repository.py`
- Modify: `apps/api/tests/auth/test_clerk_sync.py`
- Modify: `apps/api/tests/agent/test_agent_config_api.py`
- Modify: `apps/web/src/app/(app)/dashboard/agent/page.tsx`
- Test: `apps/api/tests/auth/test_clerk_sync.py`
- Test: `apps/api/tests/agent/test_agent_config_api.py`

- [ ] **Step 1: Write the failing first-run bootstrap tests**
  Add coverage for:
  - Clerk sync creates both the user and a default `agent_configs` row
  - config reads on a freshly synced user do not 404
  - the agent page no longer relies on a synthesized fallback config for first-run users

- [ ] **Step 2: Run the targeted auth and agent-config tests to verify failure**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/auth/test_clerk_sync.py tests/agent/test_agent_config_api.py -v`
Expected: FAIL because new users do not get persisted config rows.

- [ ] **Step 3: Implement the bootstrap behavior**
  Update Clerk sync and config repository behavior so:
  - first user sync creates a default config row
  - get-or-create semantics are available where useful
  - the product no longer depends on frontend-only fallback config objects
  - the agent page renders persisted config data only

- [ ] **Step 4: Re-run the targeted tests**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/auth/test_clerk_sync.py tests/agent/test_agent_config_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/auth_service.py apps/api/app/repositories/agent_config_repository.py apps/api/app/repositories/user_repository.py apps/api/tests/auth/test_clerk_sync.py apps/api/tests/agent/test_agent_config_api.py apps/web/src/app/(app)/dashboard/agent/page.tsx
git commit -m "feat: bootstrap agent config for new self-serve users"
```

### Task 6: Enforce backend readiness gates before enabling routing

**Files:**
- Modify: `apps/api/app/services/agent_config_service.py`
- Modify: `apps/api/app/services/onboarding_service.py`
- Modify: `apps/api/app/routers/agent.py`
- Modify: `apps/api/tests/agent/test_agent_config_api.py`
- Test: `apps/api/tests/agent/test_agent_config_api.py`

- [ ] **Step 1: Write the failing readiness-gate tests**
  Add coverage for enable attempts failing when:
  - no active paid subscription exists
  - provisioning has not succeeded yet
  - assigned number is missing
  - `agent_name` is still default or whitespace-only
  - `owner_context` is blank
  - both `system_prompt` and `knowledge_base` are blank after trimming

- [ ] **Step 2: Run the agent-config tests to verify failure**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/agent/test_agent_config_api.py -v`
Expected: FAIL because the backend currently checks only for phone-number presence.

- [ ] **Step 3: Implement the backend gate**
  Update the service and router so:
  - readiness checks reuse onboarding-derived subscription and provisioning state rather than only phone-number presence
  - setup completeness uses the exact spec rule
  - whitespace-only values count as incomplete
  - direct API callers get a clear readiness failure instead of opaque telephony errors

- [ ] **Step 4: Re-run the agent-config tests**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/agent/test_agent_config_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/agent_config_service.py apps/api/app/services/onboarding_service.py apps/api/app/routers/agent.py apps/api/tests/agent/test_agent_config_api.py
git commit -m "feat: enforce self-serve readiness gates before routing enable"
```

## Chunk 4: Dashboard UX For One-Plan, One-Pipeline, Onboarding-First Launch

### Task 7: Add onboarding status to the dashboard

**Files:**
- Create: `apps/web/src/lib/types/onboarding.ts`
- Create: `apps/web/src/lib/api/onboarding.ts`
- Create: `apps/web/src/components/dashboard/onboarding-status-card.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/page.tsx`
- Modify: `apps/web/src/components/dashboard/setup-checklist.tsx`
- Modify: `apps/web/src/components/dashboard/status-summary-cards.tsx`
- Create: `apps/web/tests/app/dashboard-onboarding.test.tsx`
- Test: `apps/web/tests/app/dashboard-onboarding.test.tsx`

- [ ] **Step 1: Write the failing dashboard onboarding tests**
  Cover:
  - provisioning-in-progress state renders clearly
  - provisioning-failed state shows retry CTA and support copy
  - assigned number appears after success
  - `ready_to_enable` and `live` states are distinct

- [ ] **Step 2: Run the onboarding dashboard tests to verify failure**

Run: `cd apps/web && npm run test -- --run tests/app/dashboard-onboarding.test.tsx`
Expected: FAIL because onboarding API/types/UI do not exist.

- [ ] **Step 3: Implement the onboarding dashboard surface**
  Add the onboarding API client and card, then update the dashboard page so onboarding state is the primary readiness driver rather than inferred config-only logic.

- [ ] **Step 4: Re-run the onboarding dashboard tests**

Run: `cd apps/web && npm run test -- --run tests/app/dashboard-onboarding.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/types/onboarding.ts apps/web/src/lib/api/onboarding.ts apps/web/src/components/dashboard/onboarding-status-card.tsx apps/web/src/app/(app)/dashboard/page.tsx apps/web/src/components/dashboard/setup-checklist.tsx apps/web/src/components/dashboard/status-summary-cards.tsx apps/web/tests/app/dashboard-onboarding.test.tsx
git commit -m "feat: add onboarding status to dashboard"
```

### Task 8: Narrow the launch UI to one pipeline and one plan

**Files:**
- Modify: `apps/web/src/components/agent/agent-settings-form.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/agent/page.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/agent/actions.ts`
- Modify: `apps/web/src/components/billing/billing-actions-card.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/billing/actions.ts`
- Modify: `apps/web/src/lib/api/billing.ts`
- Modify: `apps/web/src/lib/types/billing.ts`
- Modify: `apps/web/tests/app/agent-page.test.tsx`
- Modify: `apps/web/tests/app/billing-page.test.tsx`
- Test: `apps/web/tests/app/agent-page.test.tsx`
- Test: `apps/web/tests/app/billing-page.test.tsx`

- [ ] **Step 1: Write the failing launch-scope tests**
  Add coverage for:
  - `sts` is no longer rendered as a launch choice
  - routing copy explains readiness requirements
  - billing only exposes the single `starter` checkout action
  - readiness failures return new product copy rather than “Phone number not found”

- [ ] **Step 2: Run the targeted web tests to verify failure**

Run: `cd apps/web && npm run test -- --run tests/app/agent-page.test.tsx tests/app/billing-page.test.tsx`
Expected: FAIL because the UI still exposes `sts` and the starter-only contract is not fully represented.

- [ ] **Step 3: Implement the launch-scope UI changes**
  Update agent and billing surfaces so:
  - `stt_llm_tts` is the only launch pipeline shown
  - routing cannot be framed as immediately available until onboarding is complete
  - billing exposes one subscribe action for `starter`
  - shared frontend billing actions and API helpers no longer advertise `standard`
  - new backend readiness failures map to specific customer-facing messages

- [ ] **Step 4: Re-run the targeted web tests**

Run: `cd apps/web && npm run test -- --run tests/app/agent-page.test.tsx tests/app/billing-page.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/agent/agent-settings-form.tsx apps/web/src/app/(app)/dashboard/agent/page.tsx apps/web/src/app/(app)/dashboard/agent/actions.ts apps/web/src/components/billing/billing-actions-card.tsx apps/web/src/app/(app)/dashboard/billing/actions.ts apps/web/src/lib/api/billing.ts apps/web/src/lib/types/billing.ts apps/web/tests/app/agent-page.test.tsx apps/web/tests/app/billing-page.test.tsx
git commit -m "feat: narrow launch ui to starter and stt-llm-tts"
```

## Chunk 5: Verification, Docs, And Launch Readiness

### Task 9: Update docs and verify the full local suites

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture/backend-context.md`
- Test: `apps/api/tests/...`
- Test: `apps/web/tests/...`

- [ ] **Step 1: Update docs for the launch contract**
  Document:
  - France-only self-serve scope
  - `starter` as the only launch plan
  - `stt_llm_tts` as the only launch pipeline
  - onboarding status and retry path
  - the remaining real-provider staging checks

- [ ] **Step 2: Run the targeted backend suites**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/billing/test_stripe_webhooks.py tests/services/test_billing_session_service.py tests/services/test_onboarding_service.py tests/onboarding/test_onboarding_api.py tests/agent/test_agent_config_api.py tests/auth/test_clerk_sync.py tests/workers/test_individual_jobs.py -v`
Expected: PASS

- [ ] **Step 3: Run the targeted web suites**

Run: `cd apps/web && npm run test -- --run tests/app/dashboard-onboarding.test.tsx tests/app/agent-page.test.tsx tests/app/billing-page.test.tsx`
Expected: PASS

- [ ] **Step 4: Run full app verification**

Run: `cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -v`
Expected: PASS

Run: `cd apps/web && npm run test -- --run && npm run lint && npm run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md docs/architecture/backend-context.md
git commit -m "docs: record self-serve france mvp contract and verification"
```

### Task 10: Execute manual staging smoke for the real self-serve path

**Files:**
- Modify: `docs/architecture/backend-context.md`
- Modify: `README.md` if launch instructions change

- [ ] **Step 1: Run the real self-serve acceptance path in staging**
  Verify:
  - new Clerk user signs up
  - user starts `starter` checkout
  - first fresh `invoice.paid` activates subscription and starts provisioning
  - French number is assigned automatically
  - dashboard shows the assigned number and `setup_required`
  - user completes setup
  - user enables routing
  - one real inbound call produces transcript, summary, recording metadata, and minute deduction

- [ ] **Step 2: Capture exact evidence**
  Record:
  - Stripe event ids used
  - assigned French number
  - final onboarding state
  - call id and usage-ledger entry
  - any operational caveats

- [ ] **Step 3: Update `backend-context.md` with what was truly verified**

- [ ] **Step 4: Decide go/no-go**
  The branch is launch-ready only when the full self-serve path works without staff intervention.

- [ ] **Step 5: Commit**

```bash
git add docs/architecture/backend-context.md README.md
git commit -m "docs: record self-serve france staging smoke results"
```

## Notes

- Keep migrations and new persistence minimal: add only the fields needed to derive onboarding state and retryability.
- Do not expand back to `standard` or `sts` during this plan.
- Do not call the work complete until Task 10 is executed with real Stripe and Telnyx credentials.
