# AI Call Assistant Backend Foundation Design

## Summary

This document defines the deployment-ready backend foundation for the AI Call Assistant MVP. The scope includes the production FastAPI backend, the production LiveKit agent worker, and the infrastructure contracts needed to support subscription activation, phone number assignment, inbound call handling, transcript streaming, call persistence, recordings metadata, summaries orchestration, notifications, and minute exhaustion handling.

This spec is intentionally limited to the backend foundation for the MVP. It does not include the web frontend implementation, native mobile apps, outbound calling, appointment booking, voice jump-in, or multi-agent support.

## Goals

- Build a deployment-ready backend shape for the MVP, not a local-only prototype.
- Support real inbound AI call handling with the production LiveKit worker included.
- Keep runtime boundaries explicit so HTTP/business logic and voice execution can scale independently.
- Preserve provider abstraction at the integration boundaries without adding unnecessary internal indirection.
- Make the first implementation plan concrete enough to execute in small, testable steps.

## Non-Goals

- Frontend implementation beyond the backend contracts it will consume.
- Native mobile implementation.
- Post-MVP features such as voice jump-in, outbound calling, appointment booking, or team accounts.
- Full microservice decomposition.

## Architecture

The backend will use a modular monorepo with two Python runtime applications:

- `apps/api`: FastAPI application for business logic, persistence, webhooks, WebSocket fanout, billing, telephony coordination, and operational APIs.
- `apps/agent`: LiveKit Agents worker for real-time call execution.

The applications are deployed independently but share the same product data model and infrastructure.

### Deployment Model

- Web frontend: Vercel
- Backend API: Railway
- Agent worker: Railway
- Database: PostgreSQL
- Cache / queue / fanout: Redis
- Audio rooms / SIP: LiveKit Cloud EU
- Telephony and number provisioning: Telnyx
- Billing: Stripe
- Identity: Clerk
- Recording storage: S3-compatible object storage

### Critical Runtime Boundary

Telnyx and LiveKit handle inbound call setup directly. FastAPI is not on the SIP setup path. The API reacts to LiveKit webhooks, resolves the user from the called number, dispatches the agent worker with the correct configuration, persists call state, and orchestrates post-call processing.

This avoids putting the HTTP backend in the call setup critical path and allows the API and voice worker pools to scale separately.

## Repository Structure

The initial monorepo structure should follow this shape:

```text
apps/
  api/
    app/
      core/
      models/
      schemas/
      repositories/
      services/
      routers/
      webhooks/
      providers/
      workers/
      websockets/
  agent/
    agent/
      main.py
      providers.py
      pipeline_factory.py
      prompt_builder.py
      agent_scripts.py
      injection_handler.py
      jump_in_handler.py
docs/
  superpowers/
    specs/
    plans/
```

The API owns business coordination and persistence. The agent owns live call execution only.

## Core Data Model

PostgreSQL is the system of record. Redis is used only for transient real-time state.

### Postgres Tables

#### `users`

- Internal application user record keyed to Clerk `user_id`
- Stores profile data and account status

#### `subscriptions`

- Stripe customer and subscription linkage
- Plan tier
- Billing period start/end
- Subscription status

#### `usage_ledgers`

- Minute allocation and consumption events
- Reset events from Stripe billing cycle renewal
- Exhaustion events

#### `phone_numbers`

- Telnyx number assignment
- Provider metadata
- Active / disabled state
- Ownership linkage to user

#### `agent_configs`

- Agent display name
- Owner context
- Editable system prompt
- Knowledge base plain text
- Provider choices
- Pipeline mode
- Enabled flag

#### `calls`

- User and phone number linkage
- LiveKit room id
- Caller number
- Lifecycle status
- Timestamps
- Duration and charged minutes
- Recording and summary status

#### `call_messages`

- Persisted transcript lines after call completion
- Speaker, text, timestamp, sequence number

#### `notifications`

- Call start/end notification records
- Delivery attempts and status

#### `webhook_events`

- Idempotency and audit tracking for Clerk, Stripe, LiveKit, and Telnyx events

### Redis Responsibilities

- Active WebSocket session mapping by `user_id`
- Live transcript and call event fanout
- Short-lived transcript buffering during active calls
- Lightweight async job coordination state where needed

## Core Services

### API Services

#### `AuthService`

- Verify Clerk-backed application identity through `core/auth.py`
- Sync Clerk users into Postgres via signed webhook
- Reject valid tokens with missing local user records to avoid ghost access

#### `BillingService`

- Handle Stripe customer and subscription state
- Reset minutes on `invoice.paid`
- Enforce exhaustion logic and related number disablement

#### `TelephonyService`

- Provision Telnyx phone numbers
- Map DID to user
- Enable or disable numbers by switching between `app-active` and `app-disabled`

#### `CallLifecycleService`

- Create and update `calls`
- Finalize duration and charged minutes
- Persist call outcomes
- Trigger downstream summary, recording, and notification work

#### `LiveKitDispatchService`

- Handle LiveKit webhook context
- Resolve the user and agent configuration for an inbound call
- Dispatch the LiveKit worker with the job payload

#### `RecordingService`

- Store recording metadata
- Generate signed playback URLs
- Surface expiry state

#### `SummaryService`

- Queue post-call summary generation
- Persist generated summaries and failure status

#### `NotificationService`

- Create and send call start/end notifications

#### `RealtimeService`

- Authenticate WebSocket connections
- Fan out call lifecycle and transcript events to all active user sessions

### Agent Runtime Responsibilities

#### `pipeline_factory.py`

- Build the configured voice pipeline from `AgentConfig`
- Support `stt_llm_tts` immediately and keep `sts` as an explicit future-ready mode

#### `prompt_builder.py`

- Construct the final prompt from agent configuration
- Inject the user knowledge base as raw text wrapped in `<knowledge_base>` tags
- Hardcode the required AI identification and recording disclosure in the greeting

#### Live Session Runtime

- Join the LiveKit room after explicit dispatch
- Execute the voice pipeline
- Emit transcript events in normalized form
- Capture failures and produce a normalized call result payload for persistence

## Runtime Flows

### 1. Signup To Activation

1. Clerk authenticates the user.
2. Clerk emits `user.created` to `POST /auth/sync`.
3. The API validates the webhook signature and idempotently upserts the local `users` record.
4. Stripe subscription activation sets the active plan state.
5. The API provisions a Telnyx number, persists it in `phone_numbers`, creates a default `agent_configs` row, and keeps activation governed by business rules.

### 2. Incoming Call Handling

1. Caller reaches the user's forwarded Telnyx number.
2. Telnyx routes directly to LiveKit through the active application.
3. LiveKit creates a room and emits a webhook when the SIP participant joins.
4. The API resolves the DID to the owning user.
5. The API loads `agent_configs`, creates or updates the `calls` record, and dispatches the agent worker with the resolved configuration.
6. The agent joins the room, plays the mandatory AI/recording disclosure greeting, handles the conversation, and emits transcript events for real-time fanout.

### 3. Call Completion

1. Room end or agent completion triggers finalization.
2. The API computes duration and charged minutes.
3. The API writes usage events to `usage_ledgers`.
4. If the balance is exhausted, the API disables the number.
5. Transcript lines are persisted to `call_messages`.
6. Recording metadata is stored.
7. Summary generation and notifications are queued or executed through the async job path.

### 4. Live Monitoring

1. Web clients open an authenticated WebSocket.
2. The API validates the first auth message before accepting other messages.
3. Active sessions receive `call_started`, transcript events, and `call_ended`.
4. Redis backs the transient fanout path; Postgres remains the durable store.

## Failure Handling

### Idempotency

- All external webhook processing records event ids in `webhook_events`.
- Duplicate deliveries must be safe and should not double-provision or double-charge.

### Dispatch Failure

- If the API cannot dispatch the agent worker, the call is marked failed and the failure is preserved for investigation.

### Mid-Call Agent Failure

- Partial transcript and call state must be preserved.
- The user must be notified that the call ended abnormally.

### Async Post-Call Failures

- Summary generation, recording post-processing, and notification sending must fail independently from core call completion.
- Call completion must still persist even when downstream jobs need retries.

### Minute Exhaustion

- If minutes reach zero mid-call, the call is terminated according to the business rules.
- The exhaustion event is persisted and the Telnyx number is switched to the disabled application immediately afterward.

### Ghost User Protection

- A valid Clerk JWT with no corresponding local user row returns `401`.
- The client must re-trigger sync rather than being allowed partial access.

## Security And Privacy Constraints

- Clerk integration is isolated to `apps/api/app/core/auth.py`.
- WebSocket auth uses the first message pattern, not query params.
- JWT validation must include signature, expiry, issuer, and audience if configured.
- Recording disclosure and AI identification remain non-removable.
- Recordings are stored privately with signed playback URLs.
- Data is scoped by user ownership across all reads and writes.

## Testing Strategy

### API Tests

- Unit tests for service-layer business rules
- Repository tests for critical query paths
- Integration tests for webhook handlers, auth middleware, minute accounting, and DID-to-user resolution

### Agent Tests

- Prompt builder tests
- Provider selection tests
- Runtime tests for normalized transcript and call result emission

### Provider Contract Tests

- Clerk auth validation
- Stripe webhook handling
- Telnyx provisioning and number state switching
- Storage adapter behavior
- Notification adapter behavior

### Staging Smoke Path

- Create a real user
- Activate a subscription
- Provision a number
- Receive a real test call
- Dispatch the agent
- Persist transcript and call record
- Generate a summary

## Open Decisions Captured For Implementation

- Use a modular monorepo with separate API and agent applications.
- Include the production LiveKit worker in the first backend MVP.
- Build deployment-ready infrastructure and provider integrations from day one.
- Keep FastAPI out of the inbound SIP call setup path.
- Use Postgres as the source of truth and Redis only for transient real-time state.

## Implementation Handoff

The next artifact should be an implementation plan for this backend foundation. The plan should decompose the work into small, testable, TDD-oriented tasks covering repository bootstrap, API foundation, auth, billing, telephony, LiveKit dispatch, agent runtime, persistence, post-call workflows, and staging verification.
