# Local self-service activation

This document is the operator contract for Presvo's provider-free activation
journey. It describes what is implemented locally, what the disposable browser
test proves, and which production claims remain gated.

## Supported launch boundary

The implemented product path serves English-speaking professionals and small
businesses in France. An owner keeps an existing French business number and
receives one Presvo-provided French number. Only unanswered, busy, and
unreachable calls are conditionally forwarded to Presvo; unconditional
forwarding is not part of the guided path.

The local path is a deterministic product proof. It is not proof of cloud
deployment or certification against Clerk, Stripe, Telnyx, LiveKit, or speech
and model providers.

## Five durable milestones

1. **Business** — save owner/business details, Europe/Paris hours, the existing
   French number, and a confirmed carrier.
2. **Receptionist** — save the receptionist name, public business description,
   FAQs and operating boundaries, then confirm that exact persisted revision.
3. **Number** — establish starter-plan eligibility, separately approve number
   provisioning, and wait for durable assignment of one fake French number.
4. **Forwarding** — review the versioned carrier guide for unanswered, busy,
   and unreachable calls, then open a ten-minute verification window.
5. **Launch** — resume the server-timed window, simulate the forwarded call,
   review readiness, explicitly approve go-live, and land on the active
   dashboard.

Reloads between milestones read the canonical workflow snapshot from
PostgreSQL. The proof intentionally has no database-reset endpoint. The local
identity represents one fixed account, so the browser suite is one serial test
with one worker.

## Why payment and provisioning consent are separate

Billing eligibility proves that the account may use the selected plan. It does
not authorize Presvo to order a phone number. Number provisioning can incur a
provider-side effect, so the owner must review the exact consequences and give
separate, explicit consent. The local fake preserves that boundary even though
it performs no external purchase.

## Local runtime modes and credential scope

`compose.dev.yaml` explicitly selects these modes:

| Service | Explicit local values | Deliberately absent |
|---|---|---|
| API | `AUTH_MODE=local`, server-only `LOCAL_AUTH_TOKEN`, `BILLING_MODE=fake`, `CARRIER_LOOKUP_MODE=fake`, `TELEPHONY_MODE=fake`, activation enabled | Cloud credentials are unnecessary |
| Worker | `TELEPHONY_MODE=fake`, activation enabled | Auth mode, local token, billing mode, and carrier lookup |
| Web server | `AUTH_MODE=local`, server-only `LOCAL_AUTH_TOKEN`, `BILLING_MODE=fake`, `TELEPHONY_MODE=fake` | Carrier credentials and public local token |

The local token is never a `NEXT_PUBLIC_*` value or a Docker build argument.
The web server uses it only for server-to-server API requests. Fake provider
modes are explicit opt-ins; production settings reject them.

The LiveKit agent is excluded from this journey. The local simulator calls the
same forwarding-verification application service that a verified system call
would reach, without opening a room or contacting a speech/model provider.

## Start and exercise the local journey

Prerequisites are Docker with Compose and Node.js 22. No hosted-provider
credentials are needed.

Start the application services:

```bash
docker compose -f compose.dev.yaml up --build postgres redis minio minio-init migrate api worker web
```

Open `http://127.0.0.1:3000/activate` and complete the five milestones above.
Compose defaults remain available for normal development, while every exposed
host port is parameterized for isolated runs.

To run the same disposable proof as CI:

```bash
npm exec --prefix apps/web -- playwright install chromium
bash scripts/run-local-e2e.sh
```

The runner:

- uses Compose project `presvo-e2e`;
- binds web/API/PostgreSQL/Redis/MinIO to alternate loopback ports;
- builds migrate, API, worker, and web images;
- waits for datastore and application health plus both one-shot exits;
- starts no LiveKit agent;
- runs `npm --prefix apps/web run test:e2e`; and
- always executes `docker compose down --volumes --remove-orphans` through its
  exit trap.

The browser assertion covers profile persistence across reloads, the separate
plan and provisioning approvals, asynchronous worker provisioning, all three
conditional-forwarding conditions, verification-window persistence, local
simulation, explicit go-live, and the active dashboard handoff.

## Real-provider and production boundary

The production Compose file explicitly selects Clerk authentication, Stripe
billing, Telnyx carrier lookup/telephony, and requires
`ACTIVATION_FLOW_ENABLED`. It contains no local token. Required credentials use
fail-fast Compose interpolation, and application configuration rejects local or
fake modes in production.

A real call additionally requires a deployed API/web/worker/agent topology,
LiveKit SIP and agent dispatch, private object storage, model and speech
providers, webhook delivery, DNS/TLS, and operational monitoring. Use the
[staging smoke runbook](staging-smoke-runbook.md); do not reinterpret the local
browser pass as provider certification.

## Data lifecycle and deferred gates

Per-terminal-call removal is implemented. An authenticated owner can use
**Remove call** on a terminal call; active calls reject removal. One local
transaction purges customer call content, hides the call, and returns `204`
without LiveKit or storage I/O. When a private recording operation or legacy
recording metadata exists, that transaction also records stop/delete intent
plus a reference-only `recording.reconcile` event. Repeated owner removal is
idempotent.

Non-exhausting asynchronous cleanup then makes any provider recording
non-running before removing the original-audio object from active storage.
Provider or storage outages do not keep the call visible or require another
customer removal. No claim is made that provider cleanup, backup erasure, or
historical-copy erasure completes synchronously.

Account-wide export and deletion orchestration remain planned. The intended
account-deletion contract removes active call records, transcripts, summaries,
recording references, and active objects owned by the account, without claiming
that historical backup copies are synchronously erased.

The following remain planned or require approval/evidence:

- cloud deployment and rollback/restore proof;
- three clean real-provider certification journeys;
- French localization plus approved legal, privacy, recording, retention,
  subprocessor, and support surfaces;
- user-facing export and deletion orchestration;
- appointment booking and calendar integration;
- typed conversation flows, transitions, simulation, and authoring;
- an approved automatic 30-day retention policy and operating proof;
- accessibility, load, provider-outage, recovery, and behavioral voice-agent
  evaluations; and
- monitored controlled-beta evidence.

The earlier recording start/delete race is resolved for normal customer calls.
Presvo commits a private recording operation and reference-only reconciliation
intent before recording-start provider I/O. Completion and owner removal record
stop intent even without a provider egress ID, so a late or ambiguous start
remains durably reconcilable after the call is hidden. This behavior is locally
verified; it is not real-provider or production certification.

## CI boundary

The `e2e` CI job runs only after API, agent, and web verification. It installs
the locked web dependencies and Chromium, then calls the same disposable
runner. The runner completes activation, restarts PostgreSQL, Redis, MinIO, API,
worker, and web without removing their volumes, and proves the persisted active
dashboard resumes before cleanup. It publishes no image, deploys no service,
contacts no product provider, and is included in the aggregate required-status
job.
