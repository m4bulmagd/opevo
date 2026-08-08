# Controlled Deployment Runbook

Canonical release order: **backup verification → migration job → worker and agent → API → readiness → web → smoke test**.

Do not reorder or parallelize these gates. A release is complete only after the
last gate passes and its value-free evidence has been recorded.

## Worker-isolation coexistence order

This ordered transition applies when moving from the legacy generic worker to
the two explicit services. It is a compatibility sequence within the worker and
agent gate, not authorization to run a production deployment.

1. Start `worker-background` from the new API image.
2. Start `worker-lifecycle` while the generic worker still consumes the default
   queue.
3. Roll out the new API so new wakeups route explicitly.
4. Verify both health keys, depth/oldest-due metrics, and both reconciliation
   jobs.
5. Wait for old API replicas to disappear and the legacy/default backlog to drain.
6. Drain and remove the generic worker.

The services and their checks are fixed: `worker-lifecycle` consumes
`arq:queue`, reports `opevo:worker:call-lifecycle:health`, and defaults to 10
slots; `worker-background` consumes `arq:queue:background`, reports
`opevo:worker:background:health`, and defaults to 4 slots. Inspect
`opevo.worker.queue.depth{queue_class}` and
`opevo.worker.queue.oldest_due.age{queue_class}` for each class. PostgreSQL
outbox/call state is authoritative; Redis is execution and wakeup only.

During this bounded overlap, `worker-lifecycle` can consume and reject a legacy
outbox wakeup from the shared default queue; the old generic worker knows that
legacy function. This unknown-function result is a migration signal: stop the
transition, preserve the normalized function and attempt evidence, restore
compatible routing, and let background reconciliation recover the PostgreSQL
row on schedule. An orphaned lifecycle attempt recovers on call reconciliation
after service restoration. Both are reconciliation-schedule delays, not a
zero-delay guarantee.

## Scope and owners

Use this runbook for staging and production releases of the Opevo application.
It assumes managed PostgreSQL, managed Redis/Valkey, managed object storage, a
secret store, and an orchestrator that can run both services and one-shot jobs.
Replace angle-bracket placeholders with values from the approved change record;
do not paste secrets into commands or evidence.

| Responsibility | Owner for this release |
| --- | --- |
| Release commander; owns go/stop decision | `<release owner>` |
| Database backup, migration, and recovery | `<data owner>` |
| API and worker deployment | `<application owner>` |
| Voice-agent deployment and provider registration | `<voice owner>` |
| Web deployment | `<web owner>` |
| Security/privacy observation | `<security/privacy owner>` |
| Incident/on-call coverage | `<on-call owner>` |

No person should approve their own emergency exception. If a required owner is
unavailable, stop and reschedule unless the incident commander has invoked the
documented emergency process.

## Required release record

Create a value-free change record with these fields before touching the target:

```text
release_id: <opaque release id>
environment: <staging|production>
change_window_utc: <start/end>
release_commander: <name or team>
git_commit: <40-character commit>
api_image: <registry>/opevo-api@sha256:<64 hex characters>
agent_image: <registry>/opevo-agent@sha256:<64 hex characters>
web_image: <registry>/opevo-web@sha256:<64 hex characters>
previous_api_image: <registry>/opevo-api@sha256:<64 hex characters>
previous_agent_image: <registry>/opevo-agent@sha256:<64 hex characters>
previous_web_image: <registry>/opevo-web@sha256:<64 hex characters>
expected_alembic_from: <revision>
expected_alembic_to: <revision>
migration_class: <backward-compatible|maintenance-required>
agent_previous_api_contract_evidence: <CI/staging evidence id>
previous_web_new_api_contract_evidence: <CI/staging evidence id>
other_cross_service_contract_evidence: <evidence id or not-applicable with review>
dashboard_url_or_id: <non-secret reference>
incident_channel: <value-free reference>
stripe_portal_configuration_review: <evidence id>
account_deactivation_alert_review: <evidence id>
```

Images must use immutable repository digests. Tags, including a Git SHA tag,
are useful labels but are not release identities. The one-shot migration and
the worker/API services must use the same API digest. Reject a record that uses
`:latest`, a mutable tag alone, an unverified registry, or a different migration
image.

## Global preconditions and stop rules

Before the change window, the release commander must confirm:

- CI's required aggregate gate passed for the recorded commit and all three
  image digests correspond to that commit.
- Container vulnerability and secret scans passed under the current exception
  policy; no exception expires during the change window.
- The schema change was reviewed and classified. New code can run against the
  old schema during the rollout, and the previous code can run against the new
  schema. For this online runbook, the **previous API must be proven compatible
  with the migrated schema** before Gate 1 begins.
- A migration that is destructive, rewrites data incompatibly, or cannot prove
  old-code/new-schema compatibility is `maintenance-required`. Stop this
  runbook and use a separately reviewed **maintenance procedure** that pauses
  the affected writes and call intake before migration, prevents old replicas
  from serving against the new schema, defines recovery/forward-fix authority,
  and obtains explicit data-owner and release-commander approval.
- Mixed-version contract tests used the exact release commit and the recorded
  previous digests to prove the **new agent revision is compatible with the
  previous API** and the **previous web revision is compatible with the new
  API**. Record both evidence IDs even when those interfaces were unchanged.
- The release owner inventoried every other changed cross-service interface,
  including worker-produced dispatch metadata consumed by an old agent, and
  attached the resulting **cross-service contract evidence**. An interface
  change without N/N-1 evidence makes the release `maintenance-required`.
- If agent/API compatibility is missing, pause call intake through a separately
  approved maintenance procedure before either side changes. If web/API
  compatibility is missing, use an approved maintenance response or a proven
  compatibility bridge so the old web never serves against the incompatible
  API. Do not use an ordinary rolling release to discover compatibility.
- Provider status pages, database capacity, Redis capacity, queue age, API error
  rate, call-finalization latency, and recording failures are normal.
- Production API configuration sets `BILLING_MODE=stripe` and supplies
  `STRIPE_SECRET_KEY` and `STRIPE_BILLING_PORTAL_CONFIGURATION_ID`; the worker
  also receives `STRIPE_SECRET_KEY` whenever its `BILLING_MODE` is `stripe`.
  Secret values must remain in the managed secret store.
- The object referenced by `STRIPE_BILLING_PORTAL_CONFIGURATION_ID` has current
  Stripe-side review evidence proving subscription cancellation is scheduled
  for the paid-period end and proration is disabled. A repository test or
  nonblank ID is not evidence of the external configuration.
- Monitoring pages on every increment of
  `opevo.account_deactivation.attention` and alerts when
  `opevo.account_deactivation.oldest_incomplete_age` exceeds
  `MAX_CALL_DURATION_SECONDS + 900` seconds. Both rules must be active for the
  configured `MAX_CALL_DURATION_SECONDS`, not a copied default.
- There is no active incident, credential rotation, data repair, provider
  maintenance, or overlapping deployment in the environment.
- The previous immutable image digests and current configuration revision are
  available for rollback.
- The last restore drill is within the organization's approved interval and
  has a successful evidence reference.

Stop immediately and open or update an incident if any of these occur:

- tenant isolation, authorization, billing, retention, or recording integrity
  is uncertain;
- the database or queue is already degraded;
- a command would require printing a DSN, token, secret, transcript, phone
  number, recording URL, or provider payload;
- the deployed digest differs from the approved record;
- more than one gate is being changed at once, leaving the failing component
  ambiguous;
- the defined rollback or forward-fix path is no longer available.
- the reviewed Stripe Portal configuration or either account-deactivation alert
  is missing, disabled, points at the wrong environment, or cannot page the
  recorded on-call owner.

## Operator command convention

The vendor target is not approved yet, so the examples use `<deployctl>` as the
approved orchestrator CLI. Translate each command in the later vendor-specific
runbook; do not invent options during a live release.

```bash
export RELEASE_ID=<release-id>
export API_IMAGE=<registry>/opevo-api@sha256:<digest>
export AGENT_IMAGE=<registry>/opevo-agent@sha256:<digest>
export WEB_IMAGE=<registry>/opevo-web@sha256:<digest>
export API_INTERNAL_URL=<non-secret-internal-api-origin>
export API_PUBLIC_URL=<public-api-origin>
export WEB_PUBLIC_URL=<public-web-origin>
```

Do not put credentials in these exports. The orchestrator must resolve secret
references through the managed secret store. Record command exit status,
orchestrator job/deployment ID, start/end UTC times, image digest, and a link to
redacted logs. Do not copy raw logs into the change record.

## Gate 1 — backup verification

Owner: `<data owner>`

Independent checker: `<release commander>`

1. Query the managed PostgreSQL backup console/API without displaying the
   connection string. Confirm the latest successful automated backup is no more
   than 24 hours old and that the latest PITR-restorable timestamp is within the
   provider's documented recovery lag.
2. Confirm retention meets the approved recovery policy and the last restore
   drill evidence is successful.
3. Create a pre-release manual snapshot/backup if the provider supports it.
   Wait for provider state `available`/`completed`; an accepted request is not a
   completed backup.
4. Record the provider's opaque backup/snapshot ID, completion time, current
   database identifier, earliest/latest restorable timestamps, and restore-drill
   evidence reference. Never record a DSN or credential.

Provider-neutral command placeholders:

```bash
<datactl> backup latest --database <database-id> --output metadata-only
<datactl> pitr-window show --database <database-id> --output metadata-only
<datactl> snapshot create --database <database-id> --name "$RELEASE_ID-pre-release"
<datactl> snapshot wait --database <database-id> --name "$RELEASE_ID-pre-release" --state completed
```

Stop if the backup is stale, failed, still pending at the window deadline, the
PITR window cannot be established, the restore drill is overdue/failed, or the
snapshot target is in the wrong region/account. A snapshot creation request ID
alone is insufficient evidence.

Required evidence: `<automated-backup timestamp>`, `<PITR window>`,
`<completed pre-release snapshot ID>`, `<restore-drill evidence ID>`, checker,
and UTC timestamp.

## Gate 2 — migration job

Owner: `<data owner>`

Observer: `<application owner>`

This gate is authorized only for a `backward-compatible` migration. If the
classification is `maintenance-required` or uncertain, do not run it through
this online sequence.

1. Confirm no other migration job or schema repair is active.
2. Run a one-shot task from `$API_IMAGE`; override its normal command with the
   installed Alembic binary. Give it only the database secret reference and the
   minimum network/IAM permissions required for PostgreSQL.
3. Require a zero exit status. Query `alembic_version` through the approved
   metadata-only diagnostic path and confirm exactly the expected head.
4. Preserve the job ID and redacted logs under the release retention policy.

```bash
<deployctl> job run opevo-migrate \
  --release "$RELEASE_ID" \
  --image "$API_IMAGE" \
  --secret-ref <database-secret-reference> \
  -- /app/.venv/bin/alembic -c /app/alembic.ini upgrade head

<deployctl> job wait opevo-migrate --release "$RELEASE_ID" --expect-exit 0
<datactl> query-metadata --database <database-id> --query-id alembic-current-head
```

If the command exits nonzero, times out, loses its lock, reaches an unexpected
revision, or emits an integrity error, **do not start the API** and do not retry
blindly. Stop the release, keep existing services on their previous digests,
and classify the database state with the data owner. Use the rollback runbook;
an irreversible partial migration requires a reviewed forward-fix.

Required evidence: migration job ID, exact API digest, start/end UTC times,
exit status, expected/observed Alembic head, migration classification, and a
redacted log reference.

## Gate 3 — workers and agent

Owners: `<application owner>` and `<voice owner>`

Deploy `worker-background`, then `worker-lifecycle`, from `$API_IMAGE` first.
Keep the legacy generic worker consuming the default queue until the explicit
API routing and drain checks in [Worker-isolation coexistence order](#worker-isolation-coexistence-order)
are complete. For the voice agent, add
the new `$AGENT_IMAGE` revision alongside the old revision and prove it is
registered before draining the old workers. Use one changed service at a time.
Keep the existing API version serving traffic during this gate only because the
preconditions proved that version compatible with the migrated schema.
Before registering the new agent, re-check the recorded new-agent/previous-API
contract evidence and any changed dispatch-metadata evidence. Stop if the
evidence references different image digests or an older API contract.

```bash
<deployctl> service deploy worker-background --image "$API_IMAGE" --release "$RELEASE_ID" --wait
<deployctl> service deploy worker-lifecycle --image "$API_IMAGE" --release "$RELEASE_ID" --wait
<deployctl> service status worker-background --release "$RELEASE_ID"
<deployctl> service status worker-lifecycle --release "$RELEASE_ID"

<deployctl> service deploy-revision opevo-agent --image "$AGENT_IMAGE" --release "$RELEASE_ID" --retain-previous
<voicectl> agent wait-available --release "$RELEASE_ID" --image "$AGENT_IMAGE"
<voicectl> agent drain --revision <previous-agent-revision> --stop-new-dispatches
<voicectl> agent wait --revision <previous-agent-revision> --active-jobs 0 --timeout-seconds 3900
<deployctl> service remove-revision opevo-agent --revision <previous-agent-revision> --wait
```

Confirm both workers start, can reach PostgreSQL and Redis over their protected
endpoints, and do not increase oldest-unfinished outbox age. Confirm the new
agent process health endpoint passes and LiveKit shows the expected agent name
as available. Then make the previous revision **stop accepting new dispatches**
and wait until its **active job count reaches zero** before terminating it. Do
not place a real customer call at this gate.

For a Stripe-mode worker service, startup must fail closed without
`STRIPE_SECRET_KEY`. Confirm the value is supplied by secret reference without
printing it. Observe the account-deactivation operation, oldest-incomplete,
reconciliation-result, attention, and completion-duration metrics after worker
startup; zero attention is expected, but do not manufacture a real provider
deactivation as a deployment probe.

The agent worker sets a 3,900-second drain timeout, covering the current
3,600-second maximum call plus cleanup. Compose grants a 66-minute termination
grace so its stop signal can drain instead of escalating to `SIGKILL`. A managed
orchestrator may impose a shorter termination grace; in that case, the
provider-specific deployment must complete the pre-drain and active-zero gate
before asking the orchestrator to stop the old task. Never treat termination
grace alone as the drain mechanism.

Stop if either worker service or the agent crash-loops, uses an unapproved digest, cannot reach its
dependencies, increases terminal job failures, registers an unexpected agent,
cannot identify old versus new worker revisions, cannot stop new dispatches,
does not drain active jobs before the deadline, or causes provider errors. Keep
the old agent revision running and abort the rollout if safe drain controls are
unavailable; do not terminate a worker with an active call.

Required evidence: both worker/agent deployment IDs, desired/healthy replica counts,
digests, queue-age snapshot, LiveKit registration result, error-rate snapshot,
owners, and UTC times.

## Gate 4 — API

Owner: `<application owner>`

Deploy `$API_IMAGE` by rolling replacement. The orchestrator must retain the
previous healthy replicas until new replicas pass their configured health
checks. API startup must execute Uvicorn directly and must not invoke Alembic.
Before replacing the first API replica, re-check the recorded
previous-web/new-API contract evidence. The previous web remains live through
this gate, so an unproven browser/API contract requires the maintenance path,
not a rolling API deployment.

```bash
<deployctl> service deploy opevo-api --image "$API_IMAGE" --release "$RELEASE_ID" --wait
<deployctl> service status opevo-api --release "$RELEASE_ID"
```

Stop if the deployment exceeds its deadline, any new replica crash-loops, the
orchestrator reports mixed/unapproved digests, liveness fails, or the old replica
set is removed before a new healthy set exists. Restore the previous API digest
using the rollback runbook; do not downgrade the database automatically.

Required evidence: deployment ID, old/new digest, rollout event timestamps,
desired/healthy replica counts, and liveness result.

## Gate 5 — readiness

Owner: `<release commander>`

Checker: `<application owner>`

Probe each new API replica through the internal service path and then through
the load balancer. `GET /readyz` must return success and the fixed contract must
show PostgreSQL and Redis ready. Observe at least one full configured readiness
interval with no replica flapping.

```bash
curl --fail --silent --show-error "$API_INTERNAL_URL/healthz"
curl --fail --silent --show-error "$API_INTERNAL_URL/readyz"
curl --fail --silent --show-error "$API_PUBLIC_URL/healthz"
curl --fail --silent --show-error "$API_PUBLIC_URL/readyz"
```

Stop if either dependency is unready, readiness flaps, latency/error alerts
trigger, or an old API replica is the only successful target. Do not deploy the
frontend while its backend is ineligible for traffic.

Required evidence: internal/external status results, dependency outcomes,
healthy target count, observation window, latency/error snapshot, checker, and
UTC timestamp.

## Gate 6 — web

Owner: `<web owner>`

Deploy `$WEB_IMAGE` by digest after backend eligibility is stable. Confirm the
public runtime configuration points to the approved origins and Clerk
publishable identifier; never record or expose the Clerk secret.

```bash
<deployctl> service deploy opevo-web --image "$WEB_IMAGE" --release "$RELEASE_ID" --wait
<deployctl> service status opevo-web --release "$RELEASE_ID"
curl --fail --silent --show-error "$WEB_PUBLIC_URL/"
```

Stop if the service does not become healthy, serves an unapproved digest,
references a non-production backend, exposes secret configuration, or produces
sustained client/server errors. Roll back only the frontend digest if backend
contracts remain compatible.

Required evidence: deployment ID, old/new digest, healthy replica count, root
response result, build/runtime configuration revision, and UTC timestamp.

## Gate 7 — smoke test

Owners: `<release commander>` and `<product smoke-test owner>`

Use a dedicated synthetic beta account and non-production phone number. Do not
use a real customer's data, transcript, or recording. Execute in this order:

1. Load the web root, sign in, and confirm the authenticated dashboard can read
   the account's current state.
2. Make one non-mutating authenticated API request and confirm tenant isolation.
3. Deliver one provider-signed test webhook using the provider's test mode and
   confirm it is accepted exactly once.
4. Place one short consented test call through the configured beta number.
   Confirm dispatch, agent connection, configured STS or STT→LLM→TTS speech
   path, clean call completion, durable finalization, usage accounting, and the
   expected recording-retention metadata.
5. Confirm outbox age returns to baseline, no terminal failure was created,
   provider and readiness alerts remain clear, and no sensitive content appears
   in application/telemetry logs.

Do not deactivate the synthetic account in this generic smoke test. Immediate
Stripe cancellation and Telnyx number release are real provider mutations and
require a separately approved disposable-number certification procedure. No
such provider certification is claimed by this runbook or the local test suite.

Stop, prevent further rollout, and open an incident for cross-tenant access,
incorrect billing, lost/duplicated finalization, recording-policy violation,
unredacted sensitive data, or an unexplained provider error. For lesser failures,
hold the release and use the rollback decision tree rather than retrying real
calls repeatedly.

Required evidence: synthetic account reference, opaque call/test IDs, each
step's pass/fail and UTC time, dashboard/trace references containing no content,
queue/error snapshots, and both owners' sign-off.

## Account-deactivation observation and recovery

Account deactivation is reversible service shutdown, not deletion. Its account
states are `active -> deactivating -> inactive -> active`. The `deactivating`
commit immediately blocks new-call admission and service-restoring mutations.
An already-admitted call may finish; Telnyx release must wait until it reaches a
terminal state. Owner-triggered cancellation is immediate with no automatic
proration or refund. Stripe Portal subscription-only cancellation remains
active until the paid-period end and starts deactivation only on final
cancellation.

Use only value-free operation evidence. For an incomplete operation, observe:

- the opaque operation ID, trigger, safe operation status, lifecycle generation,
  request time, last reconciliation time, and bounded error code;
- which reconciler-owned phase timestamps are present: routing disabled,
  subscription cancellation verified, active call drained, number released,
  activation reset, and completed;
- the one `account.deactivate` outbox row for aggregate type
  `account-deactivation-operation`, whose aggregate ID and only payload value
  are the same operation ID;
- whether an owner-scoped call is still in a nonterminal state; and
- the five low-cardinality metrics
  `opevo.account_deactivation.operations`,
  `opevo.account_deactivation.oldest_incomplete_age`,
  `opevo.account_deactivation.reconciliation_results`,
  `opevo.account_deactivation.attention`, and
  `opevo.account_deactivation.completion_duration`.

Do not infer provider completion from the request/webhook time. The entry path
leaves routing and subscription phase timestamps unset; only the reconciler
writes a timestamp after provider success or authoritative verification.

Telnyx timeout, rate-limit, connection, and availability failures remain
non-exhausting retries while the account stays non-serving. A successful
release or an exact already-absent/not-found result satisfies release.
Authentication failures commit `telephony_authentication`; release identity
conflicts commit `telephony_release_conflict`; other bounded adapter-contract
failures commit `provider_contract`. These states require operator attention
and never expose raw Telnyx errors or provider identities to the customer.

On every increment of `opevo.account_deactivation.attention`:

1. Open an incident and record the opaque operation ID, trigger, current safe
   phase, bounded code, alert time, and owners. Do not copy raw provider
   payloads, credentials, phone numbers, or customer content.
2. Confirm the account remains `deactivating` and `serving=false`. If it does
   not, stop and escalate as an authorization/routing incident.
3. Remediate the underlying credential, provider-contract, or identity fault
   through the approved secret/provider administration procedure.
4. Requeue **only** the failed reference-only `account.deactivate` outbox event
   for the recorded operation ID through the approved datastore/queue
   administration path. Its payload must remain exactly `operation_id`; do not
   create a second operation, broaden the payload, or replay unrelated failed
   events.
5. Observe reconciler-owned timestamps and verify the durable operation reaches
   `completed`, the account reaches `inactive`, the old phone projection is
   absent, and the attention gauge returns to its expected value before closing
   the incident.

The same recovery boundary applies when
`opevo.account_deactivation.oldest_incomplete_age` exceeds
`MAX_CALL_DURATION_SECONDS + 900`: determine whether a call is legitimately
draining or a provider phase is stalled, remediate the exact fault, and requeue
only the matching failed reference-only event when necessary.

Deactivation preserves identity, confirmed profile/carrier, receptionist
configuration, calls, recordings, notifications, usage, and billing history.
It does not implement or prove export, permanent deletion, retention,
backup/historical-copy erasure, legal approval, cloud deployment, or real
Stripe/Telnyx certification.

## Close or abort the release

On success, record `released`, completion UTC time, all immutable digests,
schema head, and the seven gate evidence references. Continue heightened
monitoring for `<approved observation period>` and retain the previous digests
until that period ends.

On any stop condition, record `aborted` or `rolled_back`, the last completed
gate, the first failing signal, decision owner, incident reference, and whether
the resolution was an application rollback or a database forward-fix. Never
mark a release complete because the deploy command returned zero; all seven
gates must have passed.
