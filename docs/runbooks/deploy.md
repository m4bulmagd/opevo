# Controlled Deployment Runbook

Canonical release order: **backup verification → migration job → worker and agent → API → readiness → web → smoke test**.

Do not reorder or parallelize these gates. A release is complete only after the
last gate passes and its value-free evidence has been recorded.

## Scope and owners

Use this runbook for staging and production releases of the Presvo application.
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
api_image: <registry>/presvo-api@sha256:<64 hex characters>
agent_image: <registry>/presvo-agent@sha256:<64 hex characters>
web_image: <registry>/presvo-web@sha256:<64 hex characters>
previous_api_image: <registry>/presvo-api@sha256:<64 hex characters>
previous_agent_image: <registry>/presvo-agent@sha256:<64 hex characters>
previous_web_image: <registry>/presvo-web@sha256:<64 hex characters>
expected_alembic_from: <revision>
expected_alembic_to: <revision>
migration_class: <backward-compatible|maintenance-required>
agent_previous_api_contract_evidence: <CI/staging evidence id>
previous_web_new_api_contract_evidence: <CI/staging evidence id>
other_cross_service_contract_evidence: <evidence id or not-applicable with review>
dashboard_url_or_id: <non-secret reference>
incident_channel: <value-free reference>
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

## Operator command convention

The vendor target is not approved yet, so the examples use `<deployctl>` as the
approved orchestrator CLI. Translate each command in the later vendor-specific
runbook; do not invent options during a live release.

```bash
export RELEASE_ID=<release-id>
export API_IMAGE=<registry>/presvo-api@sha256:<digest>
export AGENT_IMAGE=<registry>/presvo-agent@sha256:<digest>
export WEB_IMAGE=<registry>/presvo-web@sha256:<digest>
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
<deployctl> job run presvo-migrate \
  --release "$RELEASE_ID" \
  --image "$API_IMAGE" \
  --secret-ref <database-secret-reference> \
  -- /app/.venv/bin/alembic -c /app/alembic.ini upgrade head

<deployctl> job wait presvo-migrate --release "$RELEASE_ID" --expect-exit 0
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

## Gate 3 — worker and agent

Owners: `<application owner>` and `<voice owner>`

Deploy the post-call worker from `$API_IMAGE` first. For the voice agent, add
the new `$AGENT_IMAGE` revision alongside the old revision and prove it is
registered before draining the old workers. Use one changed service at a time.
Keep the existing API version serving traffic during this gate only because the
preconditions proved that version compatible with the migrated schema.
Before registering the new agent, re-check the recorded new-agent/previous-API
contract evidence and any changed dispatch-metadata evidence. Stop if the
evidence references different image digests or an older API contract.

```bash
<deployctl> service deploy presvo-worker --image "$API_IMAGE" --release "$RELEASE_ID" --wait
<deployctl> service status presvo-worker --release "$RELEASE_ID"

<deployctl> service deploy-revision presvo-agent --image "$AGENT_IMAGE" --release "$RELEASE_ID" --retain-previous
<voicectl> agent wait-available --release "$RELEASE_ID" --image "$AGENT_IMAGE"
<voicectl> agent drain --revision <previous-agent-revision> --stop-new-dispatches
<voicectl> agent wait --revision <previous-agent-revision> --active-jobs 0 --timeout-seconds 3900
<deployctl> service remove-revision presvo-agent --revision <previous-agent-revision> --wait
```

Confirm the worker starts, can reach PostgreSQL and Redis over their protected
endpoints, and does not increase oldest-unfinished outbox age. Confirm the new
agent process health endpoint passes and LiveKit shows the expected agent name
as available. Then make the previous revision **stop accepting new dispatches**
and wait until its **active job count reaches zero** before terminating it. Do
not place a real customer call at this gate.

The agent worker sets a 3,900-second drain timeout, covering the current
3,600-second maximum call plus cleanup. Compose grants a 66-minute termination
grace so its stop signal can drain instead of escalating to `SIGKILL`. A managed
orchestrator may impose a shorter termination grace; in that case, the
provider-specific deployment must complete the pre-drain and active-zero gate
before asking the orchestrator to stop the old task. Never treat termination
grace alone as the drain mechanism.

Stop if either service crash-loops, uses an unapproved digest, cannot reach its
dependencies, increases terminal job failures, registers an unexpected agent,
cannot identify old versus new worker revisions, cannot stop new dispatches,
does not drain active jobs before the deadline, or causes provider errors. Keep
the old agent revision running and abort the rollout if safe drain controls are
unavailable; do not terminate a worker with an active call.

Required evidence: worker/agent deployment IDs, desired/healthy replica counts,
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
<deployctl> service deploy presvo-api --image "$API_IMAGE" --release "$RELEASE_ID" --wait
<deployctl> service status presvo-api --release "$RELEASE_ID"
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
<deployctl> service deploy presvo-web --image "$WEB_IMAGE" --release "$RELEASE_ID" --wait
<deployctl> service status presvo-web --release "$RELEASE_ID"
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

Stop, prevent further rollout, and open an incident for cross-tenant access,
incorrect billing, lost/duplicated finalization, recording-policy violation,
unredacted sensitive data, or an unexplained provider error. For lesser failures,
hold the release and use the rollback decision tree rather than retrying real
calls repeatedly.

Required evidence: synthetic account reference, opaque call/test IDs, each
step's pass/fail and UTC time, dashboard/trace references containing no content,
queue/error snapshots, and both owners' sign-off.

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
