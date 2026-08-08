# Opevo production-readiness handoff

## Purpose

This document is the durable continuation point for Opevo's local-first
production-readiness work through the account-deactivation lifecycle completed
in July 2026. It complements the canonical capability matrix in
`docs/PROJECT_STATUS.md`.

Opevo is production-oriented and locally verified, but it is not
production-certified. This implementation did not deploy anything, contact or
mutate live providers, use real credentials, change provider accounts, push, or
publish externally.

## Current lifecycle contract

The customer account state machine is:

```text
active -> deactivating -> inactive -> active
```

- `active -> deactivating` begins through an authenticated owner request or a
  final Stripe subscription cancellation. The committed state immediately
  blocks new calls and all profile, receptionist, provisioning, verification,
  routing, and go-live mutations.
- `deactivating -> inactive` occurs only after durable reconciliation proves
  routing disabled, subscription cancellation authoritative, all admitted calls
  terminal, the old number released, and current number-cycle state reset.
- `inactive -> active` requires a new subscription for the current lifecycle
  generation. It does not itself make the account ready to serve calls.

Owner deactivation requires exact `DEACTIVATE` confirmation. It requests
immediate Stripe cancellation with `invoice_now=false` and `prorate=false`;
there is no automatic prorated refund. Subscription-only cancellation remains a
separate Stripe Billing Portal action that is scheduled for the paid-period end.
The account stays active until Stripe reports final cancellation, at which point
the same deactivation workflow starts. An unexpected immediate final
cancellation also fails closed into deactivation.

An already-admitted call may finish. Telnyx routing disablement and Stripe
cancellation can finish while that call is active, but number release cannot.
The deactivation worker makes no provider request while a business database
transaction is open.

## Durable coordination and truthful progress

One private deactivation operation belongs to one account lifecycle generation.
The entry transaction increments that generation, makes the account and local
phone non-serving, disables the receptionist, and records one
`account.deactivate` outbox event whose payload is exactly `operation_id`.
Repeated owner requests and final Stripe events converge on the same incomplete
operation.

The request or Stripe-webhook transaction does not claim provider work that has
not happened. In particular, it leaves `routing_disabled_at` and
`subscription_canceled_at` unset. Only the reconciler writes each phase
timestamp after provider success or authoritative terminal-state verification.
Process restart and redelivery resume from those committed timestamps.

Retryable provider unavailability and active-call drainage are non-exhausting.
Authentication, provider-contract, and identity-conflict faults commit an
`attention_required` state and a bounded safe code while the account remains
non-serving. Provider identifiers, credentials, raw errors, provider bodies,
customer content, and typed confirmation are absent from customer responses,
outbox payloads, logs, and metric attributes.

## Retention and reactivation

Deactivation retains identity, the confirmed business profile and carrier,
receptionist prompt/context/knowledge, calls, transcripts, summaries,
recordings, notifications, usage, and billing history. Inactive owners retain
authenticated read-only access to historical Calls and Billing surfaces.

After release, the old active phone assignment and obsolete provisioning row
are removed. Provisioning consent, verification window/session/result,
forwarding verification, go-live approval, and activation time are reset. The
agent remains disabled. The old number is not recoverable through Opevo.

A generation-matched replacement subscription reactivates the account with the
confirmed profile/carrier and receptionist configuration intact. The activation
journey resumes directly at fresh number-provisioning consent, then assigns a
new number and requires forwarding verification and explicit go-live approval.

## Product and integration surfaces

- `GET /api/account` returns the safe account status, serving flag,
  deactivation progress, reactivation eligibility, and bounded blocker.
- `POST /api/account/deactivate` is owner-authenticated, rate-limited to five
  requests per minute, requires `{"confirmation":"DEACTIVATE"}`, and returns
  `202 Accepted` with safe progress for the first or repeated valid request.
- `POST /api/development/call-drain-fixture/start` and
  `POST /api/development/call-drain-fixture/finish` exist only when
  `APP_ENV=development`. Both additionally require `AUTH_MODE=local`,
  `TELEPHONY_MODE=fake`, and the authenticated local owner. They exercise the
  real call lifecycle without LiveKit or Telnyx work and are not staging or
  customer tools.

Production uses `BILLING_MODE=stripe`. The API and the Stripe-mode worker need
`STRIPE_SECRET_KEY`; production API startup also requires
`STRIPE_BILLING_PORTAL_CONFIGURATION_ID`. That ID must select an externally
reviewed Stripe Portal configuration that offers period-end subscription
cancellation and disables proration. Repository tests verify the request
contract, not the real Stripe configuration.

## Operations contract

The implemented low-cardinality metrics are:

- `opevo.account_deactivation.operations`
- `opevo.account_deactivation.oldest_incomplete_age`
- `opevo.account_deactivation.reconciliation_results`
- `opevo.account_deactivation.attention`
- `opevo.account_deactivation.completion_duration`

Before release, configure paging for every increment of
`opevo.account_deactivation.attention`, and alert when
`opevo.account_deactivation.oldest_incomplete_age` exceeds
`MAX_CALL_DURATION_SECONDS + 900` seconds.

For an attention incident, use the operation ID and bounded trigger/phase/code
only. Remediate the credential, provider-contract, or identity fault; then use
the approved datastore/queue administration path to requeue only the failed
reference-only `account.deactivate` outbox event for that recorded operation ID.
Do not synthesize a second operation or payload. Verify that the durable
operation reaches `completed` and the account reaches `inactive` before closing
the incident.

## Local verification evidence

Fresh Task 11 release-gate evidence:

- API Ruff and mypy passed across the complete source tree.
- The complete SQLite suite passed 1,901 tests and skipped 111
  PostgreSQL-specific tests.
- The complete isolated PostgreSQL 17/Redis 7 suite passed 2,012 tests with zero
  skips. Its disposable containers, volumes, and network were removed.
- Node 22.23.1 web verification passed Biome across 156 files, TypeScript,
  all 257 Vitest tests, and the production build; the build generated 10 static
  pages and completed the dynamic route manifest.
- The provider-free browser journey passed activation, deactivation with an
  active call, API/worker restart, durable resume, historical-data preservation,
  reactivation, and fresh-number provisioning: three Playwright tests passed.
  Its disposable containers, volumes, and network were removed.
- Customer-response privacy and reference-only `account.deactivate` payload
  audits passed. `git diff --check` was clean.

Exact commands, durations, warnings, cleanup checks, and the initial corrected
gate failures are recorded in
`.superpowers/sdd/2026-07-24-account-deactivation-lifecycle/task-11-report.md`.

These local gates do not constitute cloud, legal, behavioral-model, carrier,
Stripe, Telnyx, LiveKit, or other real-provider certification.

## Remaining production blockers

The following are explicitly open or unevidenced:

1. Account-wide export and permanent account/identity deletion.
2. Automatic retention, recording-access audit, backup/historical-copy erasure,
   and a demonstrated backup restore.
3. Qualified French/EU legal, privacy, recording-disclosure, retention,
   subprocessor, and support approval and surfaces.
4. Fresh multi-customer Clerk/Stripe/Telnyx/LiveKit staging certification,
   including real Portal cancellation, non-proration, active-call drainage,
   number release, reactivation, and provider-fault recovery.
5. Cloud deployment, DNS/TLS, secret management, alert routing, rollback,
   restore, and controlled-beta operating evidence.
6. Accessibility end-to-end tests, frontend performance budgets, load tests,
   outage drills, recovery drills, and credentialed behavioral evaluation
   evidence.
7. The optional realtime observer identity-key mismatch and private push
   delivery.

Deactivation is reversible service shutdown, not deletion. Do not describe it
as export, permanent deletion, retention enforcement, or backup erasure.

## Recommended next implementation unit

Design account-wide export and permanent deletion separately from deactivation.
Define export scope, legal and retention authority, active-call behavior,
recording-cleanup drainage, audit evidence, and the boundary for backups and
historical copies before product code changes. Do not infer erasure policy from
the implemented inactive state.

## Resume procedure

1. Verify a clean checkout and read this handoff, `docs/PROJECT_STATUS.md`,
   `docs/architecture/local-self-service-activation.md`,
   `docs/architecture/integration-endpoints.md`, and `docs/runbooks/deploy.md`.
2. Read
   `docs/superpowers/specs/2026-07-24-account-deactivation-lifecycle-design.md`
   and the Task 11 report. Do not reuse old verification counts.
3. Confirm the Stripe Portal configuration and both deactivation alerts are
   reviewed deployment artifacts; repository configuration alone is not
   evidence.
4. Preserve the boundary: local test infrastructure is authorized; provider
   accounts, cloud resources, deployments, real credentials, external
   publishing, and pushes require separate approval.
5. If a defect is found, reproduce it and use the repository's test-first and
   systematic-debugging workflows.

The disposable local acceptance journey uses only local identity and fake
product providers, but requires Docker, Chromium, and Node.js 22:

```bash
bash scripts/run-local-e2e.sh
```
