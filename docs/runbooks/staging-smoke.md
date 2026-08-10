# Real-Provider Staging Certification Runbook

Use this runbook to certify Opevo against a deployed, disposable staging target
with real provider integrations. It does not authorize a production deployment,
a customer-data test, or an unreviewed phone-number purchase.

`compose.dev.yaml` is deliberately provider-free and cannot execute this
procedure. Use the approved staging service topology and keep run results in an
access-controlled release/operations record rather than this repository.

## Scope

This procedure verifies one complete synthetic-owner journey across:

- the selected Clerk or Supabase identity flow and local-user provisioning;
- Stripe test-mode eligibility and hosted billing;
- explicit Telnyx number-provisioning consent and assignment;
- conditional forwarding verification and explicit go-live;
- LiveKit dispatch and the configured speech/model pipeline;
- durable call, transcript, summary, recording, notification, and usage state;
- owner-scoped call-history access and asynchronous terminal-call removal; and
- API, worker, queue, provider, and recording health signals.

Subscription cancellation, account deactivation, number release, recovery
drills, load tests, and provider-outage tests mutate broader state. Run them only
through separately approved certification records with their own stop rules.

## Authorization and prerequisites

Before the window, record value-free references for:

- the staging environment and deployed 40-character Git commit;
- immutable API, web, agent, and worker image digests;
- release commander, provider owner, test owner, and on-call owner;
- one disposable identity in the selected auth provider and one Stripe test customer;
- one approved disposable existing French line and authority to order/release
  one Opevo staging number;
- the LiveKit project, SIP trunk, Telnyx active/disabled connections, private
  object-storage bucket, and telemetry dashboard; and
- cleanup ownership, cost limit, stop conditions, and evidence retention.

Do not record tokens, credentials, email addresses, phone numbers, provider
payloads, transcripts, prompts, recording URLs, or raw logs in the change
record. Use opaque test references and redacted provider-console links.

The deployed target must use the production-equivalent modes below:

```text
AUTH_PROVIDER=<clerk-or-supabase>
BILLING_MODE=stripe
CARRIER_LOOKUP_MODE=telnyx
TELEPHONY_MODE=telnyx
ACTIVATION_FLOW_ENABLED=true
TELNYX_ORDERING_ENABLED=false
```

Confirm through deployment metadata—not by printing environment variables—that
the API, `worker-lifecycle`, `worker-background`, web, and agent have their
minimum secret references. `TELNYX_ORDERING_ENABLED` is a process-wide startup
setting, not a per-owner authorization control. Phone ordering must remain
disabled until the release commander opens the bounded provisioning gate below.

## Operator convention

Use non-secret origins and an ephemeral session token from the selected provider
in a protected shell. Do not paste the token into evidence or command output.

```bash
export STAGING_API_URL=https://<staging-api-origin>
export STAGING_WEB_URL=https://<staging-web-origin>
export AUTH_SESSION_TOKEN=<ephemeral-disposable-owner-token>
```

Replace the placeholders locally. The commands below use `--fail` so an HTTP
error stops the step instead of being mistaken for a successful response.

## Gate 1 — service eligibility

Confirm the deployed commit/digests match the approved record. Then probe the
public API:

```bash
curl --fail --silent --show-error "$STAGING_API_URL/healthz"
curl --fail --silent --show-error "$STAGING_API_URL/readyz"
```

Require API liveness, PostgreSQL readiness, Redis readiness, both worker health
keys, and an available agent registration. Stop for a digest mismatch,
readiness failure, queue backlog, provider incident, missing alert route, or
recording/storage degradation.

## Gate 2 — identity and profile

Create the disposable owner through the selected provider. For Clerk, require a
real signed `user.created` webhook to reach staging. For Supabase, require the
first verified session to exercise lazy local provisioning. Sign in through the
web application and complete the Business and Receptionist milestones using
synthetic, non-sensitive content.

Verify through the authenticated product/API that:

- the local owner exists exactly once;
- the business profile and receptionist revision persist after reload; and
- the activation snapshot requires billing or provisioning consent rather than
  skipping directly to a phone assignment.

Do not insert users, profiles, or activation state directly in PostgreSQL.

## Gate 3 — Stripe eligibility before phone consent

Start Checkout through the product and complete it in Stripe test mode. Confirm
the signed webhook creates/updates the starter subscription and grants minutes
exactly once.

Before provisioning consent, require all of the following:

- the activation snapshot reports `provisioning_consent_required`;
- no staging phone number is assigned to the owner;
- no `phone.provision` work exists for the owner; and
- replaying the same Stripe event does not grant minutes twice.

Payment eligibility is not authorization to order a phone number. Stop if a
Stripe event alone selects, orders, or assigns one.

## Gate 4 — explicit Telnyx provisioning

Use an exclusive staging window. Before changing configuration, prove no other
owner is eligible to provision and no other owner has pending or processing
`phone.provision` work. Pause new staging-owner admission for the window. A
user-interface consent check alone is not sufficient isolation.

The release commander confirms the disposable-number cost limit, approves a
configuration revision with `TELNYX_ORDERING_ENABLED=true`, and has the
application owner redeploy `worker-background` with that revision. Record its
new immutable deployment ID and require a healthy worker plus an empty
unrelated provisioning queue before continuing. Other environments and worker
revisions must retain ordering disabled.

In the disposable owner's Number milestone, review the consequences and submit
explicit provisioning consent once.

Observe the activation snapshot move from queued/running to succeeded while
`worker-background` processes reference-only `phone.provision` work. Confirm in
the Telnyx console that exactly one approved French number is assigned to the
disabled connection until go-live. A repeated consent/request must not order a
second number.

As soon as the bounded operation reaches a persisted success, review-required,
or failed outcome, restore `TELNYX_ORDERING_ENABLED=false` and redeploy
`worker-background`; verify its deployment ID, health, and queue state. Do this
before continuing to forwarding or call testing. On an unexpected country/type,
duplicate order, price above the approved limit, ownership mismatch,
authentication failure, or unresolved provider outcome, stop immediately,
restore the disabled configuration, and reconcile the provider and local
operation before resuming.

## Gate 5 — conditional forwarding verification and go-live

Use the product's carrier guide for the synthetic existing line. Configure only
unanswered, busy, and unreachable conditional forwarding; unconditional
forwarding is outside the supported launch path.

Open the timed verification window and place the approved forwarded test call.
Require the current verification session to complete once and survive a normal
page reload. Review the readiness snapshot, then submit explicit go-live.

Confirm the assigned Opevo number moves from the disabled to active Telnyx
connection only after verified forwarding and go-live approval. Stop if direct
agent-config mutation can bypass the activation gate.

## Gate 6 — real call and durable state

Place one short consented inbound call through the synthetic business line.
Use neutral phrases that contain no personal or confidential information.

Require:

- LiveKit accepts the SIP participant and the durable dispatch intent;
- the agent joins with the configured launch pipeline;
- final transcript segments are stored in order;
- `worker-lifecycle` completes finalization exactly once;
- usage debit and the local completion notification commit with finalization;
- summary work and recording reconciliation run on `worker-background`;
- the call becomes terminal and appears only for the owning account; and
- queue age, provider errors, readiness, and recording signals return to their
  expected baseline.

An accepted webhook or queue wakeup alone is not success. Verify the durable
PostgreSQL state and bounded provider/worker outcomes.

## Gate 7 — history, playback, and removal

Through the authenticated product or current OpenAPI contract:

1. List calls with pagination and at least one current filter.
2. Open the synthetic call detail and confirm ordered transcript plus structured
   summary state.
3. Confirm a fresh signed recording URL is available only to the owner when the
   recording succeeded.
4. Remove the terminal call once, then repeat the request.

Both removals must return the idempotent success contract. The call must
immediately disappear from owner list/detail access; asynchronous provider stop
and exact-object deletion must continue without re-exposing it. Do not claim
backup or historical-copy erasure.

## Completion and cleanup

Certification passes only when all seven gates have value-free evidence and no
stop condition occurred. Record the commit/digests, gate outcomes, opaque test
references, UTC times, dashboard links, cleanup owner, and approvals outside
Git. Update `docs/PROJECT_STATUS.md` only when supported capability or
production-readiness status changes.

After the observation window:

- do not release the number directly in the Telnyx console; execute a separately
  approved account-deactivation and reconciliation cleanup through the
  application, then verify terminal local owner state, completed local release
  state, and provider release before closing certification;
- remove or deactivate the disposable provider identities according to policy;
- delete active-storage test audio through the supported owner/provider path;
- confirm queues and alerts return to baseline; and
- close the controlled record with cleanup evidence.

Never append run output, historical prices, phone fragments, seeded identities,
or provider screenshots to this runbook.
