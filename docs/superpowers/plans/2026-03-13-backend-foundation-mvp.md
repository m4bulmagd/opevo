# Backend Foundation MVP Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deployment-ready backend MVP for AI Call Assistant, including the FastAPI API, the production LiveKit agent worker, real provider wiring, persistent call state, and post-call processing.

**Architecture:** The implementation uses a modular monorepo with two runtime apps: `apps/api` for HTTP, persistence, webhooks, WebSocket fanout, and async jobs, plus `apps/agent` for real-time call execution in LiveKit. Postgres is the source of truth, Redis carries transient real-time state and job coordination, and external providers are isolated behind narrow integration layers where swaps are expected.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy, Alembic, Redis, ARQ, LiveKit Agents, Clerk, Stripe, Telnyx, S3-compatible storage, pytest, uv

---

Use `@superpowers/test-driven-development` while executing every task. Before claiming any task complete, run the listed checks and follow `@superpowers/verification-before-completion`.

## Skill Mapping

- Use `@superpowers/test-driven-development` across the full plan.
- Use `@superpowers/systematic-debugging` for any failing test, provider integration issue, runtime crash, or unexpected call-path behavior before attempting fixes.
- Use local `.agents/skills/livekit-agents` for LiveKit-specific implementation work, especially Chunk 2 Task 8 and Chunk 3 Tasks 9-10.
- Use local `.agents/skills/redis-development` for Redis key design, fanout, buffering, pub/sub, and connection handling, especially Chunk 2 Task 7 and Chunk 3 Task 11.
- Do not apply the frontend-oriented local skills during this backend execution phase.

## File Structure

### Root

- Create: `.gitignore`
- Create: `README.md`
- Create: `docs/architecture/backend-context.md`

### API Application

- Create: `apps/api/pyproject.toml`
- Create: `apps/api/.env.example`
- Create: `apps/api/alembic.ini`
- Create: `apps/api/Dockerfile`
- Create: `apps/api/app/main.py`
- Create: `apps/api/app/core/config.py`
- Create: `apps/api/app/core/database.py`
- Create: `apps/api/app/core/redis.py`
- Create: `apps/api/app/core/logging.py`
- Create: `apps/api/app/core/auth.py`
- Create: `apps/api/app/models/user.py`
- Create: `apps/api/app/models/subscription.py`
- Create: `apps/api/app/models/usage_ledger.py`
- Create: `apps/api/app/models/phone_number.py`
- Create: `apps/api/app/models/agent_config.py`
- Create: `apps/api/app/models/call.py`
- Create: `apps/api/app/models/call_message.py`
- Create: `apps/api/app/models/notification.py`
- Create: `apps/api/app/models/webhook_event.py`
- Create: `apps/api/app/models/__init__.py`
- Create: `apps/api/app/schemas/auth.py`
- Create: `apps/api/app/schemas/billing.py`
- Create: `apps/api/app/schemas/calls.py`
- Create: `apps/api/app/schemas/livekit.py`
- Create: `apps/api/app/schemas/realtime.py`
- Create: `apps/api/app/schemas/telephony.py`
- Create: `apps/api/app/repositories/user_repository.py`
- Create: `apps/api/app/repositories/subscription_repository.py`
- Create: `apps/api/app/repositories/usage_repository.py`
- Create: `apps/api/app/repositories/phone_number_repository.py`
- Create: `apps/api/app/repositories/agent_config_repository.py`
- Create: `apps/api/app/repositories/call_repository.py`
- Create: `apps/api/app/repositories/message_repository.py`
- Create: `apps/api/app/repositories/notification_repository.py`
- Create: `apps/api/app/repositories/webhook_event_repository.py`
- Create: `apps/api/app/services/auth_service.py`
- Create: `apps/api/app/services/billing_service.py`
- Create: `apps/api/app/services/telephony_service.py`
- Create: `apps/api/app/services/livekit_dispatch_service.py`
- Create: `apps/api/app/services/call_lifecycle_service.py`
- Create: `apps/api/app/services/realtime_service.py`
- Create: `apps/api/app/services/recording_service.py`
- Create: `apps/api/app/services/summary_service.py`
- Create: `apps/api/app/services/notification_service.py`
- Create: `apps/api/app/providers/telephony/base.py`
- Create: `apps/api/app/providers/telephony/telnyx.py`
- Create: `apps/api/app/providers/telephony/twilio.py`
- Create: `apps/api/app/providers/storage/base.py`
- Create: `apps/api/app/providers/storage/s3.py`
- Create: `apps/api/app/providers/notifications/base.py`
- Create: `apps/api/app/providers/notifications/firebase.py`
- Create: `apps/api/app/routers/auth.py`
- Create: `apps/api/app/routers/agent.py`
- Create: `apps/api/app/routers/billing.py`
- Create: `apps/api/app/routers/calls.py`
- Create: `apps/api/app/routers/health.py`
- Create: `apps/api/app/routers/websocket.py`
- Create: `apps/api/app/webhooks/clerk.py`
- Create: `apps/api/app/webhooks/stripe.py`
- Create: `apps/api/app/webhooks/livekit.py`
- Create: `apps/api/app/workers/arq_worker.py`
- Create: `apps/api/app/workers/jobs/transcript_flush.py`
- Create: `apps/api/app/workers/jobs/summary.py`
- Create: `apps/api/app/workers/jobs/recording.py`
- Create: `apps/api/app/workers/jobs/notifications.py`
- Create: `apps/api/app/websockets/manager.py`
- Create: `apps/api/alembic/env.py`
- Create: `apps/api/alembic/versions/0001_initial_schema.py`
- Create: `apps/api/tests/conftest.py`
- Create: `apps/api/tests/test_health.py`
- Create: `apps/api/tests/auth/test_clerk_sync.py`
- Create: `apps/api/tests/auth/test_jwt_auth.py`
- Create: `apps/api/tests/billing/test_stripe_webhooks.py`
- Create: `apps/api/tests/telephony/test_telnyx_provider.py`
- Create: `apps/api/tests/livekit/test_dispatch_webhook.py`
- Create: `apps/api/tests/realtime/test_websocket_auth.py`
- Create: `apps/api/tests/calls/test_call_lifecycle.py`
- Create: `apps/api/tests/workers/test_post_call_jobs.py`

### Agent Application

- Create: `apps/agent/pyproject.toml`
- Create: `apps/agent/.env.example`
- Create: `apps/agent/Dockerfile`
- Create: `apps/agent/agent/main.py`
- Create: `apps/agent/agent/providers.py`
- Create: `apps/agent/agent/pipeline_factory.py`
- Create: `apps/agent/agent/prompt_builder.py`
- Create: `apps/agent/agent/agent_scripts.py`
- Create: `apps/agent/agent/event_publisher.py`
- Create: `apps/agent/agent/session_runtime.py`
- Create: `apps/agent/tests/test_prompt_builder.py`
- Create: `apps/agent/tests/test_pipeline_factory.py`
- Create: `apps/agent/tests/test_session_runtime.py`

## Chunk 1: Repository And API Foundation

### Task 1: Scaffold The Monorepo And Tooling

**Files:**
- Create: `.gitignore`
- Create: `README.md`
- Create: `docs/architecture/backend-context.md`
- Create: `apps/api/pyproject.toml`
- Create: `apps/agent/pyproject.toml`
- Create: `apps/api/tests/test_health.py`
- Create: `apps/agent/tests/test_prompt_builder.py`

- [ ] **Step 1: Write the first failing API smoke test**

```python
from fastapi.testclient import TestClient

from app.main import app


def test_healthcheck_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run the API smoke test to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_health.py -v`
Expected: FAIL because `app.main` or `/healthz` does not exist yet.

- [ ] **Step 3: Create the repo skeleton and package metadata**

Create the root docs plus `apps/api/pyproject.toml` and `apps/agent/pyproject.toml` with the base runtime dependencies only:
- API: `fastapi`, `uvicorn`, `sqlalchemy`, `alembic`, `redis`, `arq`, `pydantic-settings`, `pytest`, `httpx`
- Agent: `livekit-agents`, provider SDKs, `pytest`

- [ ] **Step 4: Add the minimal FastAPI app with `/healthz`**

```python
from fastapi import FastAPI

app = FastAPI()


@app.get("/healthz")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 5: Re-run the smoke test**

Run: `cd apps/api && uv run pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add .gitignore README.md docs/architecture/backend-context.md apps/api apps/agent
git commit -m "chore: scaffold backend monorepo"
```

### Task 2: Build API Core Infrastructure

**Files:**
- Create: `apps/api/.env.example`
- Create: `apps/api/Dockerfile`
- Create: `apps/api/app/main.py`
- Create: `apps/api/app/core/config.py`
- Create: `apps/api/app/core/database.py`
- Create: `apps/api/app/core/redis.py`
- Create: `apps/api/app/core/logging.py`
- Create: `apps/api/tests/conftest.py`

- [ ] **Step 1: Write failing tests for settings loading and app startup**

```python
def test_settings_load_required_fields(settings):
    assert settings.app_env == "test"
    assert settings.database_url.startswith("postgresql")
```

- [ ] **Step 2: Run the settings test to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_health.py tests/conftest.py -v`
Expected: FAIL because settings fixtures and core modules do not exist.

- [ ] **Step 3: Implement typed settings, DB session factory, Redis factory, and logging bootstrap**

Requirements:
- `config.py` uses `pydantic-settings`
- `database.py` exposes async engine and session dependency
- `redis.py` exposes a single client factory
- `logging.py` configures JSON-friendly structured logging

- [ ] **Step 4: Update `main.py` to use lifespan startup/shutdown hooks**

Ensure startup validates required config and initializes shared clients lazily.

- [ ] **Step 5: Re-run the API foundation tests**

Run: `cd apps/api && uv run pytest tests/test_health.py -v`
Expected: PASS with startup wiring intact.

- [ ] **Step 6: Build the API image once**

Run: `cd apps/api && docker build -t ai-call-api .`
Expected: Docker build completes successfully.

- [ ] **Step 7: Commit**

```bash
git add apps/api
git commit -m "chore: add api core infrastructure"
```

### Task 3: Create The Initial Schema And Repositories

**Files:**
- Create: `apps/api/alembic.ini`
- Create: `apps/api/alembic/env.py`
- Create: `apps/api/alembic/versions/0001_initial_schema.py`
- Create: `apps/api/app/models/user.py`
- Create: `apps/api/app/models/subscription.py`
- Create: `apps/api/app/models/usage_ledger.py`
- Create: `apps/api/app/models/phone_number.py`
- Create: `apps/api/app/models/agent_config.py`
- Create: `apps/api/app/models/call.py`
- Create: `apps/api/app/models/call_message.py`
- Create: `apps/api/app/models/notification.py`
- Create: `apps/api/app/models/webhook_event.py`
- Create: `apps/api/app/models/__init__.py`
- Create: `apps/api/app/repositories/user_repository.py`
- Create: `apps/api/app/repositories/subscription_repository.py`
- Create: `apps/api/app/repositories/usage_repository.py`
- Create: `apps/api/app/repositories/phone_number_repository.py`
- Create: `apps/api/app/repositories/agent_config_repository.py`
- Create: `apps/api/app/repositories/call_repository.py`
- Create: `apps/api/app/repositories/message_repository.py`
- Create: `apps/api/app/repositories/notification_repository.py`
- Create: `apps/api/app/repositories/webhook_event_repository.py`
- Create: `apps/api/tests/calls/test_call_lifecycle.py`

- [ ] **Step 1: Write the failing repository test for creating a user and default agent config**

```python
async def test_create_user_and_default_agent_config(user_repository, agent_config_repository):
    user = await user_repository.create(clerk_user_id="user_123", email="test@example.com")
    config = await agent_config_repository.create_default(user.id)
    assert config.user_id == user.id
    assert config.pipeline_mode == "stt_llm_tts"
```

- [ ] **Step 2: Run the repository test to verify it fails**

Run: `cd apps/api && uv run pytest tests/calls/test_call_lifecycle.py -v`
Expected: FAIL because models, migrations, and repositories do not exist.

- [ ] **Step 3: Implement SQLAlchemy models and the initial Alembic migration**

Schema requirements:
- UUID primary keys where practical
- unique constraints on Clerk user id, Stripe ids, and phone numbers
- indexed lookup path for DID-to-user resolution
- indexed `webhook_events.provider + webhook_events.external_event_id`

- [ ] **Step 4: Implement repository methods needed by the tests**

Start with:
- `UserRepository.create()`
- `UserRepository.get_by_clerk_user_id()`
- `AgentConfigRepository.create_default()`
- `PhoneNumberRepository.get_by_e164()`
- `CallRepository.create_pending()`
- `WebhookEventRepository.record_if_new()`

- [ ] **Step 5: Run migrations and the repository tests**

Run: `cd apps/api && uv run alembic upgrade head`
Expected: Database schema applies cleanly.

Run: `cd apps/api && uv run pytest tests/calls/test_call_lifecycle.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/api
git commit -m "feat: add core schema and repositories"
```

### Task 4: Implement Clerk Sync And Request Authentication

**Files:**
- Create: `apps/api/app/core/auth.py`
- Create: `apps/api/app/schemas/auth.py`
- Create: `apps/api/app/services/auth_service.py`
- Create: `apps/api/app/routers/auth.py`
- Create: `apps/api/app/webhooks/clerk.py`
- Create: `apps/api/tests/auth/test_clerk_sync.py`
- Create: `apps/api/tests/auth/test_jwt_auth.py`

- [ ] **Step 1: Write the failing Clerk sync webhook test**

```python
def test_clerk_user_created_webhook_upserts_local_user(client, signed_clerk_headers, clerk_user_created_payload):
    response = client.post("/webhooks/clerk", json=clerk_user_created_payload, headers=signed_clerk_headers)
    assert response.status_code == 202
```

- [ ] **Step 2: Write the failing JWT auth test**

```python
def test_protected_route_rejects_token_without_local_user(client, valid_clerk_but_missing_local_user_token):
    response = client.get("/api/agent/config", headers={"Authorization": f"Bearer {valid_clerk_but_missing_local_user_token}"})
    assert response.status_code == 401
```

- [ ] **Step 3: Run the auth tests to verify both fail**

Run: `cd apps/api && uv run pytest tests/auth/test_clerk_sync.py tests/auth/test_jwt_auth.py -v`
Expected: FAIL because Clerk webhook handling and auth middleware do not exist.

- [ ] **Step 4: Implement `AuthProvider` and `ClerkAuthProvider` in `core/auth.py`**

Requirements:
- `verify_token(token) -> UserIdentity`
- JWKS signature validation
- expiry, issuer, and audience validation
- no Clerk imports outside `core/auth.py`

- [ ] **Step 5: Implement signed Clerk webhook handling and local user upsert**

Requirements:
- validate Svix signature before processing
- record webhook idempotency in `webhook_events`
- return success on duplicate deliveries without duplicating local state

- [ ] **Step 6: Add a protected placeholder route for authenticated API access**

Use `GET /api/agent/config` as the first protected route and require a verified local user.

- [ ] **Step 7: Re-run the auth tests**

Run: `cd apps/api && uv run pytest tests/auth/test_clerk_sync.py tests/auth/test_jwt_auth.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add apps/api
git commit -m "feat: add clerk sync and request authentication"
```

## Chunk 2: Billing, Telephony, Realtime, And Call Dispatch

### Task 5: Implement Stripe Subscription And Usage Foundations

**Files:**
- Create: `apps/api/app/schemas/billing.py`
- Create: `apps/api/app/services/billing_service.py`
- Create: `apps/api/app/routers/billing.py`
- Create: `apps/api/app/webhooks/stripe.py`
- Create: `apps/api/tests/billing/test_stripe_webhooks.py`

- [ ] **Step 1: Write the failing Stripe webhook tests**

```python
def test_invoice_paid_resets_minutes(client, signed_stripe_headers, invoice_paid_payload):
    response = client.post("/webhooks/stripe", json=invoice_paid_payload, headers=signed_stripe_headers)
    assert response.status_code == 202
```

```python
def test_subscription_activation_provisions_usage_ledger(client, signed_stripe_headers, subscription_payload):
    response = client.post("/webhooks/stripe", json=subscription_payload, headers=signed_stripe_headers)
    assert response.status_code == 202
```

- [ ] **Step 2: Run the Stripe tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/billing/test_stripe_webhooks.py -v`
Expected: FAIL because Stripe webhook routes and billing service do not exist.

- [ ] **Step 3: Implement webhook signature validation and event idempotency**

Handle at minimum:
- `customer.subscription.created`
- `customer.subscription.updated`
- `invoice.paid`

- [ ] **Step 4: Implement subscription syncing and minute reset logic**

Rules:
- Starter allocates `60` minutes
- Standard allocates `120` minutes
- reset minutes on each successful billing cycle
- persist every reset and consumption event in `usage_ledgers`

- [ ] **Step 5: Re-run the billing tests**

Run: `cd apps/api && uv run pytest tests/billing/test_stripe_webhooks.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/api
git commit -m "feat: add stripe billing and usage reset handling"
```

### Task 6: Add Telnyx Provisioning And Number State Switching

**Files:**
- Create: `apps/api/app/schemas/telephony.py`
- Create: `apps/api/app/providers/telephony/base.py`
- Create: `apps/api/app/providers/telephony/telnyx.py`
- Create: `apps/api/app/providers/telephony/twilio.py`
- Create: `apps/api/app/services/telephony_service.py`
- Modify: `apps/api/app/services/billing_service.py`
- Create: `apps/api/tests/telephony/test_telnyx_provider.py`

- [ ] **Step 1: Write the failing telephony provider tests**

```python
async def test_provision_number_assigns_e164_to_user(telephony_service, active_user):
    phone_number = await telephony_service.provision_number(active_user.id, country_code="FR")
    assert phone_number.e164.startswith("+33")
```

```python
async def test_disable_number_switches_to_disabled_app(telephony_service, assigned_number):
    updated = await telephony_service.disable_number(assigned_number.user_id)
    assert updated.provider_connection_name == "app-disabled"
```

- [ ] **Step 2: Run the telephony tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/telephony/test_telnyx_provider.py -v`
Expected: FAIL because the provider interface and service do not exist.

- [ ] **Step 3: Implement `TelephonyProvider` and the Telnyx adapter**

Requirements:
- use the official async SDK
- isolate SDK imports to `providers/telephony/telnyx.py`
- expose `provision_number()`, `enable_number()`, `disable_number()`

- [ ] **Step 4: Connect subscription activation to number provisioning**

On the first active subscription for a user:
- provision the number
- persist `phone_numbers`
- create the default disabled/active state according to the business rule you choose and document

- [ ] **Step 5: Re-run the telephony tests**

Run: `cd apps/api && uv run pytest tests/telephony/test_telnyx_provider.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/api
git commit -m "feat: add telnyx provisioning and number state control"
```

### Task 7: Build The Authenticated Realtime WebSocket Path

**Files:**
- Create: `apps/api/app/schemas/realtime.py`
- Create: `apps/api/app/services/realtime_service.py`
- Create: `apps/api/app/routers/websocket.py`
- Create: `apps/api/app/websockets/manager.py`
- Create: `apps/api/tests/realtime/test_websocket_auth.py`

- [ ] **Step 1: Write the failing WebSocket auth test**

```python
def test_websocket_requires_auth_message_before_events(client, valid_local_user_token):
    with client.websocket_connect("/ws") as websocket:
        websocket.send_json({"type": "ping"})
        message = websocket.receive_json()
        assert message["type"] == "error"
```

- [ ] **Step 2: Run the WebSocket auth test to verify it fails**

Run: `cd apps/api && uv run pytest tests/realtime/test_websocket_auth.py -v`
Expected: FAIL because the websocket endpoint and manager do not exist.

- [ ] **Step 3: Implement the connection manager and first-message authentication flow**

Rules:
- first client message must be `{"type": "auth", "token": "..."}`
- unauthenticated sockets close within 3 seconds
- multiple active sockets per user are supported

- [ ] **Step 4: Implement Redis-backed fanout hooks**

Create the minimal publish/subscribe path needed for:
- `call_started`
- transcript messages
- `call_ended`

- [ ] **Step 5: Re-run the realtime tests**

Run: `cd apps/api && uv run pytest tests/realtime/test_websocket_auth.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/api
git commit -m "feat: add authenticated realtime websocket flow"
```

### Task 8: Implement LiveKit Inbound Dispatch And Call Creation

**Files:**
- Create: `apps/api/app/schemas/livekit.py`
- Create: `apps/api/app/services/livekit_dispatch_service.py`
- Create: `apps/api/app/services/call_lifecycle_service.py`
- Create: `apps/api/app/webhooks/livekit.py`
- Modify: `apps/api/app/repositories/call_repository.py`
- Modify: `apps/api/app/repositories/phone_number_repository.py`
- Create: `apps/api/tests/livekit/test_dispatch_webhook.py`

- [ ] **Step 1: Write the failing LiveKit dispatch test**

```python
def test_participant_joined_dispatches_agent_and_creates_pending_call(client, signed_livekit_headers, participant_joined_payload):
    response = client.post("/webhooks/livekit", json=participant_joined_payload, headers=signed_livekit_headers)
    assert response.status_code == 202
```

- [ ] **Step 2: Run the LiveKit test to verify it fails**

Run: `cd apps/api && uv run pytest tests/livekit/test_dispatch_webhook.py -v`
Expected: FAIL because the LiveKit webhook handler and dispatch service do not exist.

- [ ] **Step 3: Implement verified LiveKit webhook ingestion**

Requirements:
- verify signature
- ignore duplicate events
- process only the participant join event needed for inbound call start

- [ ] **Step 4: Implement DID resolution, pending call creation, and agent dispatch**

Rules:
- map the called phone number to the owning user
- load the user's agent config
- create a `calls` row before dispatch
- dispatch the worker with the minimal config payload needed for the session

- [ ] **Step 5: Publish `call_started` to the realtime layer**

Ensure all active user WebSocket sessions receive the event.

- [ ] **Step 6: Re-run the LiveKit dispatch tests**

Run: `cd apps/api && uv run pytest tests/livekit/test_dispatch_webhook.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add apps/api
git commit -m "feat: add livekit inbound dispatch flow"
```

## Chunk 3: Agent Runtime And Post-Call Processing

### Task 9: Implement Agent Prompting And Provider Selection

**Files:**
- Create: `apps/agent/agent/providers.py`
- Create: `apps/agent/agent/pipeline_factory.py`
- Create: `apps/agent/agent/prompt_builder.py`
- Create: `apps/agent/agent/agent_scripts.py`
- Create: `apps/agent/tests/test_prompt_builder.py`
- Create: `apps/agent/tests/test_pipeline_factory.py`

- [ ] **Step 1: Write the failing prompt builder tests**

```python
def test_prompt_builder_wraps_knowledge_base():
    prompt = build_system_prompt(agent_name="Ava", owner_name="Sam", knowledge_base="Hours: 9-5")
    assert "<knowledge_base>" in prompt
```

```python
def test_prompt_builder_keeps_required_disclosure():
    prompt = build_system_prompt(agent_name="Ava", owner_name="Sam", knowledge_base="")
    assert "AI assistant" in prompt
    assert "recorded" in prompt
```

- [ ] **Step 2: Run the agent prompt tests to verify they fail**

Run: `cd apps/agent && uv run pytest tests/test_prompt_builder.py tests/test_pipeline_factory.py -v`
Expected: FAIL because the builder and provider registry do not exist.

- [ ] **Step 3: Implement provider enums and pipeline selection**

Requirements:
- central provider registry in `providers.py`
- default `stt_llm_tts`
- future-ready `sts` branch that can raise a controlled "not enabled" error until used

- [ ] **Step 4: Implement the prompt builder and failure scripts**

Requirements:
- wrap knowledge base in `<knowledge_base>` tags
- hardcode AI identification and recording disclosure
- keep caller-facing fallback messages in `agent_scripts.py`

- [ ] **Step 5: Re-run the agent prompt tests**

Run: `cd apps/agent && uv run pytest tests/test_prompt_builder.py tests/test_pipeline_factory.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/agent
git commit -m "feat: add agent prompt builder and provider selection"
```

### Task 10: Implement The LiveKit Agent Session Runtime

**Files:**
- Create: `apps/agent/agent/event_publisher.py`
- Create: `apps/agent/agent/session_runtime.py`
- Create: `apps/agent/agent/main.py`
- Create: `apps/agent/tests/test_session_runtime.py`

- [ ] **Step 1: Write the failing agent runtime tests**

```python
async def test_session_runtime_publishes_transcript_events(fake_event_publisher, sample_dispatch_payload):
    runtime = SessionRuntime(fake_event_publisher)
    await runtime.handle_agent_utterance(sample_dispatch_payload, "Bonjour")
    assert fake_event_publisher.events[0]["type"] == "transcript"
```

```python
async def test_session_runtime_emits_call_end_event(fake_event_publisher, sample_dispatch_payload):
    runtime = SessionRuntime(fake_event_publisher)
    await runtime.finalize(sample_dispatch_payload, duration_seconds=61)
    assert fake_event_publisher.events[-1]["type"] == "call_ended"
```

- [ ] **Step 2: Run the agent runtime tests to verify they fail**

Run: `cd apps/agent && uv run pytest tests/test_session_runtime.py -v`
Expected: FAIL because the runtime and event publisher do not exist.

- [ ] **Step 3: Implement the Redis-backed event publisher**

Publish normalized JSON events for:
- transcript messages
- call completion payloads
- abnormal termination payloads

- [ ] **Step 4: Implement the LiveKit worker entrypoint and session runtime**

Requirements:
- accept dispatched job metadata
- build the prompt and pipeline
- join the room
- emit transcript events
- emit a normalized completion payload on both success and failure

- [ ] **Step 5: Re-run the agent runtime tests**

Run: `cd apps/agent && uv run pytest tests/test_session_runtime.py -v`
Expected: PASS

- [ ] **Step 6: Build the agent image once**

Run: `cd apps/agent && docker build -t ai-call-agent .`
Expected: Docker build completes successfully.

- [ ] **Step 7: Commit**

```bash
git add apps/agent
git commit -m "feat: add livekit agent session runtime"
```

### Task 11: Implement Post-Call Persistence And Async Jobs

**Files:**
- Create: `apps/api/app/services/recording_service.py`
- Create: `apps/api/app/services/summary_service.py`
- Create: `apps/api/app/services/notification_service.py`
- Create: `apps/api/app/workers/arq_worker.py`
- Create: `apps/api/app/workers/jobs/transcript_flush.py`
- Create: `apps/api/app/workers/jobs/summary.py`
- Create: `apps/api/app/workers/jobs/recording.py`
- Create: `apps/api/app/workers/jobs/notifications.py`
- Modify: `apps/api/app/services/realtime_service.py`
- Modify: `apps/api/app/services/call_lifecycle_service.py`
- Create: `apps/api/tests/workers/test_post_call_jobs.py`

- [ ] **Step 1: Write the failing post-call job tests**

```python
async def test_call_completion_persists_usage_and_enqueues_jobs(call_lifecycle_service, completed_call_payload):
    result = await call_lifecycle_service.finalize_call(completed_call_payload)
    assert result.minutes_charged == 2
    assert result.summary_job_enqueued is True
```

```python
async def test_minute_exhaustion_disables_number(call_lifecycle_service, exhausted_call_payload):
    result = await call_lifecycle_service.finalize_call(exhausted_call_payload)
    assert result.number_disabled is True
```

- [ ] **Step 2: Run the post-call tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/workers/test_post_call_jobs.py -v`
Expected: FAIL because finalization logic and ARQ jobs do not exist.

- [ ] **Step 3: Implement transcript flush and call finalization**

Rules:
- flush buffered transcript events into `call_messages`
- compute rounded-up charged minutes
- write usage ledger entries
- disable numbers immediately on exhaustion

- [ ] **Step 4: Implement summary, recording, and notification jobs**

Requirements:
- summary uses transcript context and stores a concise paragraph
- recording job stores metadata and signed URL inputs
- notification job emits at least call start/end notifications

- [ ] **Step 5: Re-run the post-call job tests**

Run: `cd apps/api && uv run pytest tests/workers/test_post_call_jobs.py -v`
Expected: PASS

- [ ] **Step 6: Run the broader lifecycle test set**

Run: `cd apps/api && uv run pytest tests/calls/test_call_lifecycle.py tests/workers/test_post_call_jobs.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add apps/api
git commit -m "feat: add post-call persistence and async jobs"
```

### Task 12: Finalize Deployment Readiness And Staging Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture/backend-context.md`
- Modify: `apps/api/.env.example`
- Modify: `apps/agent/.env.example`
- Modify: `apps/api/Dockerfile`
- Modify: `apps/agent/Dockerfile`

- [ ] **Step 1: Write the failing staging checklist test as documentation acceptance criteria**

Add a checklist section to `README.md` that is initially incomplete and covers:
- API starts
- agent worker starts
- Clerk webhook reaches local or staging API
- Stripe webhook resets minutes
- Telnyx number can be provisioned
- LiveKit dispatch reaches the agent

- [ ] **Step 2: Run the full automated test suites before polishing docs**

Run: `cd apps/api && uv run pytest -v`
Expected: PASS

Run: `cd apps/agent && uv run pytest -v`
Expected: PASS

- [ ] **Step 3: Complete environment examples and deployment docs**

Document required env vars for:
- Clerk
- Stripe
- Telnyx
- LiveKit
- Postgres
- Redis
- S3-compatible storage
- OpenAI / Deepgram / ElevenLabs

- [ ] **Step 4: Perform the manual staging smoke path**

Run this exact sequence in staging:
1. Create a new user through Clerk.
2. Deliver the Clerk webhook and confirm local user creation.
3. Activate a Stripe subscription and confirm minute allocation.
4. Provision a Telnyx number and confirm it is linked to the user.
5. Forward a real phone call into the number.
6. Confirm LiveKit webhook receipt, call creation, worker dispatch, transcript flow, summary generation, and usage deduction.

- [ ] **Step 5: Capture the staging results in `docs/architecture/backend-context.md`**

Record what passed, what failed, and which provider credentials or dashboards still need manual setup.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/architecture/backend-context.md apps/api/.env.example apps/agent/.env.example apps/api/Dockerfile apps/agent/Dockerfile
git commit -m "docs: finalize backend deployment readiness"
```
