# Stripe Basil Minute Grant and Agent Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make current Stripe `invoice.paid` events activate the starter subscription and grant 60 minutes exactly once, repair the affected invoice grant, and run the local LiveKit voice worker.

**Architecture:** Keep payment admission at the verified Stripe webhook boundary and make invoice status `paid` the only payment-policy input. Reuse `UsageAccountingService.grant_invoice` for the production repair so the invoice ID remains the locking and idempotency key. Start the existing Compose `voice` profile without changing agent behavior or SDK usage.

**Tech Stack:** Python 3.13, FastAPI, Stripe webhooks, SQLAlchemy async sessions, PostgreSQL 17, pytest, Docker Compose, LiveKit Agents.

## Global Constraints

- Support the current Stripe `2025-08-27.basil` invoice payload; do not add legacy Invoice `paid`-field compatibility.
- A verified `invoice.paid` event grants minutes only when the invoice object has `status == "paid"`.
- Preserve invoice-ID idempotency and the existing starter allocation of 60 minutes.
- Do not modify LiveKit agent behavior or SDK APIs.
- Do not stage or alter the user's unrelated untracked files.

---

### Task 1: Accept current Stripe paid invoices

**Files:**
- Modify: `apps/api/tests/services/test_subscription_access_policy.py:31-52`
- Modify: `apps/api/tests/billing/test_stripe_webhooks.py:352-409`
- Modify: `apps/api/app/services/subscription_access_policy.py:14-16`
- Modify: `apps/api/app/services/billing_service.py:183-194`

**Interfaces:**
- Consumes: verified Stripe event routing through `BillingService.handle_event(envelope: dict) -> bool`
- Produces: `SubscriptionAccessPolicy.should_grant_invoice(invoice_status: str) -> bool`
- Preserves: `UsageAccountingService.grant_invoice(*, user_id: UUID, invoice_id: str, minutes: int) -> UsageGrantResult`

- [ ] **Step 1: Change the policy test to express the current Stripe contract**

Replace the invoice policy parameterization with:

```python
@pytest.mark.parametrize(
    ("invoice_status", "should_grant"),
    [
        ("paid", True),
        ("open", False),
        ("draft", False),
        ("void", False),
        ("uncollectible", False),
        ("unknown", False),
    ],
)
def test_should_grant_invoice_requires_paid_status(
    invoice_status: str,
    should_grant: bool,
) -> None:
    assert SubscriptionAccessPolicy.should_grant_invoice(invoice_status) is should_grant
```

- [ ] **Step 2: Make the webhook regression fixture Basil-shaped**

In `test_invoice_paid_bootstraps_subscription_activation_and_enqueues_provisioning`, remove the legacy field and assert the allocation explicitly:

```python
invoice_payload = json.loads(json.dumps(stripe_invoice_paid_payload))
invoice_payload["data"]["object"].pop("paid")
invoice_payload["data"]["object"]["lines"]["data"][0]["price"] = {
    "lookup_key": "starter"
}
```

Add these assertions after state is fetched:

```python
assert subscriptions[0].allocated_minutes == 60
assert ledgers[-1].minutes_delta == 60
assert ledgers[-1].balance_after == 60
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/services/test_subscription_access_policy.py \
  tests/billing/test_stripe_webhooks.py::test_invoice_paid_bootstraps_subscription_activation_and_enqueues_provisioning
```

Expected: the policy test errors because the current method still requires `paid`, and the webhook test fails because the absent field is converted to false and no subscription/ledger is created.

- [ ] **Step 4: Implement the minimal status-only payment policy**

Change `SubscriptionAccessPolicy` to:

```python
@staticmethod
def should_grant_invoice(invoice_status: str) -> bool:
    return invoice_status == "paid"
```

Change the billing guard to:

```python
if not SubscriptionAccessPolicy.should_grant_invoice(
    event_object.get("status", ""),
):
    return
```

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run the Step 3 command again.

Expected: all policy cases and the Basil webhook regression pass.

- [ ] **Step 6: Run the relevant API quality gates**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/services/test_subscription_access_policy.py \
  tests/billing/test_stripe_webhooks.py \
  tests/integration/test_usage_concurrency.py \
  tests/livekit/test_durable_dispatch_service.py \
  tests/workers/test_livekit_dispatch_outbox.py
```

Expected: Ruff and mypy exit zero; all selected tests pass with zero failures.

- [ ] **Step 7: Commit the tested billing fix**

```bash
git add \
  apps/api/app/services/subscription_access_policy.py \
  apps/api/app/services/billing_service.py \
  apps/api/tests/services/test_subscription_access_policy.py \
  apps/api/tests/billing/test_stripe_webhooks.py
git commit -m "fix: grant minutes for Stripe Basil paid invoices"
```

---

### Task 2: Repair the missed invoice grant idempotently

**Files:**
- Modify: none
- Runtime state: PostgreSQL `usage_ledgers`

**Interfaces:**
- Consumes: user `18f6daad-f288-48d5-bf48-8faa2309169e`, invoice `in_1Tt4mtFUdMpDTQmClZA1X7UM`, and `Subscription.allocated_minutes`
- Produces: one `UsageLedger` row with `source_id == "in_1Tt4mtFUdMpDTQmClZA1X7UM"` and `balance_after == 60`

- [ ] **Step 1: Reconfirm that the invoice has no existing grant**

Run:

```bash
docker exec bmad-opevo-postgres-1 psql -U postgres -d ai_call -v ON_ERROR_STOP=1 -x -c \
  "SELECT id, event_type, source_id, minutes_delta, balance_after
   FROM usage_ledgers
   WHERE source_id = 'in_1Tt4mtFUdMpDTQmClZA1X7UM';"
```

Expected before repair: zero rows.

- [ ] **Step 2: Grant the configured allocation through the accounting service**

Run this idempotent application-service operation from the API container:

```bash
docker exec bmad-opevo-api-1 /app/.venv/bin/python -c "import asyncio; exec('from uuid import UUID\nfrom app.core.database import get_session_factory\nfrom app.repositories.subscription_repository import SubscriptionRepository\nfrom app.services.usage_accounting_service import UsageAccountingService\n\nasync def main():\n    user_id = UUID(\"18f6daad-f288-48d5-bf48-8faa2309169e\")\n    async with get_session_factory()() as session:\n        subscription = await SubscriptionRepository(session).get_by_user_id(user_id)\n        if subscription is None or subscription.allocated_minutes <= 0:\n            raise RuntimeError(\"eligible subscription allocation not found\")\n        result = await UsageAccountingService(session).grant_invoice(\n            user_id=user_id,\n            invoice_id=\"in_1Tt4mtFUdMpDTQmClZA1X7UM\",\n            minutes=subscription.allocated_minutes,\n        )\n        await session.commit()\n        print({\"ledger_id\": str(result.ledger.id), \"already_granted\": result.already_granted, \"balance_after\": result.ledger.balance_after})\n\nasyncio.run(main())')"
```

Expected on first execution: `already_granted` is false and `balance_after` is 60. A rerun would return the same ledger with `already_granted` true.

- [ ] **Step 3: Verify the repaired ledger and routing inputs**

Run:

```bash
docker exec bmad-opevo-postgres-1 psql -U postgres -d ai_call -v ON_ERROR_STOP=1 -x -c \
  "SELECT event_type, source_id, minutes_delta, balance_after
   FROM usage_ledgers
   WHERE user_id = '18f6daad-f288-48d5-bf48-8faa2309169e'
   ORDER BY created_at DESC, id DESC;
   SELECT s.status AS subscription_status,
          p.is_active AS phone_active,
          p.provider_connection_name,
          a.is_enabled AS agent_enabled,
          (SELECT balance_after FROM usage_ledgers ul
           WHERE ul.user_id = s.user_id AND ul.balance_after IS NOT NULL
           ORDER BY ul.created_at DESC, ul.id DESC LIMIT 1) AS minute_balance
   FROM subscriptions s
   JOIN phone_numbers p ON p.user_id = s.user_id
   JOIN agent_configs a ON a.user_id = s.user_id
   WHERE s.user_id = '18f6daad-f288-48d5-bf48-8faa2309169e';"
```

Expected: exactly one row for the invoice, `subscription_status=active`, `phone_active=true`, `provider_connection_name=app-active`, `agent_enabled=true`, and `minute_balance=60`.

---

### Task 3: Start and verify the LiveKit voice worker

**Files:**
- Modify: none
- Runtime state: Docker Compose service `agent`

**Interfaces:**
- Consumes: `apps/agent/.env`, API/worker services, and Compose profile `voice`
- Produces: running container `bmad-opevo-agent-1` registered as the configured `LIVEKIT_AGENT_NAME`

- [ ] **Step 1: Validate required agent environment values without printing secrets**

Run:

```bash
cd apps/agent
/usr/bin/env bash -c 'for name in LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET LIVEKIT_AGENT_NAME AGENT_INTERNAL_API_TOKEN; do value=$(sed -n "s/^${name}=//p" .env | tail -n 1); test -n "$value" || { echo "$name missing"; exit 1; }; echo "$name set"; done'
```

Expected: each required name reports `set`; no secret value is printed.

- [ ] **Step 2: Start the voice-agent service**

Run:

```bash
docker compose -f compose.dev.yaml --profile voice up -d --build agent
```

Expected: the agent image builds and `bmad-opevo-agent-1` starts.

- [ ] **Step 3: Verify container state and registration logs**

Run:

```bash
docker ps --filter name=bmad-opevo-agent-1 --filter status=running \
  --format '{{.Names}}\t{{.Status}}'
docker logs --since 5m bmad-opevo-agent-1
```

Expected: one running container and LiveKit worker startup/registration output with no runtime-validation, authentication, or immediate provider failure.

- [ ] **Step 4: Verify the final database readiness state**

Run the Task 2 Step 3 query again.

Expected: the balance and routing inputs remain ready after agent startup.
