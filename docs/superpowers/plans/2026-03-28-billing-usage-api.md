# Billing And Usage API Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the product-facing billing and usage backend APIs, including hosted Stripe session endpoints and read endpoints for subscription and usage state.

**Architecture:** Keep Stripe as the source of truth for billing actions. Build a read-side query service for subscription and usage data, plus a separate session service for hosted Checkout and Billing Portal creation. Reuse the existing Stripe webhook sync path so user-facing actions stay decoupled from subscription persistence.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, Pydantic, Stripe Python SDK, pytest

---

## File Map

- Modify: `apps/api/app/core/config.py`
  - Add Stripe secret key, plan price ids, and checkout URL settings.
- Modify: `apps/api/app/routers/billing.py`
  - Add all billing read/action endpoints.
- Create: `apps/api/app/schemas/billing_api.py`
  - Define request and response contracts for subscription, usage, usage ledger, checkout session, and portal session.
- Create: `apps/api/app/services/billing_query_service.py`
  - Assemble read-side subscription, usage, and ledger responses.
- Create: `apps/api/app/services/billing_session_service.py`
  - Create hosted Stripe Checkout and Billing Portal sessions.
- Modify: `apps/api/app/repositories/subscription_repository.py`
  - Add lookup by `user_id`.
- Modify: `apps/api/app/repositories/usage_repository.py`
  - Add recent ledger listing by `user_id`.
- Modify: `apps/api/.env.example`
  - Document new Stripe API settings.
- Modify: `docs/architecture/backend-context.md`
  - Record the new billing/usage API surface.
- Modify: `docs/architecture/staging-smoke-runbook.md`
  - Add usage API and hosted Stripe session verification steps.
- Create: `apps/api/tests/billing/test_billing_api.py`
  - Cover read and action endpoints.
- Create: `apps/api/tests/services/test_billing_query_service.py`
  - Cover read-side aggregation logic.
- Create: `apps/api/tests/services/test_billing_session_service.py`
  - Cover Stripe session creation logic and failure cases.

## Chunk 1: Read-Side Billing Queries

### Task 1: Add repository read helpers

**Files:**
- Modify: `apps/api/app/repositories/subscription_repository.py`
- Modify: `apps/api/app/repositories/usage_repository.py`
- Test: `apps/api/tests/services/test_billing_query_service.py`

- [ ] **Step 1: Write the failing repository/query service test**

```python
@pytest.mark.anyio
async def test_get_usage_snapshot_returns_subscription_and_balance(session):
    ...
    result = await BillingQueryService(session).get_usage_snapshot(user_id)
    assert result.minutes_remaining == 58
    assert result.plan_tier == "starter"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/services/test_billing_query_service.py -v`
Expected: FAIL because `BillingQueryService` and repository helpers do not exist.

- [ ] **Step 3: Add minimal repository helpers**

Implement:
- `SubscriptionRepository.get_by_user_id(user_id)`
- `UsageRepository.list_recent_by_user_id(user_id, limit)`

- [ ] **Step 4: Run test to verify repository wiring still fails for the missing service**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/services/test_billing_query_service.py -v`
Expected: FAIL only on missing `BillingQueryService` behavior.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/repositories/subscription_repository.py apps/api/app/repositories/usage_repository.py apps/api/tests/services/test_billing_query_service.py
git commit -m "feat: add billing query repository helpers"
```

### Task 2: Build `BillingQueryService`

**Files:**
- Create: `apps/api/app/services/billing_query_service.py`
- Create: `apps/api/app/schemas/billing_api.py`
- Test: `apps/api/tests/services/test_billing_query_service.py`

- [ ] **Step 1: Expand the failing test for all read-side outputs**

```python
@pytest.mark.anyio
async def test_get_subscription_returns_none_when_missing(): ...

@pytest.mark.anyio
async def test_get_usage_ledger_returns_newest_first_with_limit(): ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/services/test_billing_query_service.py -v`
Expected: FAIL because the service and response models are incomplete.

- [ ] **Step 3: Implement the minimal read-side service**

Implement:
- `get_subscription(user_id)`
- `get_usage_snapshot(user_id)`
- `get_usage_ledger(user_id, limit)`

Use the latest `balance_after` from usage ledgers and the current subscription row when present.

- [ ] **Step 4: Run test to verify it passes**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/services/test_billing_query_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/billing_query_service.py apps/api/app/schemas/billing_api.py apps/api/tests/services/test_billing_query_service.py
git commit -m "feat: add billing query service"
```

## Chunk 2: Hosted Stripe Session Actions

### Task 3: Add billing config and Stripe session service

**Files:**
- Modify: `apps/api/app/core/config.py`
- Create: `apps/api/app/services/billing_session_service.py`
- Test: `apps/api/tests/services/test_billing_session_service.py`

- [ ] **Step 1: Write the failing Stripe session service tests**

```python
def test_create_checkout_session_uses_price_mapping(): ...

def test_create_portal_session_requires_customer_id(): ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/services/test_billing_session_service.py -v`
Expected: FAIL because config and service do not exist.

- [ ] **Step 3: Implement minimal session service**

Implement:
- Stripe config fields:
  - `stripe_secret_key`
  - `stripe_price_starter`
  - `stripe_price_standard`
  - `stripe_checkout_success_url`
  - `stripe_checkout_cancel_url`
- `BillingSessionService.create_checkout_session(...)`
- `BillingSessionService.create_portal_session(...)`

Use plan-tier-to-price-id mapping inside the service and raise clear exceptions for missing config or invalid state.

- [ ] **Step 4: Run test to verify it passes**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/services/test_billing_session_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/core/config.py apps/api/app/services/billing_session_service.py apps/api/tests/services/test_billing_session_service.py
git commit -m "feat: add billing session service"
```

## Chunk 3: Billing Router

### Task 4: Implement billing read endpoints

**Files:**
- Modify: `apps/api/app/routers/billing.py`
- Test: `apps/api/tests/billing/test_billing_api.py`

- [ ] **Step 1: Write the failing API tests for read endpoints**

```python
def test_get_subscription_returns_null_for_new_user(client): ...

def test_get_usage_returns_zeroed_snapshot_without_subscription(client): ...

def test_get_usage_ledger_returns_recent_entries(client): ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/billing/test_billing_api.py -v`
Expected: FAIL because the routes are not implemented.

- [ ] **Step 3: Implement minimal read endpoints**

Wire `require_user_identity` into the router and delegate to `BillingQueryService`.

- [ ] **Step 4: Run test to verify it passes**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/billing/test_billing_api.py -v`
Expected: PASS for read endpoint cases

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routers/billing.py apps/api/tests/billing/test_billing_api.py
git commit -m "feat: add billing read api"
```

### Task 5: Implement checkout and portal endpoints

**Files:**
- Modify: `apps/api/app/routers/billing.py`
- Test: `apps/api/tests/billing/test_billing_api.py`

- [ ] **Step 1: Add failing API tests for hosted Stripe actions**

```python
def test_create_checkout_session_returns_url(client): ...

def test_create_checkout_session_rejects_active_subscription(client): ...

def test_create_portal_session_returns_url(client): ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/billing/test_billing_api.py -v`
Expected: FAIL on missing action endpoints and error handling.

- [ ] **Step 3: Implement minimal action endpoints**

Implement:
- `POST /api/billing/checkout-session`
- `POST /api/billing/portal-session`

Map service exceptions to:
- `409` for invalid billing state
- `502` for Stripe upstream errors

- [ ] **Step 4: Run test to verify it passes**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/billing/test_billing_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routers/billing.py apps/api/tests/billing/test_billing_api.py
git commit -m "feat: add hosted billing session api"
```

## Chunk 4: Docs And Verification

### Task 6: Document config and API usage

**Files:**
- Modify: `apps/api/.env.example`
- Modify: `docs/architecture/backend-context.md`
- Modify: `docs/architecture/staging-smoke-runbook.md`

- [ ] **Step 1: Add failing deployment-readiness expectation if needed**

If config documentation is covered by deployment-readiness tests, update tests first.

- [ ] **Step 2: Update docs minimally**

Document:
- new Stripe env vars
- endpoint usage
- staging steps for hosted Checkout and Billing Portal

- [ ] **Step 3: Run any affected tests**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/test_deployment_readiness.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add apps/api/.env.example docs/architecture/backend-context.md docs/architecture/staging-smoke-runbook.md
git commit -m "docs: add billing usage api notes"
```

### Task 7: Final verification

**Files:**
- Test: `apps/api/tests/services/test_billing_query_service.py`
- Test: `apps/api/tests/services/test_billing_session_service.py`
- Test: `apps/api/tests/billing/test_billing_api.py`

- [ ] **Step 1: Run focused billing tests**

Run:
`UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/services/test_billing_query_service.py tests/services/test_billing_session_service.py tests/billing/test_billing_api.py -v`

Expected: PASS

- [ ] **Step 2: Run the full API suite**

Run:
`UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -v`

Expected: PASS

- [ ] **Step 3: Confirm git status**

Run:
`git status --short`

Expected: only intended tracked changes plus local untracked env files.

