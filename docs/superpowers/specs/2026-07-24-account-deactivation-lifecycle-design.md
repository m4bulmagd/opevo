# Opevo Account Deactivation Lifecycle Design

**Status:** Approved on 2026-07-24

## Purpose

Opevo needs a reversible account-deactivation workflow that stops service and
provider costs without deleting the customer or their historical data.
Deactivation is not account deletion. An inactive owner can still sign in,
review prior calls and billing, and later reactivate with a new subscription
and a newly provisioned number.

This design replaces the previously recommended account-wide export and
deletion implementation unit. Export, permanent account deletion, automatic
retention, and backup-erasure policy remain separate future work.

## Approved product decisions

- Account deactivation takes effect immediately for new-call admission.
- Owner-requested deactivation cancels the Stripe subscription immediately
  without an automatic prorated refund.
- Subscription cancellation without account deactivation remains a distinct
  action in the Stripe-hosted billing portal. It takes effect at the end of the
  paid period.
- A final period-end Stripe cancellation automatically starts account
  deactivation.
- Deactivation disables routing and releases the Opevo-provided phone number.
  An account without an active subscription does not retain a number.
- A call already in progress may finish normally. The number is released only
  after the active call reaches a terminal state.
- Deactivation preserves identity, business and receptionist configuration,
  call history, recordings, usage history, notifications, and billing history.
- Inactive owners retain authenticated read access to historical calls,
  recordings, and billing.
- Profile, receptionist, provisioning, verification, routing, and go-live
  mutations are blocked while the account is deactivating or inactive.
- Reactivation reuses the saved business and receptionist configuration. It
  requires a new subscription, fresh number-provisioning consent, a newly
  provisioned number, forwarding verification, and explicit go-live approval.
- The old released number is not recoverable through the Opevo workflow.
- The owner must type `DEACTIVATE` after reviewing the consequences.

## Scope

This slice includes:

- the account lifecycle states and authoritative access policy;
- immediate owner-requested deactivation;
- period-end subscription cancellation convergence;
- durable Stripe cancellation, Telnyx disablement, active-call drainage, and
  Telnyx number release;
- deactivation progress and audit evidence;
- read-only inactive-account access;
- reactivation through the existing Stripe Checkout and activation journey;
- customer-facing Account and billing-status surfaces;
- provider-free local and automated verification.

This slice does not include:

- customer-data export;
- permanent account or identity deletion;
- deletion or anonymization of calls, recordings, billing records, or profile
  data;
- automatic recording or account retention;
- backup or historical-copy erasure;
- refunds, credits, or prorated cancellation;
- reclaiming a released phone number;
- cloud deployment, provider certification, or legal approval;
- organization membership, multiple owners, multiple subscriptions, or
  multiple numbers.

## Domain language

**Account deactivation** is the reversible product action that ends current
service, cancels the current subscription, and releases the assigned number
while preserving the customer and historical data.

**Effective deactivation** begins when the local account becomes
`deactivating`. From that commit onward, Opevo admits no new customer calls
even if provider cleanup is incomplete.

**Inactive account** is an account whose deactivation cleanup is complete. It
has no active subscription and no assigned Opevo number, but its owner retains
read-only historical access and may reactivate.

**Subscription-only cancellation** is a Stripe Billing Portal action scheduled
for the paid-period end. It does not deactivate Opevo before Stripe reports
the final cancellation.

**Deactivation operation** is the private durable coordinator for one
deactivation cycle. It owns provider cleanup progress and can outlive the
customer-facing phone assignment.

**Reactivation** is a new service cycle. It uses a new subscription and number
while reusing retained business and receptionist content.

## Account state machine

The customer-facing lifecycle is:

```text
active -> deactivating -> inactive -> active
```

`deactivating` is already non-serving. It is distinct from `inactive` only
because Stripe cancellation, active-call drainage, or number release may still
be converging.

Only these transitions are valid:

- `active -> deactivating` through an authenticated owner request or a final
  Stripe subscription cancellation;
- `deactivating -> inactive` after the durable operation proves every required
  step complete;
- `inactive -> active` after an authorized new subscription is established for
  the current lifecycle generation.

Repeated commands do not skip steps or create another incomplete operation.
Provider events cannot transition `deactivating` back to `active`.

## Authoritative serving policy

Account status becomes an input to the central customer-readiness policy.
`deactivating` and `inactive` are authoritative blockers for:

- inbound-call admission and LiveKit dispatch;
- phone enablement;
- new number provisioning;
- forwarding verification;
- go-live approval;
- receptionist enablement;
- profile or receptionist mutations;
- stale outbox work that could restore service.

These blockers apply in the API, worker, webhook, and dispatch boundaries, not
only in the web interface.

An already-running call remains authorized to append transcripts, request
recording stop, and finalize through its call-scoped credentials. Deactivation
must not corrupt the call state machine, accounting, summary creation,
notification creation, or recording reconciliation. Finalization must not
re-enable routing for a deactivating account.

## Durable data model

### Account projection

The existing user account stores:

- `status`: `active`, `deactivating`, or `inactive`;
- a monotonically increasing lifecycle generation.

The lifecycle generation distinguishes a current reactivation checkout and
subscription from delayed provider events belonging to an earlier service
cycle.

### Deactivation operation

One private deactivation-operation row represents one service cycle's
deactivation. It contains:

- account ID and lifecycle generation;
- trigger: `owner_request` or `subscription_ended`;
- request and completion timestamps;
- the Stripe subscription and Telnyx phone identities required to finish the
  operation;
- routing-disabled, subscription-canceled, active-call-drained,
  number-released, activation-reset, and completed timestamps;
- safe retry scheduling and attempt data;
- a bounded safe error code and operator-attention signal.

A partial unique constraint permits at most one incomplete operation per
account. The row does not store the typed confirmation, provider credentials,
raw provider responses, signed URLs, call content, or recording content.

The exact provider identities remain private. Customer responses expose only
safe progress states.

### Subscription projection

The subscription projection additionally records whether Stripe scheduled
cancellation at period end and its effective timestamp. A scheduled
cancellation leaves the subscription and account active until final
cancellation.

Stripe Checkout for an inactive account includes the current lifecycle
generation in private metadata. Only a new subscription matching that
generation may reactivate the account. Events for the canceled subscription
belonging to a prior generation cannot restore service.

### Phone and activation projections

The phone assignment remains present but locally inactive until provider
release is confirmed. This preserves the exact provider identity required for
idempotent disablement and release.

After release succeeds:

- the old phone assignment is removed from the active customer projection;
- obsolete provisioning state is removed or reset;
- any later provisioning attempt uses a new idempotency identity derived from
  the current lifecycle generation and cannot reconcile to the released
  service cycle;
- number-specific activation state is cleared, including provisioning consent,
  verification window/session/result, forwarding verification, go-live
  request/approval, and activation time;
- profile confirmation and saved business/receptionist content remain;
- agent content remains, while `is_enabled` remains false.

The deactivation operation retains the minimum private completion evidence
needed to prove the release without presenting the old number as assigned.

## Entry points

### Immediate owner deactivation

`POST /api/account/deactivate` accepts an authenticated request containing the
exact confirmation value `DEACTIVATE`.

In one short database transaction, the service:

1. locks the owner account;
2. returns the existing operation for a repeated request;
3. creates the operation using the current subscription and phone identities;
4. increments the lifecycle generation;
5. marks the account `deactivating`;
6. disables the agent and local phone projection;
7. records one reference-only outbox intent containing only `operation_id`;
8. commits before provider I/O.

The endpoint returns `202 Accepted` with safe lifecycle progress. It does not
wait for Stripe, Telnyx, Redis, or an active call.

The owner confirmation explains that service stops immediately, no automatic
prorated refund is issued, an active call may finish, the current number will
be permanently released, existing data remains, and reactivation requires a
new subscription and number.

### Subscription-only cancellation

The existing Stripe-hosted Billing Portal remains the only customer surface
for subscription-only cancellation. Its approved configuration schedules
cancellation at the paid-period end.

A `customer.subscription.updated` event with period-end cancellation:

- records the scheduled flag and effective date;
- leaves account status and routing unchanged;
- allows reversal before the effective date.

When Stripe reports final cancellation for the current subscription,
the webhook transaction creates or converges on a deactivation operation with
trigger `subscription_ended`. Stripe cancellation is already complete for this
operation. The account becomes `deactivating` in the same transaction, so new
call admission stops before asynchronous Telnyx cleanup.

If Stripe reports an immediate final cancellation, Opevo must still fail
closed and start deactivation. Production readiness requires verifying that the
customer portal is configured for period-end cancellation.

## Deactivation orchestration

`account.deactivate` is a reference-only outbox topic whose payload contains
only `operation_id`. The aggregate is the deactivation operation. Delivery
always reloads authoritative account, subscription, phone, call, and operation
state.

The worker converges these steps:

1. **Disable routing.** Disable the exact Telnyx number when it still exists.
   Locally, the account and phone are already non-serving.
2. **Cancel billing.** For `owner_request`, cancel the exact current Stripe
   subscription immediately without automatic proration or refund. For
   `subscription_ended`, verify that cancellation is already authoritative.
   A missing or already-canceled subscription satisfies the step.
3. **Drain active call.** Wait without holding a database transaction or
   provider request open. Periodic reconciliation advances the operation after
   the call becomes terminal.
4. **Release number.** Release the exact Telnyx number. Provider confirmation
   that it is already absent or released satisfies the step.
5. **Reset current projections.** Remove the released assignment and obsolete
   provisioning state, clear number-specific activation progress, and preserve
   all historical/customer data defined above.
6. **Complete.** Mark the operation complete and the account `inactive`.

Provider I/O never occurs while holding account, call, subscription, or phone
row locks.

Telnyx support requires a new idempotent provider interface for number release.
The implementation plan must verify the current official Telnyx contract
before selecting an SDK operation and defining exact success/not-found
classification.

Stripe cancellation and Telnyx disablement may finish while an existing call is
running. Telnyx number release cannot occur before call drainage.

## Failure and recovery behavior

The local `deactivating` state remains authoritative during every failure:

- new calls stay blocked;
- stale phone-enable, invoice, provisioning, verification, and go-live events
  cannot reopen service;
- a provider timeout cannot roll back effective deactivation;
- process restart resumes from committed step timestamps.

Retryable timeouts, rate limits, connection failures, and provider
unavailability use bounded exponential backoff and do not exhaust into a false
completion.

Authentication, authorization, provider-contract, or identity-conflict
failures set a bounded operator-attention condition. They do not expose raw
errors to the customer and do not restore service. Customer copy remains
truthful, such as `Finishing account deactivation`.

An operation completes only after every applicable step is proven. Repeated
delivery and duplicate Stripe webhooks are idempotent.

## Concurrency rules

- An account lock and the unique incomplete-operation constraint make repeated
  owner requests and final Stripe cancellation converge.
- The account status commit wins over concurrently claimed routing work.
  Every routing delivery revalidates account status immediately before provider
  enablement.
- A call admitted before the deactivation commit may finish. A call attempting
  admission after the commit is rejected.
- Deactivation and call finalization retain the existing canonical lock order.
  Neither waits on provider I/O while holding the other's database locks.
- Stripe events are still ordered by their provider event watermark, but a
  current `deactivating` status and lifecycle generation prevent an older
  invoice or subscription event from reactivation.
- Checkout is rejected while deactivation is incomplete.
- Provisioning for a new generation cannot begin while the prior number
  release is unresolved.

## Reactivation

An inactive owner sees `Reactivate Opevo`. The action uses the existing Stripe
Checkout boundary, with these additional preconditions:

- account status is `inactive`;
- no incomplete deactivation operation exists;
- the previous number assignment is no longer active;
- the current subscription is replaceable.

A successful, generation-matched new subscription changes the account to
`active`. Central readiness still blocks calls because there is no assigned
number, provisioning consent, forwarding verification, or go-live approval.

The activation snapshot reuses the confirmed business profile and
receptionist configuration and resumes at fresh provisioning consent. Payment
does not itself authorize number ordering. The later flow provisions a new
number, verifies forwarding, and requires explicit go-live approval.

Historical calls, recordings, usage entries, notifications, and canceled
subscription data remain associated with the same account.

## API contract

### `GET /api/account`

Returns:

- account status;
- whether the account is serving;
- safe deactivation progress when applicable;
- whether reactivation is currently allowed;
- a bounded blocker code when it is not.

It never returns provider IDs, retry counts that expose internals, or raw error
details.

### `POST /api/account/deactivate`

- requires the authenticated owner;
- requires the exact confirmation `DEACTIVATE`;
- is rate-limited;
- returns `202` and the current operation for the first or repeated valid
  request;
- returns a validation error for incorrect confirmation;
- returns a controlled conflict only when the account state cannot safely
  enter or resume deactivation.

There is no `DELETE /api/account` in this slice because no account is deleted.

### Existing APIs

Read APIs for calls, call details, recording playback, billing, and account
status remain available to inactive owners.

Profile, agent, activation, provisioning, verification, and go-live mutation
APIs return stable account-state blocker codes while `deactivating` or
`inactive`.

Billing checkout is allowed for `inactive` accounts only when reactivation
preconditions pass. The Billing Portal remains available for appropriate
billing-history and subscription management states.

## Web experience

Add an authenticated Account page with:

- current account state;
- deactivation progress;
- inactive-account explanation and reactivation action;
- an active-account danger zone.

The danger-zone dialog requires exact typed confirmation and presents all
approved consequences before enabling the action.

While deactivating, global dashboard messaging states that Opevo is no longer
accepting new calls and is finishing provider cleanup. Controls that mutate the
profile, receptionist, number, forwarding, or go-live state are disabled and
their server actions still enforce the same boundary.

While inactive:

- historical Calls and Billing navigation remains available;
- recordings remain private and playable through the existing authenticated
  signed-URL boundary;
- configuration is visible where useful but read-only;
- the primary action starts reactivation;
- the old number is never displayed as currently assigned.

The Billing page displays scheduled period-end cancellation and its effective
date without presenting the account as inactive early.

## Audit and observability

The private operation is the durable audit record for:

- requester/trigger;
- lifecycle generation;
- requested, step-completion, and final-completion times;
- safe failure classification;
- operator-attention state.

No customer content, typed confirmation, raw provider payload, secret, or
signed URL belongs in the audit record or logs.

Low-cardinality metrics cover:

- operations by safe state and trigger;
- oldest incomplete operation age;
- retryable step outcomes;
- operator-attention failures;
- completion latency.

Logs use operation IDs and safe step/outcome labels. Alerts cover an unfinished
operation exceeding its expected bound and every operator-attention failure.

## Verification strategy

### Policy and service tests

- account status participates in every readiness and mutation boundary;
- exact confirmation is required;
- owner requests are idempotent;
- inactive historical reads remain authorized;
- inactive mutations and dispatch are denied;
- reactivation preconditions and lifecycle generations are enforced.

### PostgreSQL concurrency tests

- concurrent owner requests create one operation and one reference-only event;
- owner deactivation racing a final Stripe event converges;
- deactivation racing call admission has one authoritative commit order;
- an admitted call finishes before release;
- stale `phone.enable`, invoice, provisioning, verification, and go-live work
  cannot restore service;
- a new checkout/provisioning generation cannot race unresolved old-number
  release.

### Provider and worker tests

- owner deactivation requests immediate non-prorated Stripe cancellation;
- subscription-ended operations do not cancel Stripe twice;
- scheduled period-end cancellation preserves service;
- cancellation reversal preserves service;
- final cancellation starts deactivation;
- Telnyx disable and release handle success, already-applied/not-found,
  timeout, rate limit, authentication failure, and identity conflict safely;
- every committed step survives restart and redelivery;
- provider calls occur without an open business transaction.

### Data-preservation tests

- profile and receptionist configuration remain;
- calls, transcripts, summaries, recordings, notifications, usage history, and
  billing history remain;
- the active phone assignment and number-specific activation progress do not
  remain after release;
- recording playback remains owner-scoped while inactive.

### Web tests

- consequence copy and exact typed confirmation;
- active, deactivating, and inactive states;
- progress and safe failure copy;
- read-only inactive controls;
- scheduled billing cancellation;
- reactivation action and resumed activation milestone.

### Provider-free acceptance

Extend the local fake-provider journey to:

1. activate and assign a fake number;
2. request deactivation;
3. prove immediate new-call blocking;
4. exercise an in-progress-call drainage fixture;
5. restart services during cleanup;
6. prove the account resumes and reaches `inactive`;
7. prove historical data remains;
8. reactivate through local billing;
9. give fresh provisioning consent;
10. receive a different fake number and resume forwarding verification.

All existing API, agent, web, migration, formatting, typing, build, security,
and disposable-browser gates remain required.

## Documentation impact

Implementation updates must keep these sources consistent:

- `docs/PROJECT_STATUS.md`;
- `docs/engineering/2026-07-18-production-readiness-handoff.md`;
- `docs/architecture/local-self-service-activation.md`;
- `docs/architecture/integration-endpoints.md`;
- billing, deployment, and provider runbooks affected by cancellation or
  release behavior.

The documentation must continue to state that permanent account deletion,
automatic retention, cloud deployment, legal approval, and real-provider
certification are not completed by this slice.
