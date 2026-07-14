# Deployment Rollback and Database Forward-Fix Runbook

Use this runbook when a release gate fails or a newly released application
causes a production incident. The default safe action is to roll application
images back by immutable digest while leaving a backward-compatible schema in
place. An irreversible schema or data change is **not** rolled back with an
automatic Alembic downgrade; it requires a reviewed forward-fix or, after an
explicit data-loss decision, recovery into a separately validated database.

## Roles and authority

| Responsibility | Required owner |
| --- | --- |
| Declares rollback/forward-fix and controls traffic | `<incident/release commander>` |
| Classifies schema state and owns database actions | `<data owner>` |
| Rolls API and worker images | `<application owner>` |
| Rolls voice-agent image and call intake | `<voice owner>` |
| Rolls web image | `<web owner>` |
| Assesses tenant/security/privacy impact | `<security/privacy owner>` |
| Approves recovery point and any accepted data loss | `<business/data-loss approver>` |

Open an incident before changing production when tenant isolation, financial
state, recording retention, data integrity, or secret exposure is possible.
Only the incident commander changes traffic or authorizes an emergency
exception. Only the data owner changes schema or data.

## Inputs to capture before action

Copy references, not values, from the release record:

```text
release_id: <opaque release id>
incident_id: <opaque incident id>
first_bad_signal_utc: <timestamp>
last_known_good_utc: <timestamp>
current_api_image: <registry>/presvo-api@sha256:<digest>
current_agent_image: <registry>/presvo-agent@sha256:<digest>
current_web_image: <registry>/presvo-web@sha256:<digest>
previous_api_image: <registry>/presvo-api@sha256:<digest>
previous_agent_image: <registry>/presvo-agent@sha256:<digest>
previous_web_image: <registry>/presvo-web@sha256:<digest>
expected_schema_before: <alembic revision>
expected_schema_after: <alembic revision>
migration_job_id: <job id or not-started>
migration_exit_status: <status or unknown>
pre-release_snapshot_id: <opaque provider id>
latest_pitr_time: <provider timestamp>
```

Reject mutable image tags. Do not print environment variables, secret values,
DSNs, provider payloads, transcripts, phone numbers, recording URLs, or raw
customer identifiers while collecting these inputs.

## Decision tree

```text
Release failure or regression
|
+-- Is tenant isolation, billing, retention, credential, or data integrity at risk?
|   +-- Yes: stop affected writes/call intake, open an incident, preserve evidence.
|   +-- No: hold rollout and continue classification.
|
+-- Did the migration job start?
    +-- No: roll back only the affected application image(s).
    |
    +-- Yes:
        +-- Did it exit 0 at the expected Alembic head?
            +-- No/unknown/partial: do not deploy or restart new application code;
            |   isolate writes and prepare a reviewed database forward-fix.
            |
            +-- Yes:
                +-- Is the new schema backward-compatible with the previous images,
                    with no irreversible data rewrite required by the old code?
                    +-- Yes: roll application images back; keep schema at current head.
                    +-- No or uncertain: stop affected writes/call intake and forward-fix.
```

An application error after a successful additive migration normally follows
the backward-compatible branch. A dropped/renamed column, destructive type
conversion, rewritten business data, deleted row, changed invariant, or
partially applied manual repair follows the irreversible branch. Uncertainty
is treated as irreversible until the data owner proves otherwise.

## Immediate stabilization

Owner: `<incident/release commander>`

1. Stop the active rollout so the orchestrator cannot continue replacing good
   replicas.
2. Record current service digests and healthy/desired replica counts.
3. If integrity is at risk, disable the narrowest write/call entry point that
   prevents further damage. Prefer a provider maintenance route or dispatch
   pause over terminating healthy calls already in progress.
4. Preserve the failed migration/deployment job, redacted logs, metrics, traces,
   and database metadata. Do not enable sensitive logging.
5. Confirm the pre-release snapshot still exists and record the current PITR
   window. Do not restore over the current database.

Provider-neutral command placeholders:

```bash
<deployctl> rollout pause --release <release-id>
<deployctl> service list --release <release-id> --output digests-and-health
<trafficctl> writes-or-call-intake pause --scope <smallest-affected-scope>
<datactl> pitr-window show --database <database-id> --output metadata-only
<datactl> snapshot show --database <database-id> --snapshot <snapshot-id> --output metadata-only
```

Stop and escalate if the current digests do not match the release record, the
schema version cannot be read safely, the recovery window is missing, evidence
collection would expose sensitive content, or the affected scope cannot be
isolated without harming in-progress calls.

Required evidence: incident/release IDs, pause action ID, first bad signal,
current digests/replica counts, current schema revision, protected traffic
scope, backup/PITR metadata, commander, and UTC timestamp.

## Path A — backward-compatible application rollback

Use this path only when the data owner confirms all of the following:

- the migration either did not start or completed at the expected head;
- the previous API and worker digest can operate safely against the current
  schema;
- no irreversible data rewrite is required to restore previous behavior;
- rolling back does not reintroduce a known critical security or integrity bug.

Leave the database at its current Alembic head. Do **not** run `alembic
downgrade`, restore a snapshot, or manually delete newly added schema objects.
Additive columns/tables can remain unused until a later cleanup migration after
all old application versions are retired.

### Roll back the affected services

Roll back the narrowest component first. For a full application rollback,
remove newly exposed frontend behavior, then restore API, worker, and agent
digests one service at a time:

```bash
<deployctl> service deploy presvo-web \
  --image <previous-web-image@sha256:digest> \
  --reason <incident-id> --wait

<deployctl> service deploy presvo-api \
  --image <previous-api-image@sha256:digest> \
  --reason <incident-id> --wait

<deployctl> service deploy presvo-worker \
  --image <previous-api-image@sha256:digest> \
  --reason <incident-id> --wait

<deployctl> service deploy presvo-agent \
  --image <previous-agent-image@sha256:digest> \
  --reason <incident-id> --wait
```

The worker must use the same previous API artifact as the API service. If only
one component changed or failed, do not roll unrelated healthy components.

After every service replacement, verify the orchestrator reports only the
approved previous digest and a stable healthy count before touching the next
service. If the previous image fails against the current schema, stop Path A,
pause affected writes/call intake, and move to Path B.

### Validate the application rollback

```bash
curl --fail --silent --show-error <internal-api-origin>/healthz
curl --fail --silent --show-error <internal-api-origin>/readyz
curl --fail --silent --show-error <public-api-origin>/readyz
curl --fail --silent --show-error <public-web-origin>/
```

Confirm PostgreSQL and Redis readiness, queue age returns toward baseline, no
terminal outbox failures increase, the voice agent has exactly the expected
registration, and one synthetic non-mutating authenticated request succeeds.
If calls were affected, run one short consented staging/beta test call only
after the service and provider signals are stable.

Keep call intake/writes paused until the incident commander and data owner both
accept the validation evidence. Resume the narrow scope gradually and observe
one full alert window.

Required evidence: old/new digest for every rolled service, deployment IDs,
healthy counts, health/readiness outcomes, queue/provider/error snapshots,
synthetic result, schema head left in place, resume action, owners, and UTC
times.

## Path B — irreversible or uncertain schema/data change

Use this path for a failed/partial migration, destructive schema change, data
rewrite, invariant change, accidental deletion, or any case where the previous
application is not proven compatible with current data.

This is a **forward-fix-only** path. Do not run an improvised SQL command,
`alembic downgrade`, or an in-place restore. Downgrade code cannot recreate
discarded values or reverse external provider effects, and an in-place restore
can destroy evidence and otherwise valid writes made after the release.

### Contain and diagnose

1. Keep the smallest unsafe write/call scope paused. Continue read-only service
   only if the data owner confirms reads cannot expose corrupt/cross-tenant data.
2. Take a separate incident-state snapshot before repair. Preserve both it and
   the pre-release snapshot.
3. Query schema revision, constraints, row-count invariants, and migration/job
   metadata using predefined metadata-only diagnostics. Do not dump production
   rows into logs or tickets.
4. Determine whether a code/schema forward migration can preserve all valid
   post-release writes. Prefer this over recovery to an earlier time.
5. Prepare a new immutable API image containing the reviewed repair migration.
   It must pass CI, migration tests from both the pre-release and incident schema
   states, security scans, and a restore-based rehearsal before production use.

```bash
<datactl> snapshot create --database <database-id> --name <incident-id>-before-forward-fix
<datactl> snapshot wait --database <database-id> --name <incident-id>-before-forward-fix --state completed
<datactl> query-metadata --database <database-id> --query-id schema-and-invariants
```

Stop if the diagnosis cannot bound affected tenants/time, the incident snapshot
fails, the repair discards valid post-release data, or the repair has not been
tested against a restored copy of the actual schema state.

### Apply a reviewed forward-fix

The data owner, application owner, security/privacy owner, and incident
commander must approve the repair artifact and evidence. Run it as a one-shot
job from the new API image by immutable digest:

```bash
<deployctl> job run presvo-forward-fix \
  --incident <incident-id> \
  --image <repair-api-image@sha256:digest> \
  --secret-ref <database-secret-reference> \
  -- /app/.venv/bin/alembic -c /app/alembic.ini upgrade head

<deployctl> job wait presvo-forward-fix --incident <incident-id> --expect-exit 0
<datactl> query-metadata --database <database-id> --query-id schema-and-invariants
```

If the job fails or any invariant differs from the reviewed expected result,
keep traffic paused, preserve the new evidence, and prepare another forward
repair. Never loop the same migration blindly.

After a successful repair, deploy only application digests proven compatible
with the repaired head, then run internal/public readiness, queue, provider,
tenant isolation, billing, recording, and finalization checks before gradually
resuming traffic.

Required evidence: diagnosis and affected scope, incident-state snapshot ID,
repair commit/image digest, reviewers, CI/rehearsal references, repair job ID and
exit status, before/after schema revision and invariant results, deployed
compatible images, traffic resume decision, and UTC times.

## Exceptional PITR recovery

PITR is recovery, not a normal deployment rollback. Use it only when a forward
repair cannot preserve integrity or when the business/data-loss approver accepts
the exact loss window and external reconciliation plan.

1. Keep writes and call intake paused.
2. Select a recovery timestamp before the damaging change, accounting for the
   provider's latest-restorable lag.
3. Restore into a **new database**, never over the source.
4. Validate schema, constraints, tenant/accounting invariants, sampled aggregate
   counts, and application compatibility in isolation.
5. Reconcile every external effect after the recovery timestamp: Stripe events,
   Telnyx provisioning, LiveKit calls/recordings, object storage, and durable
   outbox work. Do not replay blindly.
6. Obtain explicit incident commander, data owner, security/privacy owner, and
   business/data-loss approval before changing the application database secret
   reference to the restored target.
7. Retain the original database read-only for the approved investigation period.

```bash
<datactl> pitr restore \
  --source <database-id> \
  --timestamp <approved-utc-timestamp> \
  --target <incident-id>-recovery
<datactl> pitr wait --target <incident-id>-recovery --state available
<datactl> verify-restored-copy --target <incident-id>-recovery --checks <approved-check-set>
```

Stop if the chosen time is outside the recovery window, the restored instance
is not isolated, validation finds an invariant failure, the external
reconciliation set is unknown, or data-loss approval is absent.

Required evidence: chosen timestamp and rationale, declared maximum loss window,
new database ID, validation results, reconciliation inventory, all approvals,
cutover configuration revision, and UTC times.

## Completion criteria

A rollback or repair is complete only when:

- all running services use explicitly approved immutable digests;
- API liveness and PostgreSQL/Redis readiness remain stable for one full alert
  window;
- queue age, terminal failures, provider error rates, recording operations, and
  call reconciliation return to the accepted baseline;
- a synthetic tenant-isolated flow passes without incorrect billing,
  duplication, or sensitive log content;
- traffic/call intake state is explicit and any paused capability has an owner;
- the incident record contains value-free evidence, impact window, root-cause
  owner, follow-up actions, and the current schema revision;
- the pre-release and incident snapshots are retained according to the incident
  evidence policy, then removed by an approved owner when no longer needed.

Do not describe the event as an application rollback if production data was
restored or repaired. Record the precise outcome: `application image rollback`,
`database forward-fix`, or `PITR recovery and cutover`.
