# Local self-service activation

This document is the operator contract for Opevo's provider-free activation
journey. It describes what is implemented locally, what the disposable browser
test proves, and which production claims remain gated.

## Supported launch boundary

The implemented product path serves English-speaking professionals and small
businesses in France. An owner keeps an existing French business number and
receives one Opevo-provided French number. Only unanswered, busy, and
unreachable calls are conditionally forwarded to Opevo; unconditional
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
PostgreSQL. The proof intentionally has no database-reset endpoint. The
provider-free browser suite explicitly selects a fixed local identity, so it
is one serial test with both explicit workers.

## Account lifecycle and reactivation

The same provider-free journey proves the three account states:

```text
active -> deactivating -> inactive -> active
```

Exact `DEACTIVATE` confirmation commits `deactivating` before provider work.
New calls and configuration, provisioning, verification, routing, and go-live
mutations are blocked immediately. The local `worker-background` service
disables the fake number, cancels fake billing without automatic proration or refund, waits for an
already-connected call to finish, releases the fake number, resets
number-specific activation state, and only then commits `inactive`.

The inactive owner keeps authenticated read-only access to historical calls,
recordings, usage, notifications, and billing. Confirmed business,
receptionist, and existing-line carrier data are retained. A new
generation-matched local subscription changes the account back to `active` and
resumes directly at fresh number-provisioning consent. It assigns a different
fake number and still requires forwarding verification and explicit go-live.

Production subscription-only cancellation differs: it is a Stripe Billing
Portal action scheduled for the paid-period end, so the account remains active
until a final Stripe cancellation event starts deactivation. Owner-requested
account deactivation is immediate and has no automatic prorated refund.

## Why payment and provisioning consent are separate

Billing eligibility proves that the account may use the selected plan. It does
not authorize Opevo to order a phone number. Number provisioning can incur a
provider-side effect, so the owner must review the exact consequences and give
separate, explicit consent. The local fake preserves that boundary even though
it performs no external purchase.

## Local runtime modes and credential scope

Normal `compose.dev.yaml` development defaults to Supabase authentication. It
can instead select Clerk with the deployment-level
`AUTH_PROVIDER=clerk` Compose variable, which the file applies to both the API
and web services. Configure only the selected provider's values from the
checked-in examples; each application validates its own required credentials.

Provider fakes are independent of identity: fake billing or telephony does not
provide a synthetic authenticated user. Normal development uses Telnyx Number
Lookup for the owner's existing number and loads `TELNYX_API_KEY` only into the
API from `apps/api/.env`; the web and worker services do not receive that
credential. `scripts/run-local-e2e.sh` is the disposable CI-equivalent path; it
explicitly opts into local auth and fake carrier lookup, and owns its isolation,
credentials, ports, and cleanup.

Manual provider-free testing requires explicit local identity values and fake
carrier lookup on the same command, and the token is development-only:

```bash
AUTH_PROVIDER=local \
LOCAL_AUTH_TOKEN=replace-with-a-development-only-token \
CARRIER_LOOKUP_MODE=fake \
docker compose -f compose.dev.yaml up --build postgres redis minio minio-init migrate api worker-lifecycle worker-background web
```

For that explicit local-auth command, the services use these development-only
values:

| Service | Explicit local values | Deliberately absent |
|---|---|---|
| API | `AUTH_PROVIDER=local`, server-only `LOCAL_AUTH_TOKEN`, `BILLING_MODE=fake`, `CARRIER_LOOKUP_MODE=fake`, `TELEPHONY_MODE=fake`, activation enabled | Clerk credentials are not used for this explicit test mode |
| `worker-lifecycle` and `worker-background` | `BILLING_MODE=fake`, `TELEPHONY_MODE=fake`, activation enabled | Local token and carrier lookup |
| Web server | `AUTH_PROVIDER=local`, server-only `LOCAL_AUTH_TOKEN`, `BILLING_MODE=fake`, `TELEPHONY_MODE=fake` | Carrier credentials and public local token |

The local token is never a `NEXT_PUBLIC_*` value or a Docker build argument.
The web server uses it only for server-to-server API requests. Fake provider
modes are explicit opt-ins; production settings reject them.

The LiveKit agent is excluded from this journey. The local simulator calls the
same forwarding-verification application service that a verified system call
would reach, without opening a room or contacting a speech/model provider.

The call-drain acceptance uses two API fixtures:

- `POST /api/development/call-drain-fixture/start` creates and connects one
  owner-scoped call through the real repository without LiveKit or Telnyx work
  and returns only `call_id`.
- `POST /api/development/call-drain-fixture/finish` accepts that `call_id`,
  exercises the real end/finalization path, and returns only `call_id`.

The router exists only for `APP_ENV=development`. Both routes also require
`AUTH_PROVIDER=local`, `TELEPHONY_MODE=fake`, and the authenticated local identity.
Hosted-provider development deployments and non-fake telephony receive the
same bounded conflict. These fixtures are not available in staging or
production.

## Start and exercise the local journey

Prerequisites are Docker with Compose, Node.js 22, and credentials for the
selected hosted authentication provider. Fake providers remain separate from
identity.

Start the default Supabase-authenticated application services:

Normal development requires a real server-only `TELNYX_API_KEY` in
`apps/api/.env` and uses it only to look up the carrier of the owner's existing
French number. Billing and telephony remain fake unless a separate deployment
selects their real provider modes.

```bash
docker compose -f compose.dev.yaml up --build postgres redis minio minio-init migrate api worker-lifecycle worker-background web
```

`WEB_PORT` changes the published web port, application URL, and CORS origins.
When Clerk is selected explicitly, it also changes the two default local Clerk
authorized parties. Set `CLERK_AUTHORIZED_PARTIES` explicitly only when the
token's exact authorized party is intentionally different from those standard
loopback origins.

Open `http://127.0.0.1:3000/activate`, sign in through the selected provider,
and complete the five milestones above. Compose defaults remain available for
normal development, while every exposed host port is parameterized for
isolated runs.

To run the same disposable provider-free proof as CI:

```bash
npm exec --prefix apps/web -- playwright install chromium
bash scripts/run-local-e2e.sh
```

The runner:

- uses Compose project `opevo-e2e`;
- binds web/API/PostgreSQL/Redis/MinIO to alternate loopback ports;
- builds migrate, API, `worker-lifecycle`, `worker-background`, and web images;
- waits for datastore and application health plus both one-shot exits;
- starts no LiveKit agent;
- activates and assigns a first fake number;
- starts an owner-scoped connected call, requests deactivation, and proves the
  account is immediately non-serving while progress remains `draining_call`;
- restarts only API, `worker-lifecycle`, and `worker-background` while retaining
  PostgreSQL, Redis, MinIO, web,
  named volumes, and a private `0600` state file;
- finishes the same call, proves cleanup reaches `inactive`, and proves the
  historical call remains;
- reactivates directly at fresh consent with the retained confirmed
  profile/carrier and assigns a different fake number; and
- always executes `docker compose down --volumes --remove-orphans` through its
  exit trap and removes the private temporary state directory.

The browser assertions also cover profile persistence across reloads, separate
plan and provisioning approvals, asynchronous background-worker provisioning, all three
conditional-forwarding conditions, verification-window persistence, local
simulation, explicit go-live, and the active dashboard handoff.

## Real-provider and production boundary

The production Compose file selects one hosted authentication provider through
`AUTH_PROVIDER`, Stripe billing, Telnyx carrier lookup/telephony, and requires
`ACTIVATION_FLOW_ENABLED`. It contains no local token. Application startup
validates the selected identity credentials, and rejects local or fake modes in
production.

Production requires `BILLING_MODE=stripe`. `STRIPE_SECRET_KEY` is required by
the API and by both Stripe-mode worker services.
`STRIPE_BILLING_PORTAL_CONFIGURATION_ID` is a required production API setting
and is sent when creating Portal sessions. Operators must verify that the
referenced Stripe configuration allows cancellation only at period end and has
proration disabled. Local fake behavior and repository tests do not certify
that external Stripe artifact.

A real call additionally requires a deployed
API/web/`worker-lifecycle`/`worker-background`/agent topology, LiveKit SIP and
agent dispatch, private object storage, model and speech
providers, webhook delivery, DNS/TLS, and operational monitoring. Use the
[staging smoke runbook](../runbooks/staging-smoke.md); do not reinterpret the local
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

Reversible account deactivation is implemented, but account-wide export and
permanent deletion orchestration remain planned. No implemented path claims
automatic retention, account/identity deletion, or backup/historical-copy
erasure.

The following remain planned or require approval/evidence:

- cloud deployment and rollback/restore proof;
- three clean real-provider certification journeys, including Stripe
  immediate/period-end cancellation and Telnyx disable/release;
- French localization plus approved legal, privacy, recording, retention,
  subprocessor, and support surfaces;
- user-facing export and permanent deletion orchestration;
- appointment booking and calendar integration;
- typed conversation flows, transitions, simulation, and authoring;
- an approved automatic 30-day retention policy and operating proof;
- accessibility, load, provider-outage, recovery, and behavioral voice-agent
  evaluations; and
- monitored controlled-beta evidence.

The earlier recording start/delete race is resolved for normal customer calls.
Opevo commits a private recording operation and reference-only reconciliation
intent before recording-start provider I/O. Completion and owner removal record
stop intent even without a provider egress ID, so a late or ambiguous start
remains durably reconcilable after the call is hidden. This behavior is locally
verified; it is not real-provider or production certification.

## CI boundary

The `e2e` CI job runs only after API, agent, and web verification. It installs
the locked web dependencies and Chromium, then calls the same disposable
runner. The runner completes activation, deactivation with active-call drainage,
an API/`worker-lifecycle`/`worker-background` restart, historical-read proof,
and reactivation before cleanup.
It publishes no image, deploys no service, contacts no product provider, and is
included in the aggregate required-status job.
