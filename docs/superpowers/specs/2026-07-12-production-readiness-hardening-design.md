# Production Readiness Hardening Design

## Status

Approved design baseline derived from the 2026-07-12 production-readiness audit and the decision to continue building the current system rather than rewrite it.

## Goal

Turn the existing France-first inbound voice-assistant MVP into a controlled, production-capable SaaS that can safely onboard 5–10 design partners before a broader public launch.

## Product Boundary

The hardening program preserves these launch constraints:

- France-only phone-number provisioning.
- One paid `starter` plan.
- One agent configuration per customer.
- Inbound calls only.
- `stt_llm_tts` as the only customer-facing pipeline.
- Stripe-hosted checkout and billing portal.
- Telnyx telephony and LiveKit voice runtime.
- PostgreSQL as the source of truth.
- Redis and ARQ as transient infrastructure, never as the sole durable record of money or customer calls.

The program does not add team accounts, outbound campaigns, additional countries, additional plans, advanced analytics, or a general contact-center feature set.

## Decision

Retain the current FastAPI, LiveKit agent, ARQ worker, PostgreSQL, Redis, and Next.js decomposition. Replace fragile workflow internals with transactionally safe services, durable state machines, an outbox, reconciliation jobs, strict production configuration, and release gates.

This is preferred over a rewrite because the repository already has useful provider boundaries, product scope, dashboard components, documentation, migrations, and 179 passing tests. The production risks are concentrated in authoritative state transitions and operations rather than the overall application shape.

## Considered Approaches

### A. Full rewrite

Rebuild the API, agent, and web application around a new architecture.

Rejected because it discards verified behavior and does not inherently solve billing concurrency, distributed transactions, compliance, or operations. It also delays customer learning.

### B. Staged hardening of the current architecture

Keep stable interfaces, replace unsafe state transitions, add database guarantees, and release through progressively stronger gates.

Selected because it minimizes product disruption while directly addressing the risks identified in the audit.

### C. Launch now and repair incidents reactively

Release the current application to paying customers and fix failures as they occur.

Rejected because current defects can mischarge usage, preserve access after failed payment, lose transcripts, expose secrets, and cross customer boundaries.

## Architecture

### Authoritative State

PostgreSQL owns:

- Customer identity mapping.
- Subscription state.
- Available minute balance.
- Usage ledger entries.
- Call state and transcript segments.
- Phone-number state.
- Provisioning state.
- Webhook idempotency records.
- Outbox events and delivery attempts.
- Data-retention and deletion state.

The voice agent may receive a balance snapshot for user messaging, but it cannot decide the final charge. Redis locks may reduce duplicate work, but database constraints and transactions provide correctness.

### Transaction Boundary

Every business operation follows this sequence:

1. Lock and validate authoritative rows.
2. Apply the local state transition.
3. Add an outbox event in the same database transaction.
4. Commit.
5. Let a worker perform the external side effect.
6. Record provider success or failure.
7. Reconcile operations that remain incomplete.

No Stripe, Telnyx, LiveKit, Firebase, or storage side effect is treated as atomically committed with PostgreSQL.

### Billing and Usage

Subscription access uses an explicit policy:

- `active` and `trialing` may route when all other readiness conditions pass.
- `past_due`, `unpaid`, `canceled`, `incomplete`, `incomplete_expired`, and `paused` cannot route.
- A fresh paid invoice grants or resets the configured `starter` allowance once per invoice.
- A call debit derives the owner from the locked call row and derives the current balance from locked database state.
- One call may create at most one `call_completed` usage entry.
- Balance never becomes negative.

### Call Lifecycle

Calls move through explicit states:

- `pending`
- `connected`
- `ending`
- `finalizing`
- `completed`
- `failed`

Transcript segments are appended durably during the call using `(call_id, sequence_number)` idempotency. Finalization is retryable and does not depend on the voice process remaining alive. A reconciliation worker handles calls that exceed state-specific deadlines.

### Internal Agent Authentication

Every dispatch receives a short-lived JWT containing `call_id`, `user_id`, `agent_config_id`, `iat`, and `exp`. The completion and transcript endpoints validate all expected claims against database state. The static shared-token fallback is disabled in staging and production.

### Realtime Scope

Realtime WebSocket delivery is not a launch dependency. The MVP will disable the unused WebSocket route in production until canonical user identity mapping, reconnect behavior, and state resynchronization are implemented. Dashboard pages use server reads and explicit refresh/revalidation.

### Privacy and Recording

Recordings are private objects with encryption, lifecycle deletion, and short-lived signed access. A read performs an existence check before signing. Customer-facing policy pages and the caller disclosure explain the AI system, recording status, controller, purpose, retention, rights, and contact route. Account deletion and call deletion create auditable purge work rather than merely hiding rows.

### Production Platform

Local Compose remains a development tool. Production deployment uses managed PostgreSQL, managed Redis with TLS/authentication, private object storage, a secret manager, separate API/worker/agent processes, a one-shot migration release job, readiness probes, centralized logs, error reporting, metrics, traces, backups, and alerts.

## Error Handling

- Duplicate provider events return success after the unique idempotency record is observed.
- Invalid webhook signatures return `400` without stack traces.
- External provider timeouts create retryable outbox failures and never roll back already committed business intent.
- Non-retryable provider failures move the operation to an operator-visible terminal state.
- Finalization retries are idempotent at both service and database levels.
- Readiness fails closed when database or Redis dependencies are unavailable.
- Production startup fails closed when required credentials or URLs are missing.

## Verification Strategy

The program uses test-driven changes and five layers of verification:

1. Unit tests for policies and state transitions.
2. PostgreSQL and Redis integration tests for locks, uniqueness, queues, and retries.
3. Provider contract tests using Stripe test mode, Telnyx staging, LiveKit Cloud, and an S3-compatible staging bucket.
4. Browser and voice-behavior tests for onboarding, billing, call handling, disclosure, grounding, and failure behavior.
5. Operational drills for backup restoration, agent failure, provider outage, webhook replay, and rollback.

## Release Gates

### Gate 0: Containment

- All exposed credentials rotated.
- Local secret permissions restricted.
- Prompt/transcript/PII logs removed or redacted.
- Production configuration fails closed.

### Gate 1: Money and tenancy correctness

- Subscription lifecycle disables access correctly.
- Webhook replay cannot duplicate grants or provisioning.
- Concurrent calls cannot overspend.
- Agent payloads cannot charge another customer.

### Gate 2: Durable calls

- Partial transcript survives agent termination.
- Stuck calls reconcile automatically.
- Finalization is safe to retry.
- Maximum duration and balance exhaustion are enforced.

### Gate 3: Operable production platform

- CI, readiness, metrics, tracing, alerts, backups, restore, release migrations, and security scanning are operational.

### Gate 4: Product and compliance

- French customer experience and legal surfaces are complete.
- Data export/deletion and recording retention are verified.
- Accessibility and performance targets pass.
- Qualified legal review accepts the caller disclosure and privacy flow.

### Gate 5: Controlled beta

- Three clean staging journeys complete without manual database changes.
- Failure drills pass.
- Five to ten design partners can be onboarded with documented support and rollback procedures.

## Success Criteria

The product is ready for controlled production when every release gate is satisfied, no high or critical security findings remain, and an operator can detect, diagnose, recover, and explain failures without directly editing production data.

It is ready for broader public launch only after the controlled beta meets its reliability targets for a defined observation period and all legal/compliance commitments are implemented rather than documented as future work.
